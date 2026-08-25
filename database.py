import sqlite3

DB_NAME = "leads.db"


def get_db_connection() -> sqlite3.Connection:
    """Returns a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initializes the SQLite database and performs automatic migrations

    if new columns or tables are missing from an existing database file.
    """
    with get_db_connection() as conn:
        with conn:  # Context manager automatically commits/rolls back transactions
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

    print("Database initialized successfully with schema migrations.")


def get_lead(phone_number: str) -> dict | None:
    """Fetches lead data as a dictionary by identifier/phone number, or returns None."""
    with get_db_connection() as conn:
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
    with get_db_connection() as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE leads SET human_takeover = ? WHERE phone_number = ?
                """,
                (takeover_val, phone_number),
            )


def update_lead_state_and_score(
    phone_number: str, state: str, score_delta: int
) -> None:
    """Updates an existing lead's state and score, or inserts a new lead record atomically."""
    with get_db_connection() as conn:
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
    state: str = "NEW",
    score: int = 0,
    document_id_number: str = None,
    document_expiry_date: str = None,
    document_status: str = "PENDING",
    human_takeover: int = 0,
) -> None:
    """Full upsert function for saving complete lead details."""
    with get_db_connection() as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO leads (
                    phone_number, name, business_type, state, score, 
                    document_id_number, document_expiry_date, document_status, human_takeover
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phone_number) DO UPDATE SET
                    name = COALESCE(excluded.name, leads.name),
                    business_type = COALESCE(excluded.business_type, leads.business_type),
                    state = COALESCE(excluded.state, leads.state),
                    score = COALESCE(excluded.score, leads.score),
                    document_id_number = COALESCE(excluded.document_id_number, leads.document_id_number),
                    document_expiry_date = COALESCE(excluded.document_expiry_date, leads.document_expiry_date),
                    document_status = COALESCE(excluded.document_status, leads.document_status),
                    human_takeover = COALESCE(excluded.human_takeover, leads.human_takeover)
            """,
                (
                    phone_number,
                    name,
                    business_type,
                    state,
                    score,
                    document_id_number,
                    document_expiry_date,
                    document_status,
                    human_takeover,
                ),
            )