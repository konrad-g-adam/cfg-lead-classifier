"""
scoring.py — Eight-signal scoring engine for lead nationality classification.

Each profile is evaluated across eight independent signals.  Every signal
that fires adds (or subtracts) a weighted score.  The sum determines the
final classification bucket (NATIVE / FOREIGN / UNCERTAIN).

Signals
-------
1. Polish surname suffix      (-ski, -wicz, -czyk …)
2. Surname in common-surname DB
3. Polish diacritics in last name
4. Polish diacritics in first name
5. First name in strong / regular Polish DB
6. First name in foreign-name DB (negative signal)
7. Job-title language (keyword match + langdetect)
8. Summary / bio language (langdetect)
9. Company-name keywords (weak positive signal)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pandas as pd

from config import (
    SIGNAL_COMPANY_KEYWORDS,
    SIGNAL_DIACRITICS_FIRST,
    SIGNAL_DIACRITICS_LAST,
    SIGNAL_FIRST_NAME,
    SIGNAL_FOREIGN_FIRST_NAME,
    SIGNAL_STRONG_FIRST_NAME,
    SIGNAL_SURNAME_DB,
    SIGNAL_SURNAME_SUFFIX,
    SIGNAL_SUMMARY_LANGDETECT,
    SIGNAL_TITLE_KEYWORDS,
    SIGNAL_TITLE_LANGDETECT,
    LanguageProfile,
)
from language_detect import detect_language

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
# Name-database loaders
# ════════════════════════════════════════════════════════════════════════

def _db_dir() -> Path:
    """Return the path to the name_databases/ folder next to this file."""
    return Path(__file__).resolve().parent / "name_databases"


def load_first_names(filepath: Path | None = None) -> Tuple[Set[str], Set[str]]:
    """Load Polish first names from a structured text file.

    Returns
    -------
    (strong_names, regular_names)
        Two sets of lowercase first names.
    """
    path = filepath or _db_dir() / "polish_first_names.txt"
    strong: set[str] = set()
    regular: set[str] = set()
    current_section = "STRONG"

    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                if "## STRONG" in line:
                    current_section = "STRONG"
                elif "## REGULAR" in line:
                    current_section = "REGULAR"
                continue
            name = line.lower().strip()
            if current_section == "STRONG":
                strong.add(name)
            else:
                regular.add(name)

    logger.info("Loaded %d strong + %d regular Polish first names", len(strong), len(regular))
    return strong, regular


def load_foreign_names(filepath: Path | None = None) -> Set[str]:
    """Load clearly-foreign first names from a text file."""
    path = filepath or _db_dir() / "foreign_names.txt"
    names: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            names.add(line.lower())
    logger.info("Loaded %d foreign first names", len(names))
    return names


def load_surname_patterns(filepath: Path | None = None) -> Dict[str, Any]:
    """Load surname suffixes, common surnames, and keyword lists from JSON."""
    path = filepath or _db_dir() / "polish_surname_patterns.json"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    logger.info(
        "Loaded surname patterns: %d unicode suffixes, %d ascii suffixes, "
        "%d common surnames, %d title keywords, %d company keywords",
        len(data.get("suffixes_unicode", [])),
        len(data.get("suffixes_ascii", [])),
        len(data.get("common_surnames", [])),
        len(data.get("title_keywords_pl", [])),
        len(data.get("company_keywords_pl", [])),
    )
    return data


# ════════════════════════════════════════════════════════════════════════
# Text-normalisation helpers
# ════════════════════════════════════════════════════════════════════════

_EMOJI_RE = re.compile(
    r"[^\w\s\-.()"
    r"àáâãäåæçèéêëìíîïðñòóôõöùúûüýþÿ"
    r"ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
    r"]"
)

_TITLE_STRIP_RE = re.compile(
    r",?\s*(phd|mba|ll\.?m\.?|msc|bsc|eng\.?|dr|prof|cpa|cfa|pmp|acc)\b.*$",
    re.IGNORECASE,
)

_LEADING_TITLE_RE = re.compile(r"^(dr|prof|ing|mgr|inż)\s+", re.IGNORECASE)


def normalize_name(name: Any) -> str:
    """Lowercase, strip emojis, academic titles, and whitespace."""
    if pd.isna(name):
        return ""
    s = str(name).strip().lower()
    s = _EMOJI_RE.sub("", s)
    s = _TITLE_STRIP_RE.sub("", s)
    s = _LEADING_TITLE_RE.sub("", s)
    return s.strip()


def split_first_name(first_name: str) -> List[str]:
    """Split a multi-word first name into individual parts.

    Handles formats like ``"Bartlomiej W."``, ``"Krzysztof Grzegorz"``,
    and ``"Kuba (Jakub)"``.
    """
    cleaned = re.sub(r"[()]", " ", first_name)
    parts = re.split(r"[\s.]+", cleaned)
    return [p.strip().lower() for p in parts if len(p.strip()) > 1]


# ════════════════════════════════════════════════════════════════════════
# Scorer class
# ════════════════════════════════════════════════════════════════════════

class ProfileScorer:
    """Stateful scorer that holds loaded databases and a language profile.

    Instantiate once, then call :meth:`score` for each profile row.
    """

    def __init__(self, profile: LanguageProfile | None = None):
        from config import POLISH_PROFILE
        self.profile = profile or POLISH_PROFILE

        # Load databases
        self.strong_names, self.regular_names = load_first_names()
        self.all_polish_names = self.strong_names | self.regular_names
        self.foreign_names = load_foreign_names()
        self.surname_data = load_surname_patterns()

        # Pre-compile patterns
        self.diacritics_re = re.compile(
            self.surname_data.get("diacritics_regex", r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
        )
        self.title_kw_re = re.compile(
            r"\b("
            + "|".join(re.escape(w) for w in self.surname_data.get("title_keywords_pl", []))
            + r")\b",
            re.IGNORECASE,
        )
        self.common_surnames: Set[str] = set(self.surname_data.get("common_surnames", []))
        self.suffix_unicode: List[str] = self.surname_data.get("suffixes_unicode", [])
        self.suffix_ascii: List[str] = self.surname_data.get("suffixes_ascii", [])
        self.company_kw: List[str] = self.surname_data.get("company_keywords_pl", [])

    # ── Main scoring method ────────────────────────────────────────────
    def score(self, row: pd.Series) -> Tuple[int, str]:
        """Return ``(score, reasons_string)`` for one profile row."""
        w = self.profile.weights
        score = 0
        reasons: List[str] = []

        first_raw = row.get("firstName", "")
        last_raw = row.get("lastName", "")
        first = normalize_name(first_raw)
        last = normalize_name(last_raw)
        title = str(row.get("title", "")) if pd.notna(row.get("title")) else ""
        summary = str(row.get("summary", "")) if pd.notna(row.get("summary")) else ""
        company = str(row.get("companyName", "")) if pd.notna(row.get("companyName")) else ""

        # ── Signal 1: Surname suffix ───────────────────────────────────
        has_suffix = False
        if any(last.endswith(s) for s in self.suffix_ascii):
            score += w[SIGNAL_SURNAME_SUFFIX]
            reasons.append(f"Polish surname suffix (+{w[SIGNAL_SURNAME_SUFFIX]})")
            has_suffix = True
        elif any(last.endswith(s) for s in self.suffix_unicode):
            score += w[SIGNAL_SURNAME_SUFFIX]
            reasons.append(f"Polish surname suffix/diacr (+{w[SIGNAL_SURNAME_SUFFIX]})")
            has_suffix = True

        # ── Signal 2: Surname in common-surname DB ─────────────────────
        surname_parts = re.split(r"[-\s]", last)
        if any(p in self.common_surnames for p in surname_parts if len(p) > 2):
            if not has_suffix:
                score += w[SIGNAL_SURNAME_DB]
                reasons.append(f"Common Polish surname (+{w[SIGNAL_SURNAME_DB]})")

        # ── Signal 3 & 4: Diacritics ──────────────────────────────────
        if self.diacritics_re.search(str(last_raw)):
            score += w[SIGNAL_DIACRITICS_LAST]
            reasons.append(f"Polish diacritics in last name (+{w[SIGNAL_DIACRITICS_LAST]})")
        if self.diacritics_re.search(str(first_raw)):
            score += w[SIGNAL_DIACRITICS_FIRST]
            reasons.append(f"Polish diacritics in first name (+{w[SIGNAL_DIACRITICS_FIRST]})")

        # ── Signal 5: First name (strong / regular) ────────────────────
        fn_parts = split_first_name(first)
        last_as_first = last.strip().lower()   # detect swapped names

        is_strong = (
            any(p in self.strong_names for p in fn_parts)
            or first in self.strong_names
            or last_as_first in self.strong_names
        )
        is_polish = (
            any(p in self.all_polish_names for p in fn_parts)
            or first in self.all_polish_names
            or last_as_first in self.all_polish_names
        )

        if is_strong:
            score += w[SIGNAL_STRONG_FIRST_NAME]
            reasons.append(f"Strongly Polish first name (+{w[SIGNAL_STRONG_FIRST_NAME]})")
        elif is_polish:
            score += w[SIGNAL_FIRST_NAME]
            reasons.append(f"Polish first name (+{w[SIGNAL_FIRST_NAME]})")

        # ── Signal 6: Foreign first name ───────────────────────────────
        is_foreign = (
            any(p in self.foreign_names for p in fn_parts)
            or first in self.foreign_names
        )
        if is_foreign and not is_strong and not is_polish:
            score += w[SIGNAL_FOREIGN_FIRST_NAME]
            reasons.append(f"Clearly foreign first name ({w[SIGNAL_FOREIGN_FIRST_NAME]})")

        # ── Signal 7: Job-title language ───────────────────────────────
        if title:
            if self.title_kw_re.search(title):
                score += w[SIGNAL_TITLE_KEYWORDS]
                reasons.append(f"Polish-language job title (+{w[SIGNAL_TITLE_KEYWORDS]})")
            else:
                lang = detect_language(title, self.profile.min_title_chars)
                if lang == self.profile.lang_code:
                    score += w[SIGNAL_TITLE_LANGDETECT]
                    reasons.append(f"Title detected as {self.profile.label} (+{w[SIGNAL_TITLE_LANGDETECT]})")

        # ── Signal 8: Summary / bio language ───────────────────────────
        if summary and len(summary.strip()) > self.profile.min_summary_chars:
            lang = detect_language(summary, self.profile.min_summary_chars)
            if lang == self.profile.lang_code:
                score += w[SIGNAL_SUMMARY_LANGDETECT]
                reasons.append(f"Summary detected as {self.profile.label} (+{w[SIGNAL_SUMMARY_LANGDETECT]})")

        # ── Signal 9: Company-name keywords ────────────────────────────
        comp_lower = company.lower()
        if any(kw in comp_lower for kw in self.company_kw):
            score += w[SIGNAL_COMPANY_KEYWORDS]
            reasons.append(f"Polish company name (+{w[SIGNAL_COMPANY_KEYWORDS]})")

        return score, "; ".join(reasons) if reasons else ""

    # ── Classify from score ────────────────────────────────────────────
    def classify(self, score: int) -> str:
        """Map a numeric score to a classification label."""
        if score >= self.profile.native_threshold:
            return "PL"
        elif score <= self.profile.foreign_threshold:
            return "EN"
        return "UNCERTAIN"
