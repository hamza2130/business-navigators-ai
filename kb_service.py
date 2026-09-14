# kb_service.py
# The actual query now lives in database.py (shares the same connection
# pool and RLS tenant scoping as everything else). Re-exported here so
# ai_service.py's `from kb_service import get_active_knowledge_context`
# doesn't need to change.
from database import get_active_knowledge_context  # noqa: F401
