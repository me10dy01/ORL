"""Supervisor 轻量路由测试（模型职责重构）。

全部离线运行（0 次 LLM 调用）：
- RulePreCheck 在 20 题评测集上的覆盖率与准确率
- 路由一致性与任务完整性校验（_validate_routing）
- auto 模式升级链（monkeypatch _route_by_llm，不真实调用模型）
- rules_only / llm 模式行为
"""

from __future__ import annotations

import pytest

from app.agents import supervisor
from app.agents.supervisor import (
    RoutingResult,
    Task,
    _route_by_llm,
    _validate_routing,
    route_query,
    rule_route,
)
from app.seed import load_test_questions

# 规则必须"判了就对"：判定过的题目路由必须与期望一致
# （评测集 20 题中规则预期覆盖 ≥ 15 题，其余交给 LLM Router）


def test_rule_route_precision_on_eval_dataset():
    """规则在评测集上：覆盖率达标且零误判。"""
    questions = load_test_questions()
    decided = []
    for q in questions:
        result = rule_route(q.question)
        if result is not None:
            decided.append((q, result[0]))
    # 覆盖率：至少 15/20 由规则判定（其余走 LLM 升级链）
    assert len(decided) >= 15, f"规则覆盖率不足: {len(decided)}/20"
    # 准确率：规则判定的题目必须全部正确（判了就错比不判更糟）
    wrong = [
        (q.id, got, q.expected_route)
        for q, got in decided
        if got != q.expected_route
    ]
    assert not wrong, f"规则误判: {wrong}"


def test_rule_route_task_queries():
    """规则路径构造的任务：mixed 双任务、单意图单任务、query 非空。"""
    result = rule_route("K87 键盘多少钱？能退货吗？")
    assert result is not None and result[0] == "mixed"
    tasks = supervisor._tasks_for(result[0], "K87 键盘多少钱？能退货吗？")
    assert {t.agent for t in tasks} == {"rag", "tool"}
    assert all(t.query.strip() for t in tasks)

    tasks_rag = supervisor._tasks_for("rag", "退货政策是什么")
    assert [t.agent for t in tasks_rag] == ["rag"]


# ---- _validate_routing ----


def _routing(route: str, agents: list[str]) -> RoutingResult:
    return RoutingResult(
        route=route,  # type: ignore[arg-type]
        reason="test",
        tasks=[Task(agent=a, query="q") for a in agents],  # type: ignore[arg-type]
    )


def test_validate_routing_task_completeness():
    assert _validate_routing("x", _routing("mixed", ["rag", "tool"])) is None
    # mixed 缺 tool 任务
    assert _validate_routing("x", _routing("mixed", ["rag"])) is not None
    # rag 路由缺少 rag 任务
    assert _validate_routing("x", _routing("rag", ["tool"])) is not None
    # 空任务查询
    bad = RoutingResult(
        route="rag", reason="t", tasks=[Task(agent="rag", query="  ")]
    )
    assert _validate_routing("x", bad) is not None


def test_validate_routing_entity_conflict():
    """任务书第十节：判 rag 但输入含订单号 → 冲突。"""
    assert (
        _validate_routing("查询 ORD20260001 政策", _routing("rag", ["rag"]))
        is not None
    )
    # 无实体的纯政策问题不冲突
    assert _validate_routing("退货政策是什么", _routing("rag", ["rag"])) is None


def test_validate_routing_policy_tool_conflict():
    """路由 tool 却含政策意图且无事实词/实体 → 冲突。"""
    assert (
        _validate_routing("退货政策是什么", _routing("tool", ["tool"])) is not None
    )
    # 带订单实体的 tool 不冲突
    assert (
        _validate_routing("查询 ORD20260001 状态", _routing("tool", ["tool"])) is None
    )


# ---- route_query 决策链（mock LLM，不真实调用） ----


def _fake_llm(route: str, calls: list[str]):
    """构造替身 _route_by_llm：记录使用的提示词路径，返回固定路由。"""

    def fake(
        query: str, system_prompt: str, path: str, reasoning: bool = False
    ) -> RoutingResult:
        calls.append(path)
        tasks = [Task(agent="rag", query=query)] if route == "rag" else [
            Task(agent="rag", query=query),
            Task(agent="tool", query=query),
        ]
        return RoutingResult(route=route, reason="fake", tasks=tasks, decision_path=path)  # type: ignore[arg-type]

    return fake


def test_route_query_rules_short_circuit(monkeypatch):
    """规则能判定的题目直接返回，不调用任何 LLM。"""
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "_route_by_llm", _fake_llm("rag", calls))
    result = route_query("查询订单 ORD20260001 的状态")
    assert result.route == "tool"
    assert result.decision_path == "rules"
    assert calls == []


def test_route_query_escalation_on_unknown(monkeypatch):
    """规则判定不了 → 升级 LLM Router（短提示词）。"""
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "_route_by_llm", _fake_llm("rag", calls))
    result = route_query("你们商城卖手机吗？")
    assert result.route == "rag"
    assert result.decision_path == "llm_short"
    assert calls == ["llm_short"]


def test_route_query_conflict_escalates_to_full_prompt(monkeypatch):
    """短提示词输出结构无效（mixed 缺 tool 任务）→ 用完整提示词复判。"""
    calls: list[str] = []
    # 规则判定不了的查询（无实体无关键词）才会进入 LLM 升级链
    seq = iter(["short", "full"])

    def fake(
        query: str, system_prompt: str, path: str, reasoning: bool = False
    ) -> RoutingResult:
        next(seq)
        calls.append(path)
        if path == "llm_short":
            # 结构性无效：声称 mixed 但只有 rag 任务 → 校验失败 → 升级
            return RoutingResult(
                route="mixed", reason="fake", tasks=[Task(agent="rag", query=query)],
                decision_path=path,
            )
        return RoutingResult(
            route="rag", reason="fake", tasks=[Task(agent="rag", query=query)],
            decision_path=path,
        )

    monkeypatch.setattr(supervisor, "_route_by_llm", fake)
    result = route_query("你们商城卖手机吗？")
    assert calls == ["llm_short", "llm_full"]
    assert result.route == "rag"
    assert result.decision_path == "llm_full"


def test_route_query_fallback_after_repeated_conflict(monkeypatch):
    """两级 LLM 输出都与规则/结构校验冲突 → 保守 fallback rag。"""
    calls: list[str] = []

    def broken(
        query: str, system_prompt: str, path: str, reasoning: bool = False
    ) -> RoutingResult:
        calls.append(path)
        return RoutingResult(
            route="mixed", reason="fake", tasks=[Task(agent="rag", query=query)],
            decision_path=path,
        )

    monkeypatch.setattr(supervisor, "_route_by_llm", broken)
    result = route_query("你们商城卖手机吗？")
    assert calls == ["llm_short", "llm_full"]
    assert result.route == "rag"
    assert result.decision_path == "fallback"
    assert result.tasks and result.tasks[0].agent == "rag"


def test_route_query_legacy_entity_conflict_fallback(monkeypatch):
    """llm 模式安全网：LLM 判 rag 但输入含订单号 → 冲突 → fallback。"""
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "_route_by_llm", _fake_llm("rag", calls))
    monkeypatch.setattr(supervisor.settings, "router_mode", "llm")
    result = route_query("ORD20260001 到哪了？")
    # legacy 模式只有一级 LLM 调用：full 输出与实体冲突 → 直接 fallback
    assert calls == ["llm_full"]
    assert result.decision_path == "fallback"
    assert result.route == "rag"


def test_route_query_llm_exception_falls_through(monkeypatch):
    """LLM 调用异常 → 逐级降级，最终 fallback，不抛异常。"""

    def boom(
        query: str, system_prompt: str, path: str, reasoning: bool = False
    ) -> RoutingResult:
        raise RuntimeError("ollama down")

    monkeypatch.setattr(supervisor, "_route_by_llm", boom)
    result = route_query("你们商城卖手机吗？")
    assert result.decision_path == "fallback"
    assert result.route == "rag"


def test_route_query_rules_only_mode(monkeypatch):
    """rules_only：规则判定不了时保守 rag，绝不调用 LLM。"""
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "_route_by_llm", _fake_llm("rag", calls))
    monkeypatch.setattr(supervisor.settings, "router_mode", "rules_only")
    hit = route_query("K87 机械键盘现在多少钱？")
    assert hit.decision_path == "rules" and hit.route == "tool"
    miss = route_query("你们商城卖手机吗？")
    assert miss.decision_path == "fallback" and miss.route == "rag"
    assert calls == []


def test_route_query_legacy_llm_mode(monkeypatch):
    """llm 模式（旧行为）：跳过规则，始终用完整提示词调一次 LLM。"""
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "_route_by_llm", _fake_llm("mixed", calls))
    monkeypatch.setattr(supervisor.settings, "router_mode", "llm")
    result = route_query("退货政策是什么")
    assert result.route == "mixed"
    assert result.decision_path == "llm_full"
    assert calls == ["llm_full"]


def test_route_query_with_qa_feedback_skips_rules(monkeypatch):
    """QA 重规划：带质检反馈时跳过规则，直接 LLM 重新规划。"""
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "_route_by_llm", _fake_llm("mixed", calls))
    result = route_query(
        "退货政策是什么", qa_feedback=["未调用业务工具但问题涉及订单"]
    )
    assert result.route == "mixed"
    assert result.decision_path == "llm_short"
    assert calls == ["llm_short"]
