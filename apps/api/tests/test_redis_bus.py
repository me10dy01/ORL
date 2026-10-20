from app.services import redis_bus


def test_allow_chat_without_redis_allows():
    assert redis_bus.allow_chat("u-no-redis") is True


def test_allow_chat_limits_after_limit(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.counts = {}

        def incr(self, key):
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

        def expire(self, key, seconds):
            return True

    fake = FakeClient()
    monkeypatch.setattr(redis_bus, "_client", fake)
    for _ in range(20):
        assert redis_bus.allow_chat("u-limit") is True
    assert redis_bus.allow_chat("u-limit") is False
