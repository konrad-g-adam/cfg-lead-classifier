"""
config.py — Classification configuration: weights, thresholds, and settings.

All scoring weights and classification thresholds are defined here so they
can be tuned without touching the scoring engine.  To add a new target
language pair (e.g. DE/EN), duplicate the POLISH block and adjust the
signal weights.
"""

from dataclasses import dataclass, field
from typing import Dict


# ── Signal weight keys (used in scoring.py) ────────────────────────────
SIGNAL_SURNAME_SUFFIX       = "surname_suffix"
SIGNAL_SURNAME_DB           = "surname_db"
SIGNAL_DIACRITICS_LAST      = "diacritics_last"
SIGNAL_DIACRITICS_FIRST     = "diacritics_first"
SIGNAL_STRONG_FIRST_NAME    = "strong_first_name"
SIGNAL_FIRST_NAME           = "first_name"
SIGNAL_FOREIGN_FIRST_NAME   = "foreign_first_name"
SIGNAL_TITLE_KEYWORDS       = "title_keywords"
SIGNAL_TITLE_LANGDETECT     = "title_langdetect"
SIGNAL_SUMMARY_LANGDETECT   = "summary_langdetect"
SIGNAL_COMPANY_KEYWORDS     = "company_keywords"


@dataclass
class LanguageProfile:
    """Scoring weights and thresholds for one target-language classification."""

    # Human-readable label
    label: str = "Polish"
    # ISO 639-1 code used by langdetect
    lang_code: str = "pl"

    # ── Signal weights (positive = evidence FOR this language) ──────────
    weights: Dict[str, int] = field(default_factory=lambda: {
        SIGNAL_SURNAME_SUFFIX:     3,
        SIGNAL_SURNAME_DB:         2,
        SIGNAL_DIACRITICS_LAST:    2,
        SIGNAL_DIACRITICS_FIRST:   2,
        SIGNAL_STRONG_FIRST_NAME:  3,
        SIGNAL_FIRST_NAME:         2,
        SIGNAL_FOREIGN_FIRST_NAME: -3,
        SIGNAL_TITLE_KEYWORDS:     3,
        SIGNAL_TITLE_LANGDETECT:   3,
        SIGNAL_SUMMARY_LANGDETECT: 2,
        SIGNAL_COMPANY_KEYWORDS:   1,
    })

    # ── Classification thresholds ──────────────────────────────────────
    # score >= native_threshold  →  native speaker (PL)
    native_threshold: int = 3
    # score <= foreign_threshold →  foreign (EN)
    foreign_threshold: int = 0
    # everything in between      →  UNCERTAIN

    # ── Language detection settings ────────────────────────────────────
    # Minimum character count for langdetect to be reliable
    min_title_chars: int = 15
    min_summary_chars: int = 30


# ── Built-in profiles ─────────────────────────────────────────────────

POLISH_PROFILE = LanguageProfile()  # defaults are tuned for PL/EN

# Example: German profile stub (extend name databases to activate)
GERMAN_PROFILE = LanguageProfile(
    label="German",
    lang_code="de",
    weights={
        SIGNAL_SURNAME_SUFFIX:     2,
        SIGNAL_SURNAME_DB:         2,
        SIGNAL_DIACRITICS_LAST:    1,
        SIGNAL_DIACRITICS_FIRST:   1,
        SIGNAL_STRONG_FIRST_NAME:  3,
        SIGNAL_FIRST_NAME:         2,
        SIGNAL_FOREIGN_FIRST_NAME: -3,
        SIGNAL_TITLE_KEYWORDS:     3,
        SIGNAL_TITLE_LANGDETECT:   3,
        SIGNAL_SUMMARY_LANGDETECT: 2,
        SIGNAL_COMPANY_KEYWORDS:   1,
    },
    native_threshold=3,
    foreign_threshold=0,
)

# Map of supported profiles keyed by short code
PROFILES = {
    "pl": POLISH_PROFILE,
    "de": GERMAN_PROFILE,
}

# ── Default runtime settings ──────────────────────────────────────────
DEFAULT_LANGUAGE = "pl"
DEFAULT_OUTPUT_DIR = "."
DEFAULT_ENCODING = "utf-8-sig"       # BOM-prefixed UTF-8 for Excel compat
CSV_READ_ENCODING = "utf-8"
