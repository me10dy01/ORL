"""Streaming（Phase 12 / 模型职责重构）。

把 LangGraph 内部事件映射为用户可见状态，内部事件与用户可见事件分离
（AGENTS.md 第 19 条）。

事件协议：
    {"type": "status", "message": "正在分析问题"}
    {"type": "tool", "name": "get_order", "args": {...}}
    {"type": "token", "content": "..."}      # 最终回答的 token 流
    {"type": "done", "final_answer": ..., "citations": [...], ...}
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator

from app import profiling
from app.agents.main_graph import _initial_state, main_graph

# 节点名 → 用户可见状态（仅暴露有意义的状态，不暴露内部节点）
NODE_STATUS = {
    "rewrite": "正在理解上下文",
    "supervisor": "正在分析问题",
    "replan": "正在重新规划任务",
    "rag": "正在检索知识库",
    "tool": "正在查询业务数据",
    "synthesis": "正在生成回答",
    "qa": "正在验证回答",
}

# 工作线程 → 主线程的队列消息标记
_EVENT = "event"
_ERROR = "error"
_END = "end"


def _smooth_stream(text: str, chunk_size: int = 2, delay: float = 0.025) -> Iterator[str]:
    """将文本切成小块并按固定间隔产出，模拟流式打字效果。"""
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
        if i + chunk_size < len(text):
            time.sleep(delay)


def _stream_events(user_query: str, conversation_id: str) -> Iterator[dict]:
    """主图流式执行（在单一上下文中跑完，保证 profiling 埋点完整）。"""
    initial = _initial_state(user_query, conversation_id)
    state: dict = dict(initial)
    any_token = False

    with profiling.profiler_scope() as prof:
        for namespace, mode, chunk in main_graph.stream(
            initial,
            stream_mode=["updates", "messages", "custom"],
            subgraphs=True,
        ):
            # 子图内部事件：只透出 custom（工具）事件，其余不暴露给用户
            if namespace != ():
                if mode == "custom":
                    yield chunk
                continue

            if mode == "updates":
                for node, update in chunk.items():
                    if update:
                        state.update(update)
                    msg = NODE_STATUS.get(node)
                    if msg:
                        yield {"type": "status", "message": msg}
            elif mode == "messages":
                msg, meta = chunk
                if meta.get("langgraph_node") == "synthesis":
                    content = getattr(msg, "content", "") or ""
                    if content:
                        any_token = True
                        yield {"type": "token", "content": content}
            elif mode == "custom":
                yield chunk

        final_answer = state.get("final_answer", "")
        # synthesis 被跳过时无真实 token 事件，将最终答案平滑流式输出，避免整段答案突然闪现
        if not any_token and final_answer:
            for piece in _smooth_stream(final_answer):
                yield {"type": "token", "content": piece}

        # 注意：total_ms 含客户端消费 SSE 的间隔（背压），略高于服务端纯计算耗时
        yield {
            "type": "done",
            "final_answer": final_answer,
            "citations": state.get("citations", []),
            "qa_passed": (state.get("qa_result") or {}).get("passed", False),
            "retry_count": state.get("retry_count", 0),
            "degraded": state.get("degraded", False),
            "profile": prof.snapshot(),
        }


def stream_chat(user_query: str, conversation_id: str = "default") -> Iterator[dict]:
    """流式运行主图，依次产出状态、工具、答案 token、完成事件。

    为什么用工作线程 + 队列桥接：
    同步生成器会被 ASGI 服务器放入线程池**逐次迭代**，每次 `next()` 都运行在
    事件循环 Context 的**不同副本**中——这会让 contextvars 埋点在第二次迭代后
    全部失效（分阶段耗时丢失），并使 `reset(token)` 跨副本报错。
    把整条流放进一个工作线程后，埋点上下文完整、事件顺序不变。
    """
    messages: queue.Queue = queue.Queue()
    stop = threading.Event()

    def worker() -> None:
        try:
            for event in _stream_events(user_query, conversation_id):
                if stop.is_set():
                    break
                messages.put((_EVENT, event))
        except Exception as exc:  # 异常带回消费端重新抛出，保持原有错误语义
            messages.put((_ERROR, exc))
        finally:
            messages.put((_END, None))

    thread = threading.Thread(target=worker, name="chat-stream", daemon=True)
    thread.start()
    try:
        while True:
            kind, payload = messages.get()
            if kind == _END:
                break
            if kind == _ERROR:
                yield {"type": "error", "message": str(payload)}
                continue
            yield payload
    finally:
        stop.set()
