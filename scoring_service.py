"""Lead scoring and classification (FR-4).

Replaces the flat, hardcoded keyword lists that used to live in main.py.
Every rule here is stored in SQLite and editable by staff through the
dashboard without a deploy - see database.py's scoring_rules,
booking_keywords, and app_settings tables.
"""
import database


def score_breakdown(user_query: str) -> tuple[int, int]:
    """(base_boost, matched_keyword_weight) for this message. Split out so
    callers can tell "at least one intent keyword matched" (matched > 0)
    apart from the flat per-message boost every message gets."""
    text = user_query.lower()
    base = int(database.get_app_setting("base_engagement_boost", "5"))
    matched_weight = sum(
        weight for keyword, weight in database.get_active_scoring_rules()
        if keyword in text
    )
    return base, matched_weight


def compute_score_boost(user_query: str) -> int:
    """Total score delta for this message: base + every matched keyword's
    weight. Case-insensitive substring match."""
    base, matched = score_breakdown(user_query)
    return base + matched


def wants_booking(user_query: str) -> bool:
    text = user_query.lower()
    return any(kw in text for kw in database.get_active_booking_keywords())


def compute_lead_tier(score: int) -> str:
    """HOT / MEDIUM / LOW against admin-configurable thresholds
    (app_settings: hot_threshold, medium_threshold)."""
    hot = int(database.get_app_setting("hot_threshold", "70"))
    medium = int(database.get_app_setting("medium_threshold", "30"))
    if score >= hot:
        return "HOT"
    if score >= medium:
        return "MEDIUM"
    return "LOW"
