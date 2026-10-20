import time
from typing import Any

from app.config import get_settings

CHANNEL = "tickets"
_client: Any = None


def _redis():
    global _client
    if _client is not None:
        return _client
    url = get_settings().redis_url
    if not url:
        return None
    try:
        import redis
    except ImportError:
        return None
    try:
        _client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        _client.ping()
    except Exception:
        _client = None
    return _client


def publish(message: str) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.publish(CHANNEL, message)
    except Exception:
        pass


def listen():
    client = _redis()
    if client is None:
        return None
    try:
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL)
        return pubsub
    except Exception:
        return None


def allow_chat(user_id: str) -> bool:
    """Return False when the user exceeded the per-minute chat quota. Fails open."""
    client = _redis()
    if client is None:
        return True
    settings = get_settings()
    limit = max(1, settings.chat_rate_limit)
    window = max(1, settings.chat_rate_window_seconds)
    key = f"rate:chat:{user_id}:{int(time.time() // window)}"
    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window * 2)
        return count <= limit
    except Exception:
        return True
