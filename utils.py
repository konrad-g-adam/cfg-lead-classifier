"""
utils.py — File I/O helpers: CSV / Excel / JSON reading and writing.

Handles:
  - Reading CSV from a local path
  - Reading a Google Sheets link (public or shareable) as CSV
  - Writing classified results to separate CSVs, a combined XLSX, and a
    JSON summary report.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs

import pandas as pd

from config import CSV_READ_ENCODING, DEFAULT_ENCODING

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
# Input helpers
# ════════════════════════════════════════════════════════════════════════

def read_input_file(path: str | Path, encoding: str = CSV_READ_ENCODING) -> pd.DataFrame:
    """Read a CSV or Excel file into a DataFrame, auto-detecting format.

    Handles:
      - True CSV files (UTF-8, Latin-1, CP-1252 fallback)
      - Excel files (.xlsx/.xls) even if misnamed as .csv
      - Files with BOM markers
    """
    path = Path(path)
    logger.info("Reading input file: %s", path)

    # Check if file is actually Excel (ZIP-based) regardless of extension
    is_excel = False
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic == b"PK\x03\x04":  # ZIP magic bytes = Excel .xlsx
            is_excel = True
        elif magic[:2] == b"\xd0\xcf":  # OLE2 magic = old .xls format
            is_excel = True

    if is_excel or path.suffix.lower() in (".xlsx", ".xls"):
        logger.info("Detected Excel format, reading with openpyxl/xlrd")
        try:
            df = pd.read_excel(path, engine="openpyxl")
        except Exception:
            df = pd.read_excel(path)
        logger.info("Loaded %d rows x %d columns from Excel", len(df), len(df.columns))
        return df

    # CSV with encoding fallback chain
    for enc in [encoding, "utf-8-sig", "latin-1", "cp1252"]:
        try:
            df = pd.read_csv(
                path,
                encoding=enc,
                on_bad_lines="skip",
                engine="python",
            )
            logger.info("Loaded %d rows x %d columns (encoding: %s)", len(df), len(df.columns), enc)
            return df
        except (UnicodeDecodeError, UnicodeError):
            logger.warning("Encoding %s failed, trying next...", enc)
            continue

    raise ValueError(f"Could not read {path.name} with any supported encoding (tried: {encoding}, utf-8-sig, latin-1, cp1252)")


# Keep backward compatibility
def read_csv(path: str | Path, encoding: str = CSV_READ_ENCODING) -> pd.DataFrame:
    """Backward-compatible wrapper — now delegates to read_input_file."""
    return read_input_file(path, encoding)


# ════════════════════════════════════════════════════════════════════════
# Column normalization — auto-detect source and map to standard schema
# ════════════════════════════════════════════════════════════════════════

# Standard internal columns the scorer expects
_STANDARD_COLS = {"firstName", "lastName", "title", "summary", "companyName"}

# Known schema signatures and their mappings
_SCHEMA_SIGNATURES = [
    {
        "name": "Phantombuster SalesNav Export",
        "detect": {"firstName", "lastName", "companyName"},
        "mapping": {},  # already standard
    },
    {
        "name": "AnyMailFinder + Google Maps",
        "detect": {"person_name", "result_title", "placeUrl"},
        "mapping": {
            "person_name": "_full_name",       # will be split into firstName/lastName
        },
        # Special post-mapping: title (GMaps biz) -> companyName, result_title -> title (job)
        "post_mapping": [
            ("title", "companyName"),           # GMaps business name -> companyName
            ("result_title", "title"),          # CEO/Owner -> job title for scorer
        ],
    },
    {
        "name": "AnyMailFinder + LinkedIn",
        "detect": {"person_name", "result_title", "linkedin_url"},
        "mapping": {
            "person_name": "_full_name",
            "result_title": "title",
        },
    },
    {
        "name": "Generic with full name",
        "detect": {"person_name"},
        "mapping": {
            "person_name": "_full_name",
        },
    },
    {
        "name": "Generic with name column",
        "detect": {"name"},
        "mapping": {
            "name": "_full_name",
        },
    },
    {
        "name": "Generic with fullName",
        "detect": {"fullName"},
        "mapping": {
            "fullName": "_full_name",
        },
    },
]


def _split_full_name(full_name: str) -> tuple:
    """Split a full name into (firstName, lastName).

    Handles:
      - 'Jan Kowalski' -> ('Jan', 'Kowalski')
      - 'Anna Maria Nowak' -> ('Anna', 'Maria Nowak')
      - 'Kowalski' -> ('', 'Kowalski')
      - '' / NaN -> ('', '')
    """
    if not full_name or not isinstance(full_name, str) or pd.isna(full_name):
        return ("", "")
    parts = full_name.strip().split()
    if len(parts) == 0:
        return ("", "")
    elif len(parts) == 1:
        return ("", parts[0])
    else:
        return (parts[0], " ".join(parts[1:]))


def normalize_columns(df: pd.DataFrame) -> tuple:
    """Auto-detect data source schema and normalize columns for the scorer.

    Returns:
        (normalized_df, detected_schema_name)

    The scorer expects: firstName, lastName, title (job), summary, companyName.
    This function maps any known schema to that standard, preserving all
    original columns for output.
    """
    cols = set(df.columns)

    # Check if already standard
    if _STANDARD_COLS.issubset(cols):
        logger.info("Schema detected: Phantombuster SalesNav Export (standard)")
        return df, "Phantombuster SalesNav Export"

    # Try each known schema
    detected = None
    mapping = {}
    for schema in _SCHEMA_SIGNATURES:
        if schema["detect"].issubset(cols):
            detected = schema["name"]
            mapping = schema["mapping"]
            break

    if not detected:
        # Last resort: try to find any column containing "name"
        name_cols = [c for c in df.columns if "name" in c.lower()]
        if name_cols:
            detected = f"Unknown (best-guess from '{name_cols[0]}')"
            mapping = {name_cols[0]: "_full_name"}
            logger.warning("No known schema detected. Guessing name column: %s", name_cols[0])
        else:
            detected = "Unknown (no name column found)"
            logger.warning("Cannot detect schema — no name-like column found. Scoring may be inaccurate.")
            return df, detected

    logger.info("Schema detected: %s", detected)
    logger.info("Column mapping: %s", mapping)

    # Apply renaming — but preserve originals by copying, not renaming
    df = df.copy()

    # Apply post_mapping (order-sensitive column swaps like title<->result_title)
    for schema in _SCHEMA_SIGNATURES:
        if schema["name"] == detected:
            for src_col, dst_col in schema.get("post_mapping", []):
                if src_col in df.columns:
                    df[f"_orig_{src_col}"] = df[src_col]
            # Now apply assignments using originals
            for src_col, dst_col in schema.get("post_mapping", []):
                orig_key = f"_orig_{src_col}"
                if orig_key in df.columns:
                    df[dst_col] = df[orig_key]
            break

    # Rename mapped columns (create scorer-friendly columns)
    for src_col, dst_col in mapping.items():
        if src_col in df.columns:
            if dst_col in df.columns and dst_col != src_col:
                df[f"_orig_{dst_col}"] = df[dst_col]
            df[dst_col] = df[src_col]

    # Split full name into firstName / lastName if _full_name was created
    if "_full_name" in df.columns:
        split = df["_full_name"].apply(_split_full_name)
        if "firstName" not in df.columns:
            df["firstName"] = split.apply(lambda x: x[0])
        if "lastName" not in df.columns:
            df["lastName"] = split.apply(lambda x: x[1])

    # Map common alternative column names to standard names
    alt_mappings = {
        "title": ["_job_title", "job_title", "jobTitle", "position", "role", "result_title"],
        "summary": ["bio", "about", "description", "headline"],
        "companyName": ["company", "company_name", "organization", "employer"],
    }
    for standard, alternatives in alt_mappings.items():
        if standard not in df.columns or df[standard].isna().all():
            for alt in alternatives:
                if alt in df.columns and not df[alt].isna().all():
                    df[standard] = df[alt]
                    logger.info("Mapped '%s' -> '%s'", alt, standard)
                    break

    # Ensure all standard columns exist (empty string if missing)
    for col in _STANDARD_COLS:
        if col not in df.columns:
            df[col] = ""

    return df, detected


def google_sheet_to_csv_url(sheet_url: str) -> str:
    """Convert a Google Sheets URL to its CSV export URL.

    Supports formats:
      https://docs.google.com/spreadsheets/d/SHEET_ID/edit?gid=GID#gid=GID
      https://docs.google.com/spreadsheets/d/SHEET_ID/edit#gid=GID
    """
    # Extract sheet ID
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", sheet_url)
    if not match:
        raise ValueError(f"Cannot parse Google Sheets ID from URL: {sheet_url}")
    sheet_id = match.group(1)

    # Extract GID (tab identifier)
    gid = "0"
    gid_match = re.search(r"[?&#]gid=(\d+)", sheet_url)
    if gid_match:
        gid = gid_match.group(1)

    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={gid}"
    )
    return csv_url


def read_google_sheet(sheet_url: str) -> pd.DataFrame:
    """Download a Google Sheet (must be publicly shared) as a DataFrame."""
    csv_url = google_sheet_to_csv_url(sheet_url)
    logger.info("Fetching Google Sheet as CSV: %s", csv_url)
    df = pd.read_csv(csv_url)
    logger.info("Loaded %d rows x %d columns from Google Sheet", len(df), len(df.columns))
    return df


def read_input(source: str) -> pd.DataFrame:
    """Unified reader: auto-detects Google Sheets URLs vs. local file paths."""
    if source.startswith("https://docs.google.com/spreadsheets"):
        return read_google_sheet(source)
    return read_csv(source)


# ════════════════════════════════════════════════════════════════════════
# Output helpers
# ════════════════════════════════════════════════════════════════════════

def _clean_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Drop internal scoring columns (prefixed with ``_``)."""
    return df[[c for c in df.columns if not c.startswith("_")]]


def write_csvs(
    df: pd.DataFrame,
    output_dir: str | Path,
    prefix: str = "PhB_SalesNav",
    encoding: str = DEFAULT_ENCODING,
) -> Dict[str, Path]:
    """Write three classification CSVs + one audit CSV.

    Returns a dict mapping label → file path.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: Dict[str, Path] = {}

    for label, suffix in [("PL", "Polish_Speakers"), ("EN", "Foreign_EN"), ("UNCERTAIN", "Uncertain")]:
        subset = df[df["_classification"] == label]
        path = out / f"{prefix}_{suffix}.csv"
        _clean_cols(subset).to_csv(path, index=False, encoding=encoding)
        files[label] = path
        logger.info("Wrote %d rows → %s", len(subset), path)

    # Audit file (includes scoring columns)
    audit_path = out / f"{prefix}_Classification_Audit.csv"
    df.to_csv(audit_path, index=False, encoding=encoding)
    files["AUDIT"] = audit_path
    logger.info("Wrote %d rows → %s (audit)", len(df), audit_path)

    return files


def write_excel(
    df: pd.DataFrame,
    output_dir: str | Path,
    prefix: str = "PhB_SalesNav",
) -> Path:
    """Write a combined Excel workbook with per-category sheets + summary."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    xlsx_path = out / f"{prefix}_Classified.xlsx"

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for label, sheet in [("PL", "Polish_PL"), ("EN", "Foreign_EN"), ("UNCERTAIN", "Uncertain")]:
            subset = df[df["_classification"] == label]
            _clean_cols(subset).to_excel(writer, sheet_name=sheet, index=False)

        # Summary sheet
        total = len(df)
        counts = df["_classification"].value_counts()
        summary = pd.DataFrame({
            "Category": ["Polish (PL)", "Foreign (EN)", "Uncertain", "TOTAL"],
            "Count": [
                counts.get("PL", 0),
                counts.get("EN", 0),
                counts.get("UNCERTAIN", 0),
                total,
            ],
            "Percentage": [
                f"{counts.get('PL', 0) / total * 100:.1f}%",
                f"{counts.get('EN', 0) / total * 100:.1f}%",
                f"{counts.get('UNCERTAIN', 0) / total * 100:.1f}%",
                "100.0%",
            ],
        })
        summary.to_excel(writer, sheet_name="Summary", index=False)

        # Full audit sheet
        df.to_excel(writer, sheet_name="Full_Audit", index=False)

    logger.info("Wrote Excel workbook → %s", xlsx_path)
    return xlsx_path


def write_json_report(
    df: pd.DataFrame,
    output_dir: str | Path,
    prefix: str = "PhB_SalesNav",
    source_file: str = "",
) -> Path:
    """Write a JSON summary report with classification statistics."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{prefix}_Report.json"

    total = len(df)
    counts = df["_classification"].value_counts()
    score_stats = df["_classification_score"].describe().to_dict()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(source_file),
        "total_profiles": total,
        "classification": {
            "PL": {
                "count": int(counts.get("PL", 0)),
                "pct": round(counts.get("PL", 0) / total * 100, 1),
            },
            "EN": {
                "count": int(counts.get("EN", 0)),
                "pct": round(counts.get("EN", 0) / total * 100, 1),
            },
            "UNCERTAIN": {
                "count": int(counts.get("UNCERTAIN", 0)),
                "pct": round(counts.get("UNCERTAIN", 0) / total * 100, 1),
            },
        },
        "score_statistics": {
            "mean": round(score_stats.get("mean", 0), 2),
            "std": round(score_stats.get("std", 0), 2),
            "min": int(score_stats.get("min", 0)),
            "max": int(score_stats.get("max", 0)),
            "median": round(score_stats.get("50%", 0), 1),
        },
    }

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    logger.info("Wrote JSON report → %s", json_path)
    return json_path
