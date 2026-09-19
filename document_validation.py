"""FR-7: sanity-check an uploaded document before it reaches staff.

WHAT THIS IS - AND ISN'T. This is a keyword/pattern heuristic over the OCR'd
text, not authentication of the document. It can be wrong in both directions
(a blurry scan may read as UNKNOWN; a text-only forgery would pass), so it
NEVER rejects anything on its own: it records a best-guess document type and,
when something looks off, a human-readable flag that shows up in the staff
review queue and is passed to the AI so the client can be asked for a better
copy. Staff make the actual accept/reject decision (FR-14).

Flags raised:
- the type couldn't be recognised (or two types matched equally well);
- the type isn't one the business accepts (app_settings.accepted_document_types,
  a comma-separated list; empty/unset means "any recognised type");
- the document has already expired.
"""
import datetime
import re
from dataclasses import dataclass

import database

# type -> [(pattern, weight)]. A type is recognised when its summed weight
# reaches MIN_SCORE; weights are higher for text that is essentially unique
# to that document (e.g. an Emirates ID number, a passport MRZ line).
_SIGNALS: dict[str, list[tuple[str, int]]] = {
    "emirates_id": [
        (r"\b784-?\d{4}-?\d{7}-?\d\b", 3),
        (r"resident\s+identity\s+card|emirates\s+id|identity\s+card", 2),
        (r"united\s+arab\s+emirates", 1),
        (r"بطاقة\s+هوية|الهيئة\s+الاتحادية\s+للهوية", 2),
    ],
    "passport": [
        (r"\bP<[A-Z]{3}", 3),
        (r"\bpassport\b", 2),
        (r"date\s+of\s+expiry", 1),
        (r"place\s+of\s+(birth|issue)|nationality", 1),
    ],
    "trade_license": [
        (r"trade\s+licen[cs]e|commercial\s+licen[cs]e|business\s+licen[cs]e", 3),
        (r"licen[cs]e\s+(no|number)", 2),
        (r"department\s+of\s+(economy|economic)|economic\s+development|free\s+zone", 1),
        (r"legal\s+(form|type)|business\s+activit", 1),
    ],
    "residence_visa": [
        (r"residen(ce|cy)\s+(visa|permit)|residence\s+permit", 3),
        (r"\b(gdrfa|icp|federal\s+authority\s+for\s+identity)\b", 2),
        (r"entry\s+permit|\bvisa\b", 1),
        (r"file\s+(no|number)|sponsor", 1),
    ],
    "vat_certificate": [
        (r"tax\s+registration\s+certificate|vat\s+registration\s+certificate", 3),
        (r"tax\s+registration\s+number|\bTRN\b", 2),
        (r"federal\s+tax\s+authority|\bFTA\b", 1),
    ],
}
MIN_SCORE = 3

DOCUMENT_TYPES = tuple(_SIGNALS)
_TYPE_LABELS = {
    "emirates_id": "Emirates ID",
    "passport": "passport",
    "trade_license": "trade licence",
    "residence_visa": "residence visa",
    "vat_certificate": "VAT certificate",
}


@dataclass
class Validation:
    detected_type: str  # one of DOCUMENT_TYPES, or "unknown"
    flag: str | None    # None = nothing looked wrong

    @property
    def ok(self) -> bool:
        return self.flag is None


def detect_document_type(text: str) -> str:
    """Best-matching document type, or "unknown" if nothing scores at least
    MIN_SCORE or the top two types tie (ambiguous - better to ask a human
    than to guess)."""
    scores = {
        doc_type: sum(w for pattern, w in signals if re.search(pattern, text or "", re.IGNORECASE))
        for doc_type, signals in _SIGNALS.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best = ranked[0]
    if best < MIN_SCORE or (len(ranked) > 1 and ranked[1][1] == best):
        return "unknown"
    return best_type


def _accepted_types() -> set[str] | None:
    raw = database.get_app_setting("accepted_document_types", "") or ""
    accepted = {t.strip().lower() for t in raw.split(",") if t.strip()}
    return accepted or None


def label(doc_type: str) -> str:
    return _TYPE_LABELS.get(doc_type, "document")


def validate_document(text: str, expiry_date: str | None) -> Validation:
    """expiry_date is the ISO date string ocr_service extracted (or None)."""
    doc_type = detect_document_type(text)
    flags: list[str] = []

    if doc_type == "unknown":
        flags.append("Could not recognise the document type from its text (unclear scan, or not a supported document).")
    else:
        accepted = _accepted_types()
        if accepted is not None and doc_type not in accepted:
            flags.append(
                f"Looks like a {label(doc_type)}, which is not an accepted document type "
                f"(accepted: {', '.join(sorted(accepted))})."
            )

    if expiry_date:
        try:
            if datetime.date.fromisoformat(expiry_date) < datetime.date.today():
                flags.append(f"The document has already expired ({expiry_date}).")
        except ValueError:
            pass

    return Validation(detected_type=doc_type, flag=" ".join(flags) or None)
