"""Knowledge-base context for the AI (FR-9).

Retrieval, not stuffing: the whole KB used to be pasted into every prompt, so
its size was capped by the model's context window and every irrelevant entry
cost tokens and diluted the answer. Now:

- if the active KB is small (<= KB_FULL_CONTEXT_MAX_CHARS) all of it is sent -
  nothing can be missed, and behaviour is identical to before;
- otherwise only the KB_RETRIEVAL_TOP_K entries most relevant to the client's
  message are sent, found with PostgreSQL full-text search;
- if retrieval finds nothing, the model is told so explicitly (mode
  "none_matched"), which is a strong signal to escalate instead of guessing.

HONEST LIMITS: this is lexical (keyword + stemming) retrieval, not semantic
embedding search. "How much is a licence?" will find an entry that says
"licence"/"license" but not one that only says "permit". Embedding retrieval
(pgvector + an embedding model) would close that gap; it needs the extension
and an embedding provider, and this module is the single place it would plug
in. Very long KB entries are also not chunked - each entry is retrieved whole.
"""
import re
from typing import NamedTuple

from config import settings
from database import format_kb_rows, get_active_kb_rows, search_kb

# Kept for callers that import it from here.
from database import get_active_knowledge_context  # noqa: F401

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_MAX_TERMS = 30


class KnowledgeContext(NamedTuple):
    text: str
    mode: str  # "full" | "retrieved" | "none_matched"


def extract_terms(query: str) -> list[str]:
    """Distinct lowercase word tokens from free text, safe to join into a
    tsquery (letters/digits/underscore only)."""
    seen: dict[str, None] = {}
    for token in _WORD_RE.findall((query or "").lower()):
        if len(token) >= 2:
            seen.setdefault(token)
    return list(seen)[:_MAX_TERMS]


def get_knowledge_context(query: str | None = None) -> KnowledgeContext:
    rows = get_active_kb_rows()
    total_chars = sum(len(r["topic"]) + len(r["content"]) for r in rows)

    if total_chars <= settings.KB_FULL_CONTEXT_MAX_CHARS:
        return KnowledgeContext(format_kb_rows(rows), "full")

    terms = extract_terms(query or "")
    if not terms:
        # Nothing to search on (e.g. the document-upload flow): send as much
        # of the KB as fits rather than nothing.
        kept, used = [], 0
        for row in rows:
            used += len(row["topic"]) + len(row["content"])
            if used > settings.KB_FULL_CONTEXT_MAX_CHARS:
                break
            kept.append(row)
        return KnowledgeContext(format_kb_rows(kept), "full")

    hits = search_kb(terms, settings.KB_RETRIEVAL_TOP_K)
    if not hits:
        return KnowledgeContext("No Knowledge Base entry matched this question.", "none_matched")
    return KnowledgeContext(format_kb_rows(sorted(hits, key=lambda r: r["category"])), "retrieved")
