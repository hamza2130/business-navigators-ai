import sqlite3
from contextlib import closing

DB_NAME = "leads.db"


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

    print("Database initialized successfully with schema migrations.")


def get_lead(phone_number: str) -> dict | None:
    """Fetches lead data as a dictionary by identifier/phone number, or returns None."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT phone_number AS identifier, state, score, name, business_type,
                   document_id_number, document_expiry_date, document_status, human_takeover
            FROM leads WHERE phone_number = ?
            """,
            (phone_number,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


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
