"""PostgreSQL data layer with Row-Level Security tenant isolation.

Schema and RLS policies live in schema.sql - apply it once per database
(`psql -f schema.sql`) before running the app. Every table carries
tenant_id; DEFAULT_TENANT_ID below is the single tenant this app currently
operates as (SRS: "single live tenant now... multi-tenant DB from day
one"). Adding a second tenant means resolving the right tenant_id per
request (e.g. from which WHATSAPP_PHONE_NUMBER_ID received the message)
instead of the hardcoded constant - the schema and RLS policies already
support it with zero migration.

Public function names/signatures intentionally match the previous SQLite
module as closely as possible so callers (main.py, kb_service.py,
scoring_service.py, scheduler_service.py) needed minimal changes.
"""
import os
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# This module reads DATABASE_URL/TENANT_ID directly from the environment at
# import time, and can be imported before config.py (whose own
# load_dotenv() call would otherwise populate .env into os.environ first) -
# e.g. via `import database` in scoring_service.py, which main.py imports
# ahead of `from config import settings`. Loading .env here too (idempotent,
# safe to call more than once) means the values are correct regardless of
# import order.
load_dotenv()

DEFAULT_TENANT_ID = os.getenv("TENANT_ID", "default")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://app_user:change_me_in_production@127.0.0.1:5432/business_navigators"
)

_pool: ConnectionPool | None = None


def init_db() -> None:
    """Opens the connection pool. Schema/RLS setup is schema.sql's job
    (run once, out of band) - this only wires up the app's connections.
    Kept as init_db() (not init_pool()) since main.py's lifespan already
    calls this name from the previous SQLite version."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=True, kwargs={"row_factory": dict_row})
        _pool.wait(timeout=10)
    print("Database pool opened (PostgreSQL, RLS tenant isolation).")


def close_db() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_conn(tenant_id: str = DEFAULT_TENANT_ID):
    """Yields a connection with app.tenant_id set for the duration of one
    transaction, via SET LOCAL (set_config(..., is_local=true)) so a
    pooled connection can never leak one caller's tenant scope into the
    next borrower. Every RLS policy in schema.sql reads this setting -
    with it unset, tables default-fail-closed to zero visible rows (see
    the DEFAULT current_setting(...) + NOT NULL on every tenant_id
    column), never to "all tenants"."""
    if _pool is None:
        raise RuntimeError("database.init_db() must be called before use")
    with _pool.connection() as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            yield conn


# Backwards-compatible alias - some call sites (tests, ad hoc scripts)
# used this name against the old SQLite module.
def get_db_connection():
    return get_conn()


def _dict_or_none(row) -> dict | None:
    return dict(row) if row else None


# ==========================================================================
# Leads
# ==========================================================================
def get_lead(phone_number: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT phone_number AS identifier, state, score, name, business_type,
                   document_id_number, document_expiry_date, document_status, human_takeover,
                   turnover, industry, vat_status, service_interest, lead_tier
            FROM leads WHERE phone_number = %s
            """,
            (phone_number,),
        ).fetchone()
        return _dict_or_none(row)


def list_leads(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT phone_number AS identifier, state, score, name, business_type,
                   document_expiry_date, document_status, human_takeover,
                   turnover, industry, vat_status, service_interest, lead_tier
            FROM leads ORDER BY created_at DESC LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def set_human_takeover(phone_number: str, status: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO leads (tenant_id, phone_number, human_takeover)
            VALUES (%(tenant_id)s, %(phone_number)s, %(status)s)
            ON CONFLICT (tenant_id, phone_number) DO UPDATE SET human_takeover = excluded.human_takeover
            """,
            {"tenant_id": DEFAULT_TENANT_ID, "phone_number": phone_number, "status": bool(status)},
        )


def update_lead_state_and_score(phone_number: str, state: str, score_delta: int) -> None:
    """ADDS score_delta to the lead's score (or creates the lead starting
    from that delta). Contrast with save_or_update_lead(), whose `score`
    is an absolute value - see that function's docstring."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO leads (tenant_id, phone_number, state, score)
            VALUES (%(tenant_id)s, %(phone_number)s, %(state)s, %(score)s)
            ON CONFLICT (tenant_id, phone_number) DO UPDATE SET
                state = excluded.state,
                score = leads.score + excluded.score
            """,
            {"tenant_id": DEFAULT_TENANT_ID, "phone_number": phone_number, "state": state, "score": score_delta},
        )


def save_or_update_lead(
    phone_number: str,
    name: str = None,
    business_type: str = None,
    state: str = None,
    score: int = None,
    document_id_number: str = None,
    document_expiry_date: str = None,
    document_status: str = None,
    human_takeover: bool = None,
    turnover: str = None,
    industry: str = None,
    vat_status: str = None,
    service_interest: str = None,
    lead_tier: str = None,
) -> None:
    """Partial upsert: only fields explicitly passed (non-None) are
    written; a field left None is untouched on an existing lead and takes
    its schema default on a newly created one.

    `score` is an ABSOLUTE value, not a delta - for incrementing, use
    update_lead_state_and_score() instead.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO leads (tenant_id, phone_number) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id, phone_number) DO NOTHING",
            (DEFAULT_TENANT_ID, phone_number),
        )
        conn.execute(
            """
            UPDATE leads SET
                name = COALESCE(%(name)s, name),
                business_type = COALESCE(%(business_type)s, business_type),
                state = COALESCE(%(state)s, state),
                score = COALESCE(%(score)s, score),
                document_id_number = COALESCE(%(document_id_number)s, document_id_number),
                document_expiry_date = COALESCE(%(document_expiry_date)s, document_expiry_date),
                document_status = COALESCE(%(document_status)s, document_status),
                human_takeover = COALESCE(%(human_takeover)s, human_takeover),
                turnover = COALESCE(%(turnover)s, turnover),
                industry = COALESCE(%(industry)s, industry),
                vat_status = COALESCE(%(vat_status)s, vat_status),
                service_interest = COALESCE(%(service_interest)s, service_interest),
                lead_tier = COALESCE(%(lead_tier)s, lead_tier),
                reminder_30d_sent_at = CASE WHEN %(document_expiry_date)s IS NOT NULL THEN NULL ELSE reminder_30d_sent_at END,
                reminder_7d_sent_at = CASE WHEN %(document_expiry_date)s IS NOT NULL THEN NULL ELSE reminder_7d_sent_at END
            WHERE tenant_id = %(tenant_id)s AND phone_number = %(phone_number)s
            """,
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "phone_number": phone_number,
                "name": name,
                "business_type": business_type,
                "state": state,
                "score": score,
                "document_id_number": document_id_number,
                "document_expiry_date": document_expiry_date,
                "document_status": document_status,
                "human_takeover": human_takeover,
                "turnover": turnover,
                "industry": industry,
                "vat_status": vat_status,
                "service_interest": service_interest,
                "lead_tier": lead_tier,
            },
        )


def mark_reminder_sent(phone_number: str, window: str) -> None:
    column = {"30d": "reminder_30d_sent_at", "7d": "reminder_7d_sent_at"}[window]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE leads SET {column} = now() WHERE phone_number = %s",
            (phone_number,),
        )


# ==========================================================================
# Messages (conversation history + dedup)
# ==========================================================================
def save_message(identifier: str, channel: str, role: str, content: str, external_message_id: str = None) -> bool:
    """Returns False (writes nothing) if external_message_id was already
    recorded for this tenant - makes a retried webhook delivery a safe
    no-op instead of a duplicate reply."""
    with get_conn() as conn:
        try:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO messages (tenant_id, identifier, channel, role, content, external_message_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (DEFAULT_TENANT_ID, identifier, channel, role, content, external_message_id),
                )
            return True
        except psycopg.errors.UniqueViolation:
            return False


def is_duplicate_message(external_message_id: str) -> bool:
    if not external_message_id:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE external_message_id = %s LIMIT 1",
            (external_message_id,),
        ).fetchone()
        return row is not None


def get_recent_messages(identifier: str, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE identifier = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (identifier, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ==========================================================================
# Knowledge base
# ==========================================================================
def list_kb_items() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, category, topic, content, is_active, updated_at FROM knowledge_base ORDER BY category"
        ).fetchall()
        return [dict(r) for r in rows]


def add_kb_item(category: str, topic: str, content: str, is_active: bool = True) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO knowledge_base (tenant_id, category, topic, content, is_active)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (DEFAULT_TENANT_ID, category, topic, content, is_active),
        ).fetchone()
        return row["id"]


def delete_kb_item(item_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM knowledge_base WHERE id = %s", (item_id,))


def get_active_knowledge_context() -> str:
    """Fetches active KB items formatted as prompt context. Lives here
    (not kb_service.py) now that both share the same connection pool -
    kb_service.py just re-exports this for backwards compatibility."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category, topic, content FROM knowledge_base WHERE is_active = TRUE ORDER BY category"
        ).fetchall()

    if not rows:
        return "No specific knowledge base records loaded."

    formatted, current_category = [], ""
    for row in rows:
        if row["category"] != current_category:
            current_category = row["category"]
            formatted.append(f"\n--- Category: {current_category} ---")
        formatted.append(f"- {row['topic']}: {row['content']}")
    return "\n".join(formatted)


# ==========================================================================
# Scoring rules & booking keywords (FR-4)
# ==========================================================================
def get_active_scoring_rules() -> list[tuple[str, int]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT keyword, weight FROM scoring_rules WHERE is_active = TRUE").fetchall()
        return [(r["keyword"], r["weight"]) for r in rows]


def list_scoring_rules() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, keyword, weight, is_active, updated_at FROM scoring_rules ORDER BY weight DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def add_scoring_rule(keyword: str, weight: int) -> int:
    keyword = keyword.lower().strip()
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO scoring_rules (tenant_id, keyword, weight) VALUES (%s, %s, %s)
            ON CONFLICT (tenant_id, keyword) DO UPDATE SET weight = excluded.weight, updated_at = now()
            RETURNING id
            """,
            (DEFAULT_TENANT_ID, keyword, weight),
        ).fetchone()
        return row["id"]


def delete_scoring_rule(rule_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM scoring_rules WHERE id = %s", (rule_id,))


def get_active_booking_keywords() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT keyword FROM booking_keywords WHERE is_active = TRUE").fetchall()
        return [r["keyword"] for r in rows]


def list_booking_keywords() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id, keyword, is_active FROM booking_keywords ORDER BY keyword").fetchall()
        return [dict(r) for r in rows]


def add_booking_keyword(keyword: str) -> int:
    keyword = keyword.lower().strip()
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO booking_keywords (tenant_id, keyword) VALUES (%s, %s)
            ON CONFLICT (tenant_id, keyword) DO UPDATE SET is_active = TRUE
            RETURNING id
            """,
            (DEFAULT_TENANT_ID, keyword),
        ).fetchone()
        return row["id"]


def delete_booking_keyword(rule_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM booking_keywords WHERE id = %s", (rule_id,))


# ==========================================================================
# App settings
# ==========================================================================
def get_app_setting(key: str, default: str = None) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
        return row["value"] if row else default


def get_all_app_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_app_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (tenant_id, key, value) VALUES (%s, %s, %s)
            ON CONFLICT (tenant_id, key) DO UPDATE SET value = excluded.value
            """,
            (DEFAULT_TENANT_ID, key, value),
        )


# ==========================================================================
# Documents (FR-8 storage + FR-14 review queue)
# ==========================================================================
def create_document(
    lead_phone_number: str,
    storage_key: str,
    original_filename: str = None,
    content_type: str = None,
    size_bytes: int = None,
    extracted_expiry_date: str = None,
    status: str = "PENDING",
) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO documents (
                tenant_id, lead_phone_number, storage_key, original_filename,
                content_type, size_bytes, extracted_expiry_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                DEFAULT_TENANT_ID, lead_phone_number, storage_key, original_filename,
                content_type, size_bytes, extracted_expiry_date, status,
            ),
        ).fetchone()
        return row["id"]


def get_document(document_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = %s", (document_id,)).fetchone()
        return dict(row) if row else None


def list_documents_for_lead(lead_phone_number: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE lead_phone_number = %s ORDER BY uploaded_at DESC",
            (lead_phone_number,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_pending_documents() -> list[dict]:
    """The staff review queue (FR-14) - every upload starts here."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE status = 'PENDING' ORDER BY uploaded_at"
        ).fetchall()
        return [dict(r) for r in rows]


def review_document(document_id: int, status: str, reviewed_by: str, review_note: str = None) -> None:
    """status must be 'APPROVED' or 'REJECTED' - staff decision, with who
    and when recorded as the audit trail FR-14 asks for."""
    if status not in ("APPROVED", "REJECTED"):
        raise ValueError("status must be APPROVED or REJECTED")
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE documents SET status = %s, reviewed_by = %s, reviewed_at = now(), review_note = %s
            WHERE id = %s
            """,
            (status, reviewed_by, review_note, document_id),
        )


def log_document_access(document_id: int, accessed_by: str) -> None:
    """Called every time a signed URL is issued for a document - the
    audit trail the SRS asks for ("log access")."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO document_access_log (tenant_id, document_id, accessed_by) VALUES (%s, %s, %s)",
            (DEFAULT_TENANT_ID, document_id, accessed_by),
        )
