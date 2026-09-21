"""把主图事件流呈现为用户可见的 SSE 事件。

图的执行和状态合并只在 main_graph.iter_chat_events。这里负责：
- 工作线程桥接，避免 ASGI 逐次迭代拆掉 contextvars
- 没有真实 token 时，把最终答案切块打出
- 去掉 done 上的内部 result，不把路由细节发给浏览器
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator

from app.agents.main_graph import iter_chat_events

_EVENT = "event"
_ERROR = "error"
_END = "end"


def _smooth_stream(text: str, chunk_size: int = 2, delay: float = 0.025) -> Iterator[str]:
    """将文本切成小块并按固定间隔产出，模拟流式打字效果。"""
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
        if i + chunk_size < len(text):
            time.sleep(delay)


def _present(events: Iterator[dict]) -> Iterator[dict]:
    """补上展示用的打字块，并剥掉内部结果。"""
    saw_token = False
    for event in events:
        kind = event.get("type")
        if kind == "token":
            saw_token = True
            yield event
            continue
        if kind == "done":
            answer = event.get("final_answer") or ""
            if not saw_token and answer:
                for piece in _smooth_stream(answer):
                    yield {"type": "token", "content": piece}
            yield {key: value for key, value in event.items() if key != "result"}
            continue
        yield event


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
            for event in _present(iter_chat_events(user_query, conversation_id)):
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
