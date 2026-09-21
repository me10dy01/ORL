"""回答缓存（精确匹配）。

键 = `hash(模型名 + 归一化后的问题)`；命中后直接跳过整条 LLM 链路（实测可省 100s+）。

失效策略：
- 换模型 → 键含模型名，天然失效
- 知识库变化（上传/删除文档）→ 由 `management` 调用 `clear()` 主动清空
- 订单/库存类回答不写入本缓存（见 main_graph.cache_store_node）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata

from sqlalchemy import Integer, cast, delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[？?。.！!，,、；;：:\s]+$")


def normalize(question: str) -> str:
    """问题归一化：Unicode NFC、压缩空白、去掉结尾标点、小写化。"""
    text = unicodedata.normalize("NFC", (question or "").strip())
    text = _WS_RE.sub(" ", text)
    return _TRAILING_PUNCT_RE.sub("", text).strip().lower()


def make_key(question: str) -> str:
    """缓存键：模型名 + 归一化问题。"""
    raw = f"{settings.llm_model}|{normalize(question)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get(question: str) -> dict | None:
    """命中则返回 payload（并累加命中次数），未命中返回 None。"""
    key = make_key(question)
    try:
        with db.session_scope() as session:
            row = session.get(db.AnswerCache, key)
            if row is None:
                return None
            row.hits = (row.hits or 0) + 1
            try:
                payload = json.loads(row.payload)
            except (json.JSONDecodeError, TypeError):
                return None
            return payload if isinstance(payload, dict) else None
    except SQLAlchemyError:
        logger.warning("缓存读取失败（MySQL 不可用），降级为未命中", exc_info=True)
        return None


def put(question: str, payload: dict) -> None:
    """写入或覆盖缓存。"""
    key = make_key(question)
    encoded = json.dumps(payload, ensure_ascii=False)
    try:
        with db.session_scope() as session:
            row = session.get(db.AnswerCache, key)
            if row is None:
                session.add(
                    db.AnswerCache(cache_key=key, question=question, payload=encoded)
                )
            else:
                row.question = question
                row.payload = encoded
    except SQLAlchemyError:
        logger.warning("缓存写入失败（MySQL 不可用），跳过缓存", exc_info=True)


def clear() -> int:
    """清空缓存，返回清除条数。"""
    try:
        with db.session_scope() as session:
            count = session.scalar(select(func.count()).select_from(db.AnswerCache))
            session.execute(delete(db.AnswerCache))
            return count or 0
    except SQLAlchemyError:
        logger.warning("缓存清空失败（MySQL 不可用）", exc_info=True)
        return 0


def stats() -> dict:
    """缓存统计（条数 / 累计命中次数）。"""
    try:
        with db.session_scope() as session:
            entries = session.scalar(select(func.count()).select_from(db.AnswerCache))
            hits = session.scalar(
                select(cast(func.coalesce(func.sum(db.AnswerCache.hits), 0), Integer))
            )
            return {"entries": entries or 0, "hits": hits or 0}
    except SQLAlchemyError:
        logger.warning("缓存统计失败（MySQL 不可用）", exc_info=True)
        return {"entries": 0, "hits": 0}
