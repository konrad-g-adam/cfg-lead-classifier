"""
language_detect.py — Safe wrapper around the `langdetect` library.

Falls back gracefully if the library is missing.  Provides a
deterministic, exception-free interface used by the scoring engine.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Try to import langdetect; set a flag if unavailable ────────────────
try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0          # make results deterministic
    _LANGDETECT_OK = True
except ImportError:
    _LANGDETECT_OK = False
    logger.warning(
        "langdetect is not installed — language-detection signals will be "
        "skipped.  Install with:  pip install langdetect"
    )


def is_available() -> bool:
    """Return True if the langdetect library loaded successfully."""
    return _LANGDETECT_OK


def detect_language(text: str | None, min_chars: int = 15) -> str:
    """Detect the ISO 639-1 language code of *text*.

    Parameters
    ----------
    text : str | None
        The text to analyse.
    min_chars : int
        Minimum character count for a reliable detection.  Shorter
        strings return ``"unknown"``.

    Returns
    -------
    str
        Two-letter language code (e.g. ``"pl"``, ``"en"``) or
        ``"unknown"`` when detection fails or is skipped.
    """
    if not _LANGDETECT_OK:
        return "unknown"
    if text is None or len(str(text).strip()) < min_chars:
        return "unknown"
    try:
        return detect(str(text))
    except Exception:
        return "unknown"
