"""pytest 共享 fixture 与命令行选项。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import cache, memory
from app.rag import build_index
from app.scripts.init_db import ensure_initialized

# 测试产生的会话 id 前缀：测试结束后自动清理，
# 避免污染前端「历史会话」列表（演示会话 demo-* 与真实用户会话不受影响）
TEST_CONVERSATION_PREFIXES = (
    "mem-",
    "api-",
    "stream-",
    "e2e-",
    "cache-",
    "eval-",
    "t-",
    "perf-",
    "len-",
    "dbg-",
    "qa-",
)

_db_initialized = False


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="运行慢测试（涉及本地 LLM/Ollama 调用）",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """未指定 --run-slow 时自动跳过 slow 标记的测试。"""
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="需要 --run-slow 参数才运行慢测试")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(scope="session")
def initialized_db() -> None:
    """确保 MySQL 已建库、建表并灌入种子数据（会话级，只执行一次）。"""
    global _db_initialized
    _db_initialized = True
    ensure_initialized()
    # 清空回答缓存：测试应真实走一遍完整链路，不因缓存命中而跳过
    cache.clear()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_conversations() -> Iterator[None]:
    """会话结束后清理测试会话（保留演示会话与真实用户会话）。"""
    yield
    if not _db_initialized:
        return
    try:
        test_ids = [
            item["conversation_id"]
            for item in memory.list_conversations()
            if item["conversation_id"].startswith(TEST_CONVERSATION_PREFIXES)
        ]
        if test_ids:
            memory.delete_conversations(test_ids)
    except SQLAlchemyError as exc:  # 清理失败不能让测试结果失真，但必须显式告警
        print(f"\n[conftest] 测试会话清理失败: {exc}")


@pytest.fixture
def fresh_cache() -> None:
    """用例级清空回答缓存。

    需要“真实跑一遍链路”的测试（路由 / 流式 / 端到端）应依赖本 fixture，
    否则可能命中其他用例留下的缓存，导致跳过 Agent 而不报错。
    """
    cache.clear()


@pytest.fixture(scope="session")
def rag_index() -> dict:
    """构建（或复用）知识库索引（会话级，只执行一次）。"""
    return build_index()
