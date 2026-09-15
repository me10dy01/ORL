"""Reranker（Phase 10 / 模型职责重构）。

模型职责分离：Reranker 只负责"对候选文档重新排序"，不生成答案、不做路由。

可插拔接口（任务书第五节）：

    class Reranker(Protocol):
        def rerank(self, query, documents, top_n) -> list[dict]: ...

三种实现：
- ``OnnxCrossEncoderReranker``（默认）：**专业 cross-encoder 重排模型**
  （BAAI bge-reranker-base 的 ONNX int8 版，约 279MB），**CPU 推理、零显存占用**，
  不与 Qwen3 8B 抢显存；单对判定约 5ms。
- ``LLMReranker``：Qwen3 8B LLM-as-Reranker（旧行为，作为兜底）。
- ``NoopReranker``：不重排（透传，用于对照实验）。

为什么不用 Ollama 跑 Qwen3-Reranker-0.6B（负结论 N4）：
Ollama 0.34 无原生 rerank 端点，社区 GGUF 以普通因果 LM 方式加载后
打分头不可用（实测输出为标点乱码、logprobs 全 0），无法得到相关度分数。
故改用 ONNX 运行真正的 cross-encoder，且复用环境已有的
onnxruntime / tokenizers / huggingface_hub（chromadb 传递依赖），不新增任何 pip 依赖。

模型不可用（首次下载失败 / 离线）时自动降级到 LLMReranker 并告警，
保证 RAG 链路永不因重排失败而中断。
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Protocol, cast, runtime_checkable

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import profiling
from app.config import get_settings
from app.llm import get_structured_model

logger = logging.getLogger(__name__)
settings = get_settings()

SYSTEM_PROMPT = "你是检索重排助手，只按相关度排序候选片段，不生成其他内容。"

# 送入重排器的片段截断长度（控制输入规模与耗时）
_MAX_DOC_CHARS = 300


class RerankResult(BaseModel):
    """LLM Reranker 的结构化输出。"""

    indices: list[int] = Field(description="候选片段按相关度从高到低排序的编号列表")


@runtime_checkable
class Reranker(Protocol):
    """重排器接口：对候选文档按与查询的相关度重新排序。"""

    def rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        """返回按相关度降序排列的前 top_n 个候选。"""
        ...


# ---------- 实现 1：专业 cross-encoder（ONNX，CPU，零显存） ----------


class OnnxCrossEncoderReranker:
    """基于 ONNX cross-encoder 的重排器（默认实现）。

    支持两类打分约定，按 ONNX 输出形状自动识别：

    - ``sigmoid``（bge-reranker 系列）：输出 ``(batch, 1)`` 相关性 logit，
      经 sigmoid 得到 0~1 分数。
    - ``yes_no``（Qwen3-Reranker 系列）：输出 ``(batch, seq, vocab)`` 的因果 LM
      logits，取末尾位置 "yes" / "no" 两个 token 的 logit 差再过 sigmoid。

    模型与分词器**懒加载**（首次 rerank 时才加载），避免拖慢应用启动与单测。
    分数相同时保持向量召回顺序（稳定排序）。
    """

    def __init__(
        self,
        model_id: str | None = None,
        onnx_file: str | None = None,
        max_length: int | None = None,
        scoring: str | None = None,
        instruct: str | None = None,
    ) -> None:
        self.model_id = model_id or settings.reranker_model
        self.onnx_file = onnx_file or settings.reranker_onnx_file
        self.max_length = max_length or settings.reranker_max_length
        self.scoring = scoring or settings.reranker_scoring
        self.instruct = instruct or settings.reranker_instruct
        self._session = None
        self._tokenizer = None
        self._resolved_scoring: str | None = None
        self._yes_id: int | None = None
        self._no_id: int | None = None
        self._past_inputs: list[str] = []
        self._lock = threading.Lock()

    # ---- 懒加载 ----

    def _ensure_loaded(self) -> None:
        """懒加载 ONNX 会话与分词器（线程安全）。"""
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            session = ort.InferenceSession(
                hf_hub_download(self.model_id, self.onnx_file),
                providers=["CPUExecutionProvider"],
            )
            tokenizer = Tokenizer.from_file(
                hf_hub_download(self.model_id, "tokenizer.json")
            )
            tokenizer.enable_truncation(max_length=self.max_length)

            # 先按输出形状解析打分方式，再依此准备分词器与 token id
            self._session = session
            resolved = self._resolve_scoring()
            self._resolved_scoring = resolved
            if resolved == "yes_no":
                self._yes_id = tokenizer.token_to_id("yes")
                self._no_id = tokenizer.token_to_id("no")
            else:
                # 序列分类模型需要 padding 才能批量推理
                pad_id = tokenizer.token_to_id("<pad>")
                tokenizer.enable_padding(
                    pad_id=pad_id if pad_id is not None else 1, pad_token="<pad>"
                )

            self._past_inputs = [
                i.name
                for i in session.get_inputs()
                if i.name.startswith("past_key_values")
            ]
            self._tokenizer = tokenizer
            logger.info(
                "cross-encoder 重排器已加载：%s（%s，scoring=%s）",
                self.model_id,
                self.onnx_file,
                resolved,
            )

    def _resolve_scoring(self) -> str:
        """确定打分方式：配置显式指定，或按输出形状自动识别。"""
        if self._resolved_scoring is not None:
            return self._resolved_scoring
        if self.scoring in {"sigmoid", "yes_no"}:
            return self.scoring
        if self._session is None:
            # 未加载时无法探测形状，按配置意图保守返回（加载后会重新解析）
            return "sigmoid"
        # auto：输出 3 维（batch, seq, vocab）判为因果 LM 的 yes/no 模式
        return "yes_no" if len(self._session.get_outputs()[0].shape) == 3 else "sigmoid"

    # ---- 打分 ----

    def _build_yes_no_prompt(self, query: str, content: str) -> str:
        """Qwen3-Reranker 官方判定模板（只回答 yes / no）。"""
        return (
            "<|im_start|>system\n"
            "Judge whether the Document meets the requirements based on the Query and the "
            'Instruct provided. Note that the answer can only be "yes" or "no".'
            "<|im_end|>\n<|im_start|>user\n"
            f"<Instruct>: {self.instruct}\n"
            f"<Query>: {query}\n"
            f"<Document>: {content[:_MAX_DOC_CHARS]}"
            "<|im_end|>\n<|im_start|>assistant\n"
        )

    def _score_batch(self, query: str, documents: list[dict]) -> list[float]:
        """按 sigmoid 约定批量打分（bge-reranker：输出 (batch, 1)）。"""
        encoded = self._tokenizer.encode_batch(
            [(query, d.get("content", "")[:_MAX_DOC_CHARS]) for d in documents]
        )
        feeds = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array(
                [e.attention_mask for e in encoded], dtype=np.int64
            ),
        }
        input_names = {i.name for i in self._session.get_inputs()}
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.array(
                [e.type_ids for e in encoded], dtype=np.int64
            )
        logits = self._session.run(["logits"], feeds)[0].reshape(-1)
        return [1.0 / (1.0 + math.exp(-float(x))) for x in logits]

    def _score_one_yes_no(self, query: str, content: str) -> float:
        """按 yes/no 约定单条打分（Qwen3-Reranker：取末尾位置 yes/no logit 差）。"""
        if self._yes_id is None or self._no_id is None:
            raise RuntimeError("yes/no token id 未解析，无法打分")
        ids = self._tokenizer.encode(
            self._build_yes_no_prompt(query, content), add_special_tokens=False
        ).ids
        length = len(ids)
        feeds = {
            "input_ids": np.array([ids], dtype=np.int64),
            "attention_mask": np.ones((1, length), dtype=np.int64),
            "position_ids": np.arange(length, dtype=np.int64)[None, :],
        }
        # 因果 LM 导出需显式传入空的 KV cache
        for name in self._past_inputs:
            feeds[name] = np.zeros((1, 8, 0, 128), dtype=np.float32)
        logits = np.asarray(self._session.run(["logits"], feeds)[0])[0, -1, :]
        diff = float(logits[self._yes_id]) - float(logits[self._no_id])
        # 数值稳定：避免 exp 溢出
        if diff >= 0:
            return 1.0 / (1.0 + math.exp(-min(diff, 60.0)))
        exp_d = math.exp(max(diff, -60.0))
        return exp_d / (1.0 + exp_d)

    def score(self, query: str, documents: list[dict]) -> list[float]:
        """批量打分：返回与 documents 等长的相关度分数（0~1）。"""
        self._ensure_loaded()
        with profiling.stage("reranker"):
            if self._resolve_scoring() == "yes_no":
                # 各候选长度不一，逐条推理（避免右 padding 影响末尾位置取 logits）
                return [
                    self._score_one_yes_no(query, d.get("content", ""))
                    for d in documents
                ]
            return self._score_batch(query, documents)

    def rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        if len(documents) <= top_n:
            return documents[:top_n]
        scores = self.score(query, documents)
        order = sorted(range(len(documents)), key=lambda i: (-scores[i], i))
        return [documents[i] for i in order[:top_n]]


# ---------- 实现 2：LLM-as-Reranker（Qwen3 8B，兜底） ----------


class LLMReranker:
    """用 Qwen3 8B 一次性输出候选编号顺序（旧行为）。"""

    def rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        if not documents:
            return []
        if len(documents) <= top_n:
            return documents[:top_n]

        numbered = "\n\n".join(
            f"[{i}] {c['content'][:_MAX_DOC_CHARS]}" for i, c in enumerate(documents)
        )
        prompt = (
            f"查询：{query}\n\n候选片段：\n{numbered}\n\n"
            "请按与查询的相关度从高到低，输出候选片段的编号列表。"
        )
        llm = get_structured_model().with_structured_output(RerankResult)
        with profiling.stage("reranker"):
            profiling.mark("llm_calls")
            result = cast(
                RerankResult,
                llm.invoke(
                    [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
                ),
            )

        order = _validate_order(result.indices, len(documents))
        return [documents[i] for i in order[:top_n]]


# ---------- 实现 3：不重排（对照） ----------


class NoopReranker:
    """透传：直接取前 top_n 个候选（用于对照实验）。"""

    def rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        return documents[:top_n]


# ---------- 降级包装：cross-encoder 失败时回落 LLM Reranker ----------


class _FallbackReranker:
    """主重排器失败时自动降级：主实现 → 兜底实现 → 不重排（透传）。

    三级链路保证 RAG 永不因重排失败而中断。实测 LLM 兜底自身也可能失败
    （Qwen3 为思考型模型，思考 token 吃满 num_predict 时结构化输出为空），
    因此必须有最后一级安全出口。
    """

    def __init__(
        self, primary: Reranker, fallback: Reranker, safe: Reranker | None = None
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._safe = safe or NoopReranker()

    def rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        try:
            return self._primary.rerank(query, documents, top_n)
        except Exception as exc:
            logger.warning(
                "重排器 %s 调用失败，降级到兜底实现：%s",
                type(self._primary).__name__,
                exc,
            )
        try:
            return self._fallback.rerank(query, documents, top_n)
        except Exception as exc:
            logger.warning(
                "兜底重排器 %s 亦失败，退化为向量召回顺序（不重排）：%s",
                type(self._fallback).__name__,
                exc,
            )
            return self._safe.rerank(query, documents, top_n)


# ---------- 工厂 ----------

_reranker_cache: dict[str, Reranker] = {}


def get_reranker(impl: str | None = None) -> Reranker:
    """按配置返回重排器（带缓存，避免重复加载 ONNX 模型）。

    默认 `llm`（Qwen3 8B LLM-as-Reranker）：本知识库实测 MRR 1.00，优于
    cross-encoder 的 0.80，且复用已驻留显存的 8B、不额外占显存。
    `cross_encoder`（专业 ONNX cross-encoder，CPU 推理零显存）可按需切换。

    所有实现都套一层失败兜底，保证 RAG 不因重排失败而中断。
    """
    name = impl or settings.reranker_impl
    if name not in _reranker_cache:
        if name == "none":
            _reranker_cache[name] = NoopReranker()
        elif name == "llm":
            _reranker_cache[name] = _FallbackReranker(LLMReranker(), NoopReranker())
        else:
            _reranker_cache[name] = _FallbackReranker(
                OnnxCrossEncoderReranker(), LLMReranker(), NoopReranker()
            )
    return _reranker_cache[name]


def rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    """兼容入口（保留旧签名）：按配置使用当前重排器。"""
    if not candidates:
        return []
    return get_reranker().rerank(query, candidates, top_n)


def _validate_order(indices: list[int], n: int) -> list[int]:
    """过滤非法/重复编号，并补齐缺失编号，保证返回顺序完整。"""
    seen: set[int] = set()
    order: list[int] = []
    for i in indices:
        if 0 <= i < n and i not in seen:
            order.append(i)
            seen.add(i)
    for i in range(n):
        if i not in seen:
            order.append(i)
    return order


__all__ = [
    "Reranker",
    "OnnxCrossEncoderReranker",
    "LLMReranker",
    "NoopReranker",
    "get_reranker",
    "rerank",
]
