"""QA 闭环测试（模型职责重构 Phase C）。

全部离线运行（monkeypatch QA 与子 Agent，不调用 LLM / 数据库）：

- QA PASS → finalize → cache_store（写缓存）
- QA FAIL 第 1 次 → synthesis 带反馈重生成
- QA FAIL 第 2 次 → 回 Supervisor 重规划（replan）→ 重跑子图 → 再合成
- 重试耗尽 → 安全降级回答（degraded=True），且**不写缓存**
"""

from __future__ import annotations

import importlib

import pytest

# 注意：app.agents 包把 main_graph 重导出为"编译后的图对象"，
# 因此用 importlib 取真正的模块来 monkeypatch 模块级依赖。
mg = importlib.import_module("app.agents.main_graph")
from app.agents.main_graph import (  # noqa: E402
    DEGRADED_ANSWER,
    MAX_RETRY,
    _qa_decision,
    _retry_decision,
    cache_store_node,
    replan_node,
    run_main_graph,
)


# ---- 决策函数（纯逻辑，无需 mock） ----


def test_qa_decision_pass():
    assert _qa_decision({"qa_result": {"passed": True}}) == "finalize"


def test_qa_decision_retry_then_degrade():
    """未过检：重试未达上限 → retry；达到上限 → degrade（不再 finalize 草稿）。"""
    assert _qa_decision({"qa_result": {"passed": False}, "retry_count": 0}) == "retry"
    assert _qa_decision({"qa_result": {"passed": False}, "retry_count": 1}) == "retry"
    assert _qa_decision(
        {"qa_result": {"passed": False}, "retry_count": MAX_RETRY}
    ) == "degrade"


def test_retry_decision_escalates_to_replan():
    """第 1 次重试重新合成；第 2 次回 Supervisor 重规划。"""
    assert _retry_decision({"retry_count": 1}) == "resynthesize"
    assert _retry_decision({"retry_count": 2}) == "replan"


# ---- 缓存守卫 ----


def test_cache_store_skips_failed_qa(monkeypatch):
    """QA 未通过的回答绝不写入缓存（防止污染后续请求）。"""
    called: list = []
    monkeypatch.setattr(mg.cache, "put", lambda *a, **k: called.append(a))
    mg.cache_store_node(
        {"user_query": "q", "final_answer": "答案", "qa_result": {"passed": False}}
    )
    assert called == []


def test_cache_store_writes_passed_qa(monkeypatch):
    called: list = []
    monkeypatch.setattr(mg.cache, "put", lambda *a, **k: called.append(a))
    mg.cache_store_node(
        {
            "user_query": "q",
            "route": "rag",
            "final_answer": "答案",
            "qa_result": {"passed": True},
        }
    )
    assert len(called) == 1


def test_cache_store_skips_degraded(monkeypatch):
    """降级回答不写缓存。"""
    called: list = []
    monkeypatch.setattr(mg.cache, "put", lambda *a, **k: called.append(a))
    mg.cache_store_node(
        {
            "user_query": "q",
            "final_answer": DEGRADED_ANSWER,
            "qa_result": {"passed": False},
            "degraded": True,
        }
    )
    assert called == []


def test_cache_store_skips_business_routes(monkeypatch):
    """订单和混合问答不进精确缓存，避免库存或订单状态变了仍返回旧答案。"""
    called: list = []
    monkeypatch.setattr(mg.cache, "put", lambda *a, **k: called.append(a))
    for route in ("tool", "mixed"):
        mg.cache_store_node(
            {
                "user_query": "q",
                "route": route,
                "final_answer": "答案",
                "qa_result": {"passed": True},
            }
        )
    assert called == []


def test_run_main_graph_reads_done_result(monkeypatch):
    """同步入口只消费同一次事件流，不再单独 invoke。"""
    monkeypatch.setattr(
        mg,
        "iter_chat_events",
        lambda *args, **kwargs: iter(
            [
                {"type": "status", "message": "正在分析问题"},
                {
                    "type": "done",
                    "final_answer": "答案",
                    "result": {"route": "rag", "final_answer": "答案"},
                },
            ]
        ),
    )
    assert run_main_graph("问题") == {"route": "rag", "final_answer": "答案"}


def test_cache_gate_ignores_non_rag_payload(monkeypatch):
    monkeypatch.setattr(
        mg.cache, "get", lambda *args, **kwargs: {"route": "tool", "final_answer": "旧订单"}
    )
    got = mg.cache_gate_node({"rewritten_query": "订单状态", "user_query": "订单状态"})
    assert got["cache_hit"] is False

    monkeypatch.setattr(
        mg.cache, "get", lambda *args, **kwargs: {"route": "rag", "final_answer": "退货政策"}
    )
    got = mg.cache_gate_node({"rewritten_query": "退货政策", "user_query": "退货政策"})
    assert got["cache_hit"] is True


# ---- replan 节点 ----


def test_replan_node_resets_evidence_and_carries_feedback(monkeypatch):
    """replan：清空旧证据、把质检反馈传给 Supervisor。"""
    captured: dict = {}

    def fake_route(query: str, qa_feedback=None):
        captured["query"] = query
        captured["feedback"] = qa_feedback

        class R:
            route = "mixed"
            reason = "重新规划"
            decision_path = "llm_short"
            tasks = [
                type("T", (), {"model_dump": lambda self: {"agent": "rag", "query": query}})(),
                type("T", (), {"model_dump": lambda self: {"agent": "tool", "query": query}})(),
            ]

        return R()

    monkeypatch.setattr(mg, "route_query", fake_route)
    update = replan_node(
        {
            "user_query": "原问题",
            "rewritten_query": "改写后问题",
            "qa_result": {"passed": False, "issues": ["缺少订单依据"]},
            "rag_result": {"answer": "旧答案"},
            "citations": [{"chunk_id": "c1"}],
            "tool_calls": [{"name": "get_order"}],
        }
    )
    assert captured["query"] == "改写后问题"
    assert captured["feedback"] == ["缺少订单依据"]
    assert update["route"] == "mixed"
    # 旧证据必须清空，避免污染新路由的合成
    assert update["rag_result"] == {} and update["tool_result"] == {}
    assert update["citations"] == [] and update["tool_calls"] == []
    assert "QA 重规划" in update["reason"]


# ---- 端到端闭环（mock 掉所有 LLM 调用与数据访问） ----


@pytest.fixture
def wired(monkeypatch):
    """把主图的关键外部依赖替换为可观测替身，验证图结构（非模型质量）。"""
    state = {"qa_calls": 0, "synth_calls": 0, "plans": []}
    # 路由：离线替身（真实 route_query 在带质检反馈时会调用 LLM）
    monkeypatch.setattr(
        mg,
        "route_query",
        lambda q, qa_feedback=None: type(
            "R",
            (),
            {
                "route": "tool",
                "reason": "测试路由",
                "decision_path": "rules",
                "tasks": [
                    type(
                        "T", (), {"model_dump": lambda self: {"agent": "tool", "query": q}}
                    )()
                ],
            },
        )(),
    )
    # QA 始终失败 → 走满重试链
    monkeypatch.setattr(
        mg,
        "rule_check",
        lambda *a, **k: None,  # 规则无法判定 → 交给 LLM
    )
    monkeypatch.setattr(
        mg,
        "validate_answer",
        lambda *a, **k: type(
            "QA",
            (),
            {
                "model_dump": lambda self: {
                    "passed": False,
                    "issues": ["仍缺少依据"],
                    "citation_valid": False,
                    "reason": "fail",
                }
            },
        )(),
    )
    monkeypatch.setattr(
        mg,
        "_synthesize",
        lambda *a, **k: f"草稿{state['synth_calls']}",
    )
    monkeypatch.setattr(mg, "run_rag_agent", lambda q: {"answer": "RAG 答案", "sources": []})
    monkeypatch.setattr(
        mg, "run_tool_agent", lambda *a, **k: {"answer": "工具答案", "tool_calls": [{"name": "get_order"}]}
    )
    # 图节点在编译时已绑定真实子图，必须替换子图内部的 LLM 依赖才能真正离线
    from langchain_core.messages import AIMessage

    ta = importlib.import_module("app.agents.tool_agent")

    monkeypatch.setattr(
        ta,
        "_get_llm",
        lambda: type(
            "FakeLLM",
            (),
            {"invoke": lambda self, messages: AIMessage(content="工具替身答案")},
        )(),
    )
    monkeypatch.setattr(mg.memory, "get_recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(mg.memory, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(mg.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(mg.cache, "put", lambda *a, **k: None)
    return state


def test_graph_degrades_after_max_retry(wired, monkeypatch):
    """QA 始终不过 → 重试耗尽 → 返回安全降级回答。"""
    result = run_main_graph("查询订单 ORD20260001 的状态", conversation_id="qa-1")
    assert result["degraded"] is True
    assert result["final_answer"] == DEGRADED_ANSWER
    assert result["qa_passed"] is False
    assert result["retry_count"] == MAX_RETRY


def test_graph_pass_path_not_degraded(wired, monkeypatch):
    """QA 通过 → 正常返回草稿，不降级。"""
    monkeypatch.setattr(
        mg,
        "validate_answer",
        lambda *a, **k: type(
            "QA",
            (),
            {
                "model_dump": lambda self: {
                    "passed": True,
                    "issues": [],
                    "citation_valid": True,
                    "reason": "ok",
                }
            },
        )(),
    )
    result = run_main_graph("查询订单 ORD20260001 的状态", conversation_id="qa-2")
    assert result["degraded"] is False
    assert result["final_answer"].startswith("草稿")
    assert result["qa_passed"] is True
    assert result["retry_count"] == 0
