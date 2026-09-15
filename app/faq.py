"""常见问题（FAQ）。

对话页常驻的问题列表，纯静态数据：**不调用 LLM、不查数据库**，
仅在页面加载时读一次文件（进程内缓存），对对话延迟零影响。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FAQ_FILE = DATA_DIR / "faq.json"


@lru_cache
def load_faq() -> list[dict]:
    """读取常见问题（进程内缓存，只读一次文件）。

    文件缺失或格式损坏时返回空列表并记日志：
    FAQ 属于锦上添花，不应影响对话主流程。
    """
    if not FAQ_FILE.exists():
        return []
    try:
        with FAQ_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("常见问题加载失败: %s", exc)
        return []
    questions = data.get("questions") if isinstance(data, dict) else data
    return list(questions or [])
