"""API 路由（Phase 13）。

API 层不含核心业务逻辑，只做协议转换与编排：API → Service → Graph / RAG / Tool。
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app import cache, memory
from app.agents.main_graph import run_main_graph
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationBatchDeleteRequest,
    ConversationBatchDeleteResponse,
    ConversationDeleteResponse,
    ConversationDetail,
    ConversationInfo,
    ConversationRenameRequest,
    ConversationRenameResponse,
    DocumentDeleteResponse,
    DocumentInfo,
    DocumentUpdateResponse,
    DocumentUploadResponse,
    FaqItem,
    SuggestRequest,
    SuggestResponse,
)
from app.faq import load_faq
from app.rag import management
from app.rag.loaders import SUPPORTED_HINT, is_supported
from app.streaming import stream_chat

router = APIRouter()


def _new_conversation_id() -> str:
    return f"conv-{uuid.uuid4().hex[:8]}"


# ---- Chat ----


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    cid = req.conversation_id or _new_conversation_id()
    result = run_main_graph(req.message, conversation_id=cid)
    return ChatResponse(
        conversation_id=cid,
        route=result["route"],
        reason=result.get("reason", ""),
        tasks=result.get("tasks", []),
        route_decision=result.get("route_decision", ""),
        rewritten_query=result.get("rewritten_query", ""),
        final_answer=result["final_answer"],
        citations=result.get("citations", []),
        qa_passed=result.get("qa_passed", False),
        retry_count=result.get("retry_count", 0),
        degraded=result.get("degraded", False),
        profile=result.get("profile", {}),
    )


@router.post("/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    cid = req.conversation_id or _new_conversation_id()

    def event_gen():
        yield _sse({"type": "meta", "conversation_id": cid})
        for event in stream_chat(req.message, conversation_id=cid):
            yield _sse(event)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---- Documents ----


@router.get("/documents", response_model=list[DocumentInfo])
def list_documents() -> list[dict]:
    return management.list_documents()


MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)) -> dict:
    if not file.filename or not is_supported(file.filename):
        raise HTTPException(status_code=400, detail=f"仅支持 {SUPPORTED_HINT} 格式")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件大小不能超过 10MB")
    return management.ingest_document(file.filename, content)


@router.delete(
    "/documents/{document_id}", response_model=DocumentDeleteResponse
)
def delete_document_api(document_id: str) -> dict:
    """删除已入库文档（向量 + 无同名文档时的源文件）。"""
    return management.delete_document(document_id)


@router.put(
    "/documents/{document_id}", response_model=DocumentUpdateResponse
)
async def update_document_api(document_id: str, file: UploadFile = File(...)) -> dict:
    """替换文档内容（修改）：同名保持 document_id 不变，内容未变化时幂等返回。"""
    if not file.filename or not is_supported(file.filename):
        raise HTTPException(status_code=400, detail=f"仅支持 {SUPPORTED_HINT} 格式")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件大小不能超过 10MB")
    return management.update_document(document_id, file.filename, content)


# ---- Conversations ----


@router.get("/conversations", response_model=list[ConversationInfo])
def list_conversations() -> list[dict]:
    return memory.list_conversations()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str) -> dict:
    return {
        "conversation_id": conversation_id,
        "messages": memory.get_recent_messages(conversation_id, limit=50),
    }


@router.patch(
    "/conversations/{conversation_id}", response_model=ConversationRenameResponse
)
def rename_conversation(conversation_id: str, req: ConversationRenameRequest) -> dict:
    """重命名会话（仅标题）。"""
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    if not memory.rename_conversation(conversation_id, title):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"conversation_id": conversation_id, "title": title}


@router.delete(
    "/conversations/{conversation_id}", response_model=ConversationDeleteResponse
)
def delete_conversation(conversation_id: str) -> dict:
    """删除会话及其全部消息。"""
    return memory.delete_conversation(conversation_id)


@router.post(
    "/conversations/batch-delete", response_model=ConversationBatchDeleteResponse
)
def batch_delete_conversations(req: ConversationBatchDeleteRequest) -> dict:
    """批量删除会话（前端“全选删除”使用）。"""
    return memory.delete_conversations(req.conversation_ids)


# ---- FAQ ----


@router.get("/faq", response_model=list[FaqItem])
def list_faq() -> list[dict]:
    """常见问题列表（静态数据：不调用 LLM、不查数据库）。"""
    return load_faq()


# ---- Answer Cache ----


@router.get("/cache")
def cache_stats() -> dict:
    """回答缓存统计（条数 / 累计命中次数）。"""
    return cache.stats()


@router.delete("/cache")
def clear_cache() -> dict:
    """清空回答缓存。"""
    return {"cleared": cache.clear()}


# ---- Suggest ----


@router.post("/suggest", response_model=SuggestResponse)
def suggest_followup(req: SuggestRequest) -> dict:
    """根据对话上下文推荐后续问题。

    优先使用前端直传的 context_text（消除 MySQL 写入竞态），
    未提供时降级为从 MySQL 读取历史。
    """
    from app.llm import get_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage

    if req.context_text:
        history_text = req.context_text
    else:
        history = memory.get_recent_messages(req.conversation_id, limit=6)
        if not history:
            return {"questions": []}
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history)

    prompt = (
        f"以下是用户与客服的对话：\n{history_text}\n\n"
        "请根据对话内容，预测用户接下来最可能想问的 3 个问题。"
        "要求：\n"
        "1. 问题要简短自然，像真实用户会问的\n"
        "2. 与对话内容相关，不要重复已经回答过的问题\n"
        "3. 每行一个问题，不要编号，不要其他内容"
    )
    try:
        llm = get_chat_model(num_predict=200, temperature=0.3)
        response = llm.invoke([
            SystemMessage(content="你是问题推荐助手。根据对话历史预测用户后续可能的问题。只输出问题，每行一个。"),
            HumanMessage(content=prompt),
        ])
        content = response.content if isinstance(response.content, str) else str(response.content)
        questions = [q.strip() for q in content.strip().split("\n") if q.strip()]
        return {"questions": questions[:3]}
    except Exception:
        return {"questions": []}
