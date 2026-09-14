import io
import os
import re
from datetime import datetime

from PIL import Image
import pytesseract

# Tesseract's binary location is platform- and install-specific (it's a
# system binary, not a Python package). Only override pytesseract's default
# PATH lookup when TESSERACT_CMD is explicitly set - e.g. for local Windows
# dev where the installer doesn't add tesseract.exe to PATH. Leaving this
# unset (the default in Docker/Linux, where `apt install tesseract-ocr`
# already puts it on PATH) keeps OCR working without code changes.
_tesseract_cmd = os.getenv("TESSERACT_CMD")
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd


_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

# Numeric dates in either field order, and YYYY-first ISO style. Separator
# may be '-', '/' or '.'.
_YEAR = r"(?:19|20)\d{2}"
_NUMERIC_YMD = re.compile(rf"\b({_YEAR})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b")
_NUMERIC_DMY = re.compile(rf"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.]({_YEAR})\b")
_NUMERIC_MDY = re.compile(rf"\b(0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])[-/.]({_YEAR})\b")
# Text-month dates: "15 JAN 2029" or "JAN 15, 2029" / "January 15 2029".
_TEXT_DMY = re.compile(rf"\b(0?[1-9]|[12]\d|3[01])\s+([A-Za-z]{{3,9}})\.?,?\s+({_YEAR})\b")
_TEXT_MDY = re.compile(rf"\b([A-Za-z]{{3,9}})\.?\s+(0?[1-9]|[12]\d|3[01])\s*,?\s+({_YEAR})\b")

# Labels that precede an EXPIRY date specifically, as opposed to a date of
# birth or date of issue, which real ID documents virtually always print
# first. Matched with a short trailing window so we grab the date right next
# to the label rather than some unrelated date further down the page.
_EXPIRY_LABEL = re.compile(
    r"(?:expiry|expires?|expiration|valid\s*(?:until|thru|through))\D{0,25}",
    re.IGNORECASE,
)

_DATE_PATTERNS_IN_ORDER = (
    ("ymd", _NUMERIC_YMD),
    ("dmy_text", _TEXT_DMY),
    ("mdy_text", _TEXT_MDY),
    ("dmy", _NUMERIC_DMY),
    ("mdy", _NUMERIC_MDY),
)


def _normalize(year, month, day) -> str | None:
    """Validate and format as YYYY-MM-DD, or None if not a real date."""
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _month_to_num(name: str) -> str | None:
    return _MONTHS.get(name.strip(".").lower()[:3])


def _match_to_date(kind: str, m: re.Match) -> str | None:
    if kind == "ymd":
        return _normalize(m.group(1), m.group(2), m.group(3))
    if kind == "dmy":
        return _normalize(m.group(3), m.group(2), m.group(1))
    if kind == "mdy":
        return _normalize(m.group(3), m.group(1), m.group(2))
    if kind == "dmy_text":
        mon = _month_to_num(m.group(2))
        return _normalize(m.group(3), mon, m.group(1)) if mon else None
    if kind == "mdy_text":
        mon = _month_to_num(m.group(1))
        return _normalize(m.group(3), mon, m.group(2)) if mon else None
    return None


def _first_date_in(snippet: str) -> str | None:
    """Try every supported date format against a short window of text,
    preferring day-first (DD/MM/YYYY) over month-first for numeric dates
    when a date is ambiguous, matching UAE/GCC document conventions."""
    for kind, pattern in _DATE_PATTERNS_IN_ORDER:
        m = pattern.search(snippet)
        if m:
            date = _match_to_date(kind, m)
            if date:
                return date
    return None


def _all_dates(text: str) -> list[str]:
    """Every valid date found anywhere in the text, normalized."""
    found = []
    for kind, pattern in _DATE_PATTERNS_IN_ORDER:
        for m in pattern.finditer(text):
            date = _match_to_date(kind, m)
            if date:
                found.append(date)
    return found


def parse_expiry_date(text: str) -> str | None:
    """Extracts a document's EXPIRY date - not just the first date in the text.

    Real ID documents (passports, Emirates IDs, trade licences) print a date
    of birth and/or an issue date BEFORE the expiry date, so a naive
    "first date wins" search reliably returns the wrong field. This instead:

    1. Looks for a date near an explicit "expiry / expires / valid until"
       label, trying numeric (either day/month order) and text-month formats.
    2. Falls back to the LATEST date found anywhere in the text, since on a
       dated-validity document the expiry is later than any issue date or
       date of birth present.
    """
    if not text:
        return None

    label_match = _EXPIRY_LABEL.search(text)
    if label_match:
        window = text[label_match.end():label_match.end() + 20]
        labelled_date = _first_date_in(window)
        if labelled_date:
            return labelled_date

    candidates = _all_dates(text)
    return max(candidates) if candidates else None


def validate_document(image_bytes: bytes) -> dict:
    """Validates a document byte stream uploaded directly via REST API
    endpoints and extracts a compliance expiry date if found."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image).strip()

        if len(text) > 10:
            return {
                "status": "success",
                "is_legible": True,
                "extracted_text": text,
                "expiry_date": parse_expiry_date(text),
            }
        return {
            "status": "warning",
            "is_legible": False,
            "message": "Image text could not be clearly extracted. Please provide a clearer scan.",
            "expiry_date": None,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to process document: {str(e)}",
            "expiry_date": None,
        }
