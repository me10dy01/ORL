"""统一的本地 LLM 构造入口。

集中管理推理参数（模型、temperature、num_ctx、num_predict、keep_alive），
避免各模块各自 `new ChatOllama` 导致参数不一致。

性能相关的两个关键参数：

- `num_ctx`：Ollama 对 qwen3 默认 32768，KV cache 约 4.8GB，会让 8GB 显卡装不下模型
  而卸载到 CPU（实测掉速数倍）；本场景 4096 足够。
- `num_predict`：单次生成的最大输出 token 数，**直接决定长文本生成的耗时上限**。

注意：Qwen3 的思考 token 同样计入 `num_predict`。生成类调用（RAG 生成、
Synthesis、Query Rewrite）保留思考；结构化判定类调用请使用
`get_structured_model()` 关闭思考，否则长提示下会耗尽预算并返回空输出。
"""

from __future__ import annotations

from typing import Any

from langchain_ollama import ChatOllama

from app.config import get_settings

settings = get_settings()


def get_chat_model(**overrides: Any) -> ChatOllama:
    """构造 ChatOllama（集中参数，可用 overrides 覆盖单项）。"""
    params: dict[str, Any] = {
        "model": settings.llm_model,
        "base_url": settings.ollama_base_url,
        "temperature": 0,
        "num_ctx": settings.ollama_num_ctx,
        "num_predict": settings.ollama_num_predict,
        "keep_alive": settings.ollama_keep_alive,
    }
    params.update(overrides)
    return ChatOllama(**params)


def get_structured_model(**overrides: Any) -> ChatOllama:
    """结构化输出专用构造入口：**关闭思考模式**，降低 num_predict。

    Qwen3 是思考型模型，思考 token 也计入 `num_predict`。实测长提示下
    （如 6 个候选片段的重排提示）思考会吃满 512 token 预算，
    导致 `done_reason=length` 且**可见输出为空**，`with_structured_output`
    随即抛 OutputParserException。结构化判定类调用（重排、逐句归因、质检）
    不需要长思考，关闭后可稳定返回且明显更快（重排 0.7s vs 超时失败）。

    结构化输出通常仅需 ~50 tokens，num_predict=128 足够且节省推理时间。
    """
    params: dict[str, Any] = {"reasoning": False, "num_predict": 128}
    params.update(overrides)
    return get_chat_model(**params)
