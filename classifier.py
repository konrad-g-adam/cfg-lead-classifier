#!/usr/bin/env python3
"""
classifier.py — Main CLI entry point for the Lead Nationality Classifier.

Usage
-----
    # Classify a local CSV (default PL/EN, outputs next to input)
    python classifier.py data/leads.csv

    # Custom output directory and file prefix
    python classifier.py data/leads.csv -o results/ -p MyProject

    # Adjust classification thresholds on the fly
    python classifier.py data/leads.csv --native-threshold 4 --foreign-threshold -1

    # Classify from a public Google Sheet
    python classifier.py "https://docs.google.com/spreadsheets/d/SHEET_ID/edit?gid=0#gid=0"

    # Skip Excel output (CSV + JSON only)
    python classifier.py data/leads.csv --no-excel

    # Verbose logging
    python classifier.py data/leads.csv -v
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

# Ensure the project root is on sys.path so sibling modules resolve
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PROFILES, DEFAULT_LANGUAGE, LanguageProfile
from scoring import ProfileScorer
from utils import read_input, write_csvs, write_excel, write_json_report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lead-classifier",
        description=(
            "Classify Phantombuster / Sales Navigator leads as native "
            "speakers vs. foreigners using an 8-signal scoring engine."
        ),
    )
    p.add_argument(
        "input",
        help="Path to a CSV file or a public Google Sheets URL.",
    )
    p.add_argument(
        "-o", "--output-dir",
        default=None,
        help="Directory for output files (default: same as input file).",
    )
    p.add_argument(
        "-p", "--prefix",
        default="PhB_SalesNav",
        help="Filename prefix for outputs (default: PhB_SalesNav).",
    )
    p.add_argument(
        "-l", "--language",
        default=DEFAULT_LANGUAGE,
        choices=list(PROFILES.keys()),
        help=f"Target language profile (default: {DEFAULT_LANGUAGE}).",
    )
    p.add_argument(
        "--native-threshold",
        type=int,
        default=None,
        help="Override: minimum score to classify as native speaker.",
    )
    p.add_argument(
        "--foreign-threshold",
        type=int,
        default=None,
        help="Override: maximum score to classify as foreign.",
    )
    p.add_argument(
        "--no-excel",
        action="store_true",
        help="Skip writing the combined Excel workbook.",
    )
    p.add_argument(
        "--no-json",
        action="store_true",
        help="Skip writing the JSON summary report.",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )
    return p


def run(args: argparse.Namespace) -> None:
    """Execute the full classification pipeline."""

    # ── Logging ────────────────────────────────────────────────────────
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Resolve output directory ───────────────────────────────────────
    if args.output_dir:
        out_dir = Path(args.output_dir)
    elif args.input.startswith("https://"):
        out_dir = Path(".")
    else:
        out_dir = Path(args.input).resolve().parent

    # ── Load language profile with optional overrides ──────────────────
    profile: LanguageProfile = PROFILES[args.language]
    if args.native_threshold is not None:
        profile.native_threshold = args.native_threshold
    if args.foreign_threshold is not None:
        profile.foreign_threshold = args.foreign_threshold

    # ── Banner ─────────────────────────────────────────────────────────
    print("=" * 64)
    print("  LEAD NATIONALITY CLASSIFIER")
    print(f"  Language profile : {profile.label}")
    print(f"  Native threshold : >= {profile.native_threshold}")
    print(f"  Foreign threshold: <= {profile.foreign_threshold}")
    print("=" * 64)

    # ── Step 1: Load data ──────────────────────────────────────────────
    t0 = time.time()
    print(f"\n[1/4] Loading data from: {args.input}")
    df = read_input(args.input)
    print(f"      Loaded {len(df):,} profiles  ({time.time() - t0:.1f}s)")

    # ── Step 2: Score & classify ───────────────────────────────────────
    t1 = time.time()
    print("\n[2/4] Scoring profiles (language detection may take a moment)...")
    scorer = ProfileScorer(profile)
    results = df.apply(scorer.score, axis=1)
    df["_classification_score"] = results.apply(lambda x: x[0])
    df["_classification_reasons"] = results.apply(lambda x: x[1])
    df["_classification"] = df["_classification_score"].apply(scorer.classify)
    elapsed = time.time() - t1
    print(f"      Scored {len(df):,} profiles  ({elapsed:.1f}s)")

    # ── Step 3: Print summary ──────────────────────────────────────────
    counts = df["_classification"].value_counts()
    total = len(df)
    pl = counts.get("PL", 0)
    en = counts.get("EN", 0)
    unc = counts.get("UNCERTAIN", 0)

    print(f"\n[3/4] Classification results:")
    print(f"      {profile.label} (PL):  {pl:>6,}  ({pl / total * 100:.1f}%)")
    print(f"      Foreign  (EN):  {en:>6,}  ({en / total * 100:.1f}%)")
    print(f"      Uncertain:      {unc:>6,}  ({unc / total * 100:.1f}%)")
    print(f"      Total:          {total:>6,}")

    # ── Step 4: Write outputs ──────────────────────────────────────────
    print(f"\n[4/4] Writing outputs to: {out_dir.resolve()}")
    csv_files = write_csvs(df, out_dir, args.prefix)
    for label, path in csv_files.items():
        tag = f"({df[df['_classification'] == label].shape[0]:,} rows)" if label != "AUDIT" else "(full audit)"
        print(f"      CSV  {label:<12s} -> {path.name}  {tag}")

    if not args.no_excel:
        xlsx = write_excel(df, out_dir, args.prefix)
        print(f"      XLSX             -> {xlsx.name}")

    if not args.no_json:
        js = write_json_report(df, out_dir, args.prefix, source_file=args.input)
        print(f"      JSON report      -> {js.name}")

    # ── Done ───────────────────────────────────────────────────────────
    total_time = time.time() - t0
    print(f"\nDone in {total_time:.1f}s.")
    print("=" * 64)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
