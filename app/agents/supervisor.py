"""Supervisor（Phase 07 / 模型职责重构）。

Supervisor 是**架构角色**（工作流控制与任务路由），不是某个具体模型。
它不直接回答用户问题，不访问 MySQL / Chroma（数据访问只属于 RAG/Tool Agent）。

决策链（Rules → Lightweight Router → Full Prompt → Fallback）：

    RulePreCheck（0 次 LLM：实体正则 + 意图关键词，毫秒级）
        ↓ 规则无法判定 / 结果与规则冲突
    Lightweight Router（精简提示词 + 结构化输出；ROUTER_LLM_MODEL 可指向小模型）
        ↓ 输出仍与规则冲突（schema 校验 / 一致性冲突）
    Full Prompt 复判（完整 SUPERVISOR_PROMPT，与旧版一致）
        ↓ 仍异常
    保守默认 rag（带完整任务，永不失败）

轻量 Router 只决定"走哪条流程"，不做事实判断；最终事实来自
MySQL / RAG Evidence，最终生成由 Answer LLM 负责。
"""

from __future__ import annotations

import re
from typing import Literal, cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import profiling
from app.agents.intent_keywords import (
    FACT_KEYWORDS,
    KNOWLEDGE_KEYWORDS,
    POLICY_KEYWORDS,
    PRODUCT_SHORT_QUERY_MAX_LEN,
    PRODUCT_WORDS,
    TOOL_KEYWORDS,
)
from app.config import get_settings
from app.llm import get_structured_model

settings = get_settings()

# 完整版 Supervisor 提示词（保留旧版语义，作为最终升级路径）
SUPERVISOR_PROMPT = (
    "你是智能客服系统的任务路由协调器（Supervisor）。"
    "根据用户问题判断任务类型并生成结构化路由，不要直接回答用户问题。\n"
    "判断规则：\n"
    "1. 仅涉及企业知识/政策（退货、发货、保修、会员、积分、运费等）→ 路由 rag。\n"
    "2. 仅需要查询业务数据（订单、商品、库存、用户订单等）→ 路由 tool。\n"
    "3. 同时包含【具体业务数据（订单号/商品/用户）】和【政策疑问（能不能退、什么时候发货、运费谁承担等）】"
    "→ 必须路由 mixed，同时生成 rag 和 tool 两个任务。\n"
    "4. 为每个任务生成清晰、可直接执行的查询，只保留用户原话里出现过的订单号、商品ID、用户ID。"
    "不要把下面示例中的编号写进任务。\n"
    "\n"
    "示例：\n"
    '- "商城退货政策是什么" → rag\n'
    '- "查询订单 ORD00000000 状态" → tool\n'
    '- "订单 ORD00000002 显示待发货，你们什么时候发货" → mixed（tool 查订单状态 + rag 查发货规则）\n'
    '- "K87 键盘多少钱，能退货吗" → mixed（tool 查商品价格 + rag 查退货政策）\n'
)

# 精简版路由提示词（Lightweight Router 用，输入 token 约为完整版的 1/5）
ROUTER_SHORT_PROMPT = (
    "你是任务路由器。只判断该把用户问题交给谁处理，不要回答问题本身。\n"
    "route 取值：rag=查企业政策/知识（退货、发货、保修、会员、积分）；"
    "tool=查业务数据（订单、库存、价格、用户订单）；mixed=两者都要。\n"
    "tasks 为每个任务的 {agent, query}。query 只能保留用户原话里的订单号、商品ID，不要抄示例编号。\n"
    '示例："K87 多少钱？能退货吗" → route=mixed, tasks=[tool 查K87价格, rag 查K87退货政策]\n'
    '"退货政策是什么" → route=rag\n'
    '"ORD00000000 到哪了" → route=tool'
)


# ---------- 结构化契约 ----------


class Task(BaseModel):
    """单个可执行任务。"""

    agent: Literal["rag", "tool"] = Field(description="执行该任务的 Agent：rag 或 tool")
    query: str = Field(description="该 Agent 需要执行的查询")


class RoutingResult(BaseModel):
    """Supervisor 的结构化路由结果。"""

    route: Literal["rag", "tool", "mixed"] = Field(description="路由类型")
    reason: str = Field(description="路由判断理由")
    tasks: list[Task] = Field(description="需要执行的任务列表")
    decision_path: str = Field(
        default="llm",
        description="决策路径：rules / llm_short / llm_full / fallback（供 profiling）",
    )


class _RouterOutput(BaseModel):
    """LLM Router 的结构化输出（不暴露 decision_path，由代码填充）。"""

    route: Literal["rag", "tool", "mixed"] = Field(description="路由类型")
    reason: str = Field(description="路由判断理由")
    tasks: list[Task] = Field(description="需要执行的任务列表")


# ---------- RulePreCheck（0 次 LLM） ----------

_ORDER_RE = re.compile(r"ORD\d{6,}", re.IGNORECASE)
_USER_RE = re.compile(r"U\d{3,4}", re.IGNORECASE)
_PRODUCT_RE = re.compile(r"P\d{3}", re.IGNORECASE)
# SKU 型号：字母开头 + 2~3 位数字（K87 / M27），词边界避免误伤英文单词
_SKU_RE = re.compile(r"\b[A-Z]\d{2,3}\b", re.IGNORECASE)

def _has_business_entity(query: str) -> bool:
    """是否出现订单号/用户ID/商品ID 等强业务实体。"""
    return bool(
        _ORDER_RE.search(query) or _USER_RE.search(query) or _PRODUCT_RE.search(query)
    )


def rule_route(query: str) -> tuple[str, str] | None:
    """规则前置判定（0 次 LLM，毫秒级）。

    返回 (route, reason)；返回 None 表示规则无法判定，需升级 LLM Router。
    """
    policy_hits = [k for k in POLICY_KEYWORDS if k in query]
    fact_hits = [k for k in FACT_KEYWORDS if k in query]
    tool_hits = [k for k in TOOL_KEYWORDS if k in query]
    knowledge_hits = [k for k in KNOWLEDGE_KEYWORDS if k in query]
    product_hits = [k for k in PRODUCT_WORDS if k in query]
    has_entity = _has_business_entity(query)

    # 强实体 + 政策意图 → mixed
    if has_entity and policy_hits:
        return "mixed", f"规则：业务实体 + 政策词{policy_hits}"
    # 强实体（订单/用户/商品ID）→ tool
    if has_entity:
        return "tool", "规则：检测到订单/用户/商品实体"
    # 价格/库存等业务事实（含 SKU 型号）→ tool
    if tool_hits:
        if policy_hits:
            return "mixed", f"规则：事实查询{fact_hits or tool_hits} + 政策词{policy_hits}"
        return "tool", f"规则：业务查询词{tool_hits}"
    # 企业信息类话题（门店/营业时间等）→ 知识库
    if knowledge_hits:
        return "rag", f"规则：企业信息类话题{knowledge_hits}"
    # 仅由商品词构成的短查询 → 商品检索（如"键盘"）
    if product_hits and not policy_hits and len(query) <= PRODUCT_SHORT_QUERY_MAX_LEN:
        return "tool", f"规则：纯商品词查询{product_hits}"
    if policy_hits:
        return "rag", f"规则：政策/知识词{policy_hits}"
    return None


def _tasks_for(route: str, query: str) -> list[Task]:
    """规则路径下按路由构造任务（子 Agent 拿完整查询，自行提取要素）。"""
    if route == "mixed":
        return [Task(agent="tool", query=query), Task(agent="rag", query=query)]
    if route == "tool":
        return [Task(agent="tool", query=query)]
    return [Task(agent="rag", query=query)]


def _validate_routing(query: str, result: RoutingResult) -> str | None:
    """路由一致性与任务完整性校验。

    返回冲突说明（应升级复判）；None 表示校验通过。
    只做结构层面判定，不判断事实。
    """
    # 1) 任务完整性：路由必须携带对应任务，任务 query 非空
    agents = {t.agent for t in result.tasks}
    if result.route == "mixed" and agents != {"rag", "tool"}:
        return "mixed 路由缺少 rag/tool 双任务"
    if result.route == "rag" and "rag" not in agents:
        return "rag 路由缺少 rag 任务"
    if result.route == "tool" and "tool" not in agents:
        return "tool 路由缺少 tool 任务"
    if any(not t.query.strip() for t in result.tasks):
        return "存在空任务查询"

    # 2) 一致性冲突：输入含强实体却路由 rag（任务书第十节示例）
    if result.route == "rag" and (
        _ORDER_RE.search(query) or _USER_RE.search(query) or _PRODUCT_RE.search(query)
    ):
        return "输入包含订单/用户/商品实体但路由为 rag"

    # 3) 一致性冲突：路由 tool 却带明确政策意图且无事实查询词
    if result.route == "tool":
        policy_hits = [k for k in POLICY_KEYWORDS if k in query]
        fact_hits = [k for k in FACT_KEYWORDS if k in query]
        if policy_hits and not fact_hits and not _has_business_entity(query):
            return f"输入含政策意图{policy_hits}但路由为 tool"
    return None


# ---------- LLM Router（轻量 / 完整两级） ----------


def _route_by_llm(
    query: str, system_prompt: str, path: str, reasoning: bool = False
) -> RoutingResult:
    """用当前 Router LLM 输出结构化路由（schema validation 由 pydantic 保证）。

    reasoning=False（默认）关闭思考：路由是分类任务，实测关闭后延迟
    6.1s → 1.1s 且不再失败；完整提示词复判一级传 reasoning=True 以保准确率
    （该级只在轻量级输出无效/冲突时才触发，成本可摊销）。
    """
    model_kwargs: dict = {}
    if settings.router_llm_model:
        model_kwargs["model"] = settings.router_llm_model
    llm = get_structured_model(reasoning=reasoning, **model_kwargs)
    with profiling.stage("router"):
        profiling.mark("llm_calls")
        output = cast(
            _RouterOutput,
            llm.with_structured_output(_RouterOutput).invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=query)]
            ),
        )
    return RoutingResult(
        route=output.route,
        reason=output.reason,
        tasks=output.tasks,
        decision_path=path,
    )


def _fallback(query: str, reason: str) -> RoutingResult:
    """兜底：保守走知识库（rag），保证路由永不失败。"""
    return RoutingResult(
        route="rag",
        reason=reason,
        tasks=[Task(agent="rag", query=query)],
        decision_path="fallback",
    )


def _feedback_prompt(qa_feedback: list[str] | None) -> str:
    """QA 重规划时的附加提示。"""
    if not qa_feedback:
        return ""
    return (
        "\n上一轮回答质检未通过，重新规划时请注意：\n"
        + "\n".join(f"- {i}" for i in qa_feedback)
    )


def route_query(
    user_query: str, qa_feedback: list[str] | None = None
) -> RoutingResult:
    """Supervisor 主入口：Rules → LightRouter → FullPrompt → Fallback。

    规则高置信直接路由；判定不了/冲突时升级轻量 Router；
    仍冲突时用完整提示词复判；最后保守 fallback。

    qa_feedback 非空时（QA FAIL 后重规划）跳过规则直判，由 LLM 结合
    质检反馈重新规划（规则对"上轮为什么不通过"无能为力）。
    """
    # ---- 1) RulePreCheck（0 次 LLM，毫秒级） ----
    if not qa_feedback:
        with profiling.stage("precheck"):
            rule = rule_route(user_query)
        if rule is not None:
            route, reason = rule
            candidate = RoutingResult(
                route=cast(Literal["rag", "tool", "mixed"], route),
                reason=reason,
                tasks=_tasks_for(route, user_query),
                decision_path="rules",
            )
            if _validate_routing(user_query, candidate) is None:
                return candidate
            # 规则结果与输入特征冲突 → 继续走 LLM Router 升级链

    extra = _feedback_prompt(qa_feedback)

    # ---- 2) Lightweight Router（精简提示词，关闭思考求快） ----
    try:
        candidate = _route_by_llm(
            user_query, ROUTER_SHORT_PROMPT + extra, "llm_short", reasoning=False
        )
    except Exception:  # 路由失败不能中断主流程，降级到完整提示词
        candidate = None
    if candidate is not None and _validate_routing(user_query, candidate) is None:
        return candidate

    # ---- 3) Full Prompt 复判（完整提示词 + 开启思考求准） ----
    try:
        candidate = _route_by_llm(
            user_query, SUPERVISOR_PROMPT + extra, "llm_full", reasoning=True
        )
    except Exception:
        candidate = None
    if candidate is not None and _validate_routing(user_query, candidate) is None:
        return candidate

    # ---- 4) Fallback：保守默认 rag ----
    return _fallback(user_query, "路由多级校验未通过，保守走知识库")
