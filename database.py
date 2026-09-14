import os
import sqlite3
from contextlib import closing

# Overridable so the SQLite file can live on a mounted volume in a
# container (see Dockerfile/docker-compose.yml) instead of the working
# directory, which would otherwise be lost whenever the container is
# recreated.
DB_NAME = os.getenv("DB_NAME", "leads.db")


def get_db_connection() -> sqlite3.Connection:
    """Returns a connection to the SQLite database with row factory enabled.

    Callers must use this inside `contextlib.closing(...)` (or close it
    themselves) - `with conn:` alone only commits/rolls back a transaction,
    it does not close the underlying connection.
    """
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Initializes the SQLite database and performs automatic migrations
    if new columns or tables are missing from an existing database file.
    """
    with closing(get_db_connection()) as conn:
        with conn:  # commits/rolls back the transaction; connection itself
            # is still closed by the `closing()` wrapper on exit.
            cursor = conn.cursor()

            # 1. Ensure core leads table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    phone_number TEXT PRIMARY KEY,
                    state TEXT DEFAULT 'NEW',
                    score INTEGER DEFAULT 0
                )
            """)

            # 2. Inspect existing leads table structure
            cursor.execute("PRAGMA table_info(leads)")
            existing_columns = [col[1] for col in cursor.fetchall()]

            # 3. Auto-add any missing columns without dropping existing data
            required_columns = {
                "state": "TEXT DEFAULT 'NEW'",
                "score": "INTEGER DEFAULT 0",
                "name": "TEXT",
                "business_type": "TEXT",
                "document_id_number": "TEXT",
                "document_expiry_date": "TEXT",
                "document_status": "TEXT DEFAULT 'PENDING'",
                "human_takeover": "INTEGER DEFAULT 0",
                # Set the first time each reminder is actually sent for the
                # CURRENT document_expiry_date; cleared whenever that date
                # changes (see save_or_update_lead). Lets the scheduler use
                # a "days remaining <= threshold" range check instead of
                # exact-date equality - a missed day of downtime no longer
                # skips the client forever, and the guard column stops it
                # from re-sending the same reminder every day after.
                "reminder_30d_sent_at": "TEXT",
                "reminder_7d_sent_at": "TEXT",
                # Structured qualification fields (FR-3) - filled in as the
                # AI extracts them from natural conversation, rather than
                # relying on staff to read a transcript to find them.
                "turnover": "TEXT",
                "industry": "TEXT",
                "vat_status": "TEXT",
                "service_interest": "TEXT",
                # Hot/Medium/Low classification (FR-4), recomputed from
                # `score` against admin-configurable thresholds every time
                # score changes - see scoring_service.compute_lead_tier().
                "lead_tier": "TEXT",
            }

            for col_name, col_def in required_columns.items():
                if col_name not in existing_columns:
                    cursor.execute(
                        f"ALTER TABLE leads ADD COLUMN {col_name} {col_def}"
                    )

            # 4. Ensure knowledge base table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 5. Conversation history, so the AI can be given prior turns
            # instead of answering every message from scratch (FR-1).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identifier TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    external_message_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_identifier "
                "ON messages(identifier, created_at)"
            )
            # Enforced only where Meta actually supplies a message id (text/
            # media inbound messages), which is what makes retried webhook
            # deliveries idempotent.
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external_id "
                "ON messages(external_message_id) "
                "WHERE external_message_id IS NOT NULL"
            )

            # 6. Scoring rules (FR-4): keyword -> weight, editable by staff
            # via the dashboard instead of hardcoded in main.py. Separate
            # from booking_keywords below - a keyword can independently
            # score intent AND trigger booking (e.g. "consultation" does
            # both), so these can't share one UNIQUE(keyword) table.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scoring_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL UNIQUE,
                    weight INTEGER NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS booking_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL UNIQUE,
                    is_active INTEGER DEFAULT 1
                )
            """)
            cursor.execute("SELECT COUNT(*) FROM scoring_rules")
            if cursor.fetchone()[0] == 0:
                _seed_default_scoring_rules(cursor)
            cursor.execute("SELECT COUNT(*) FROM booking_keywords")
            if cursor.fetchone()[0] == 0:
                _seed_default_booking_keywords(cursor)

            # 7. Small admin-configurable key/value settings - currently
            # just the Hot/Medium/Low score thresholds and the flat
            # per-message engagement boost, but a generic enough shape to
            # hold future tunables without another migration.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            for key, default in (
                ("hot_threshold", "70"),
                ("medium_threshold", "30"),
                ("base_engagement_boost", "5"),
            ):
                cursor.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (key, default),
                )

    print("Database initialized successfully with schema migrations.")


def _seed_default_scoring_rules(cursor) -> None:
    """One-time seed matching the keyword list that used to be hardcoded in
    main.py (each worth the old flat +15), so upgrading an existing
    deployment doesn't silently zero out its scoring behaviour. Staff can
    edit/remove/add to these afterwards via the dashboard - this only runs
    when the table is empty."""
    intent_keywords = [
        "setup", "compliance", "growth", "tax", "license", "consultation",
        "booking", "visa", "cost", "appointment", "schedule", "meet",
        "expire", "expiry", "expiration", "document",
    ]
    cursor.executemany(
        "INSERT INTO scoring_rules (keyword, weight) VALUES (?, ?) "
        "ON CONFLICT(keyword) DO NOTHING",
        [(kw, 15) for kw in intent_keywords],
    )


def _seed_default_booking_keywords(cursor) -> None:
    """One-time seed matching the keyword list that used to be hardcoded in
    main.py for triggering the calendar-booking flow."""
    booking_keywords = [
        "book", "booking", "appointment", "schedule", "meet", "meeting", "call",
    ]
    cursor.executemany(
        "INSERT INTO booking_keywords (keyword) VALUES (?) "
        "ON CONFLICT(keyword) DO NOTHING",
        [(kw,) for kw in booking_keywords],
    )


def get_lead(phone_number: str) -> dict | None:
    """Fetches lead data as a dictionary by identifier/phone number, or returns None."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT phone_number AS identifier, state, score, name, business_type,
                   document_id_number, document_expiry_date, document_status, human_takeover,
                   turnover, industry, vat_status, service_interest, lead_tier
            FROM leads WHERE phone_number = ?
            """,
            (phone_number,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_leads(limit: int = 200) -> list[dict]:
    """All leads for the staff dashboard's lead list, most recently
    active first (by rowid, since SQLite has no updated_at on this table
    without another migration - good enough for a MVP-scale list)."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT phone_number AS identifier, state, score, name, business_type,
                   document_expiry_date, document_status, human_takeover,
                   turnover, industry, vat_status, service_interest, lead_tier
            FROM leads ORDER BY rowid DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def set_human_takeover(phone_number: str, status: bool) -> None:
    """Updates the human agent takeover flag for a specific lead."""
    takeover_val = 1 if status else 0
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO leads (phone_number, human_takeover)
                VALUES (?, ?)
                ON CONFLICT(phone_number) DO UPDATE SET
                    human_takeover = excluded.human_takeover
                """,
                (phone_number, takeover_val),
            )


def update_lead_state_and_score(
    phone_number: str, state: str, score_delta: int
) -> None:
    """Updates an existing lead's state and ADDS score_delta to its score
    (or inserts a new lead record atomically, starting from that delta).

    score_delta is an increment, not an absolute value - this is the
    conversational scoring path (see process_message_intent). Contrast with
    save_or_update_lead(), which sets an absolute score - used by the
    document-upload path, where the caller already computes the new total.
    """
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO leads (phone_number, state, score)
                VALUES (?, ?, ?)
                ON CONFLICT(phone_number) DO UPDATE SET
                    state = excluded.state,
                    score = leads.score + excluded.score
            """,
                (phone_number, state, score_delta),
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
    human_takeover: int = None,
    turnover: str = None,
    industry: str = None,
    vat_status: str = None,
    service_interest: str = None,
    lead_tier: str = None,
) -> None:
    """Partial upsert: only the fields explicitly passed (non-None) are
    written. A field left as None is untouched on an existing lead, and
    takes its schema default (state='NEW', score=0, document_status=
    'PENDING', human_takeover=0) on a newly created one.

    `score` here is an ABSOLUTE value, not a delta - pass the full new
    total (e.g. current_score + 30). For incrementing, use
    update_lead_state_and_score() instead.
    """
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.cursor()
            # Ensure the row exists first so column defaults apply naturally
            # on first creation; a no-op if the lead already exists.
            cursor.execute(
                "INSERT INTO leads (phone_number) VALUES (?) "
                "ON CONFLICT(phone_number) DO NOTHING",
                (phone_number,),
            )
            cursor.execute(
                """
                UPDATE leads SET
                    name = COALESCE(?, name),
                    business_type = COALESCE(?, business_type),
                    state = COALESCE(?, state),
                    score = COALESCE(?, score),
                    document_id_number = COALESCE(?, document_id_number),
                    document_expiry_date = COALESCE(?, document_expiry_date),
                    document_status = COALESCE(?, document_status),
                    human_takeover = COALESCE(?, human_takeover),
                    turnover = COALESCE(?, turnover),
                    industry = COALESCE(?, industry),
                    vat_status = COALESCE(?, vat_status),
                    service_interest = COALESCE(?, service_interest),
                    lead_tier = COALESCE(?, lead_tier),
                    reminder_30d_sent_at = CASE WHEN ? IS NOT NULL THEN NULL ELSE reminder_30d_sent_at END,
                    reminder_7d_sent_at = CASE WHEN ? IS NOT NULL THEN NULL ELSE reminder_7d_sent_at END
                WHERE phone_number = ?
                """,
                (
                    name,
                    business_type,
                    state,
                    score,
                    document_id_number,
                    document_expiry_date,
                    document_status,
                    human_takeover,
                    turnover,
                    industry,
                    vat_status,
                    service_interest,
                    lead_tier,
                    document_expiry_date,
                    document_expiry_date,
                    phone_number,
                ),
            )


def mark_reminder_sent(phone_number: str, window: str) -> None:
    """Records that the 30-day or 7-day expiry reminder was just sent, so
    the scheduler doesn't send it again on every subsequent run."""
    column = {"30d": "reminder_30d_sent_at", "7d": "reminder_7d_sent_at"}[window]
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute(
                f"UPDATE leads SET {column} = datetime('now') WHERE phone_number = ?",
                (phone_number,),
            )


def save_message(
    identifier: str,
    channel: str,
    role: str,
    content: str,
    external_message_id: str = None,
) -> bool:
    """Records one turn of a conversation for later use as LLM context.

    Returns False (and writes nothing) if external_message_id has already
    been recorded - this is what makes a retried webhook delivery from Meta
    a safe no-op instead of a duplicate reply.
    """
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO messages (identifier, channel, role, content, external_message_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (identifier, channel, role, content, external_message_id),
                )
                return True
            except sqlite3.IntegrityError:
                return False  # external_message_id already seen - duplicate delivery


def is_duplicate_message(external_message_id: str) -> bool:
    """True if this Meta message id has already been processed."""
    if not external_message_id:
        return False
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM messages WHERE external_message_id = ? LIMIT 1",
            (external_message_id,),
        )
        return cursor.fetchone() is not None


def get_recent_messages(identifier: str, limit: int = 10) -> list[dict]:
    """Last `limit` turns for this identifier, oldest first, formatted for
    direct use as LLM chat history (role/content pairs)."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT role, content FROM messages
            WHERE identifier = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (identifier, limit),
        )
        rows = cursor.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ==========================================================================
# Scoring rules & booking keywords (FR-4: admin-configurable, no deploy
# needed to add/remove/reweight a keyword)
# ==========================================================================
def get_active_scoring_rules() -> list[tuple[str, int]]:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT keyword, weight FROM scoring_rules WHERE is_active = 1"
        )
        return [(row["keyword"], row["weight"]) for row in cursor.fetchall()]


def list_scoring_rules() -> list[dict]:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, keyword, weight, is_active, updated_at FROM scoring_rules ORDER BY weight DESC"
        )
        return [dict(row) for row in cursor.fetchall()]


def add_scoring_rule(keyword: str, weight: int) -> int:
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO scoring_rules (keyword, weight) VALUES (?, ?)
                ON CONFLICT(keyword) DO UPDATE SET weight = excluded.weight,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (keyword.lower().strip(), weight),
            )
            cursor.execute("SELECT id FROM scoring_rules WHERE keyword = ?", (keyword.lower().strip(),))
            return cursor.fetchone()["id"]


def delete_scoring_rule(rule_id: int) -> None:
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute("DELETE FROM scoring_rules WHERE id = ?", (rule_id,))


def get_active_booking_keywords() -> list[str]:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT keyword FROM booking_keywords WHERE is_active = 1")
        return [row["keyword"] for row in cursor.fetchall()]


def list_booking_keywords() -> list[dict]:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, keyword, is_active FROM booking_keywords ORDER BY keyword")
        return [dict(row) for row in cursor.fetchall()]


def add_booking_keyword(keyword: str) -> int:
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO booking_keywords (keyword) VALUES (?) "
                "ON CONFLICT(keyword) DO UPDATE SET is_active = 1",
                (keyword.lower().strip(),),
            )
            cursor.execute("SELECT id FROM booking_keywords WHERE keyword = ?", (keyword.lower().strip(),))
            return cursor.fetchone()["id"]


def delete_booking_keyword(rule_id: int) -> None:
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute("DELETE FROM booking_keywords WHERE id = ?", (rule_id,))


# ==========================================================================
# App settings (score-tier thresholds, etc.)
# ==========================================================================
def get_app_setting(key: str, default: str = None) -> str | None:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default


def get_all_app_settings() -> dict:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM app_settings")
        return {row["key"]: row["value"] for row in cursor.fetchall()}


def set_app_setting(key: str, value: str) -> None:
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
