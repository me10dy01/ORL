"""Latency Profiling（性能剖析基础设施）。

记录一次请求内各阶段耗时（毫秒），阶段命名与评估口径对齐：
    precheck / router / rewrite / embedding / retrieval / reranker /
    rag_gen / tool_llm / tool_exec / synthesis / qa / cache_lookup / total
同时统计 llm_calls（LLM 调用次数），用于核对"每请求调用了多少次大模型"。

实现基于 contextvars：FastAPI 同步端点跑在线程池中，每个请求各自持有
独立 Profiler 互不串扰；mixed 并行的子线程通过 copy_context() 传播。
所有埋点入口（stage / mark）在无 Profiler 时为 no-op，因此对既有
单元测试与离线脚本零影响。
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator
from contextlib import contextmanager

_CURRENT: contextvars.ContextVar["Profiler | None"] = contextvars.ContextVar(
    "profiler", default=None
)

# 评估报告关注的阶段顺序（仅用于输出排序）
STAGE_ORDER = (
    "precheck",
    "router",
    "rewrite",
    "cache_lookup",
    "embedding",
    "retrieval",
    "reranker",
    "rag_gen",
    "tool_llm",
    "tool_exec",
    "synthesis",
    "qa",
    "qa_llm",
)


class Profiler:
    """收集单次请求的阶段耗时。同名阶段多次出现时累加（如多轮 LLM 调用）。"""

    def __init__(self) -> None:
        self._stages: dict[str, float] = {}
        self._t0 = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - start) * 1000)

    def add(self, name: str, ms: float) -> None:
        key = f"{name}_ms"
        self._stages[key] = self._stages.get(key, 0.0) + ms

    def mark(self, name: str) -> None:
        """记录事件次数（不计时），如 llm_calls。"""
        key = f"{name}_count"
        self._stages[key] = self._stages.get(key, 0) + 1

    def snapshot(self) -> dict[str, float]:
        data = dict(self._stages)
        data["total_ms"] = (time.perf_counter() - self._t0) * 1000
        return {k: round(v, 1) for k, v in data.items()}


def get_profiler() -> Profiler | None:
    return _CURRENT.get()


@contextmanager
def profiler_scope() -> Iterator[Profiler]:
    """创建并绑定当前请求的 Profiler。

    注意：同步生成器（SSE 流式端点）会被 Starlette 放线程池逐次迭代，
    每次 `next()` 运行在事件循环 Context 的不同副本中，因此 `reset(token)`
    可能跨副本失败（Token 由另一副本创建）。此时退化为直接置空，避免中断请求。
    """
    prof = Profiler()
    token = _CURRENT.set(prof)
    try:
        yield prof
    finally:
        try:
            _CURRENT.reset(token)
        except ValueError:
            _CURRENT.set(None)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """模块级埋点：有 Profiler 时计时，否则 no-op。"""
    prof = _CURRENT.get()
    if prof is None:
        yield
        return
    with prof.stage(name):
        yield


def mark(name: str) -> None:
    """模块级计数埋点：无 Profiler 时 no-op。"""
    prof = _CURRENT.get()
    if prof is not None:
        prof.mark(name)


def ordered_stages(profile: dict[str, float]) -> list[tuple[str, float]]:
    """按固定顺序返回 (阶段, 毫秒) 列表，未收录阶段排在后面。"""
    items: list[tuple[str, float]] = []
    for name in STAGE_ORDER:
        key = f"{name}_ms"
        if key in profile:
            items.append((name, profile[key]))
    for key, value in profile.items():
        if key.endswith("_ms") and key[: -len("_ms")] not in STAGE_ORDER:
            name = key[: -len("_ms")]
            if name != "total":  # total 单独输出，避免重复
                items.append((name, value))
    return items
