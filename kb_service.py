import sqlite3

import database


def get_active_knowledge_context() -> str:
    """Fetch all active knowledge base items and format as context for the prompt."""
    # `import database` (not `from database import DB_NAME`) so this always
    # reads the current value - a plain name import would freeze a copy at
    # import time, which breaks anything that reconfigures DB_NAME later
    # (tests included).
    conn = sqlite3.connect(database.DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT category, topic, content FROM knowledge_base WHERE is_active = 1 ORDER BY category"
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "No specific knowledge base records loaded."

    formatted_docs = []
    current_category = ""
    
    for category, topic, content in rows:
        if category != current_category:
            current_category = category
            formatted_docs.append(f"\n--- Category: {current_category} ---")
        formatted_docs.append(f"• {topic}: {content}")

    return "\n".join(formatted_docs)