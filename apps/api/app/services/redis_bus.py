"""Redis 的两个用途：聊天限流 + 工单变更广播（pub/sub）。

设计基调是 fail-open：Redis 挂了/没装，限流放行、广播静默跳过，
绝不让基础设施故障拖垮主流程。代价是限流可能失效——面试可聊这个 tradeoff。
"""

import time
from typing import Any

from app.config import get_settings

CHANNEL = "tickets"
# 进程级懒加载单例；redis 是可选依赖，import 失败也允许跑。
_client: Any = None


def _redis():
    """懒加载连接：首次使用时建连并 ping 验证，失败则全程降级为 None。"""
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
    """广播工单变更（routes 里建单/认领/回复/关单后调用）。失败静默。"""
    client = _redis()
    if client is None:
        return
    try:
        client.publish(CHANNEL, message)
    except Exception:
        pass


def listen():
    """坐席端 /api/tickets/events 的 SSE 数据源；不可用时返回 None 走兜底。"""
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
    """Return False when the user exceeded the per-minute chat quota. Fails open.

    固定窗口计数器：key 里带时间桶（rate:chat:{user}:{窗口序号}），
    INCR 首访时设 EXPIRE 自动清理。Redis 异常时放行——可用性优先于限流精度。
    """
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
