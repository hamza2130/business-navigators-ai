"""Shared between main.py (enqueues jobs) and worker.py (consumes them) -
kept separate from both so importing one for its Redis settings doesn't
drag in the other (worker.py imports functions from main.py; main.py
importing worker.py back would be circular)."""
from urllib.parse import urlparse

from arq.connections import RedisSettings


def redis_settings_from_url(url: str) -> RedisSettings:
    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        password=parsed.password,
        database=int((parsed.path or "/0").lstrip("/") or 0),
    )
