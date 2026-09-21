"""API 路由。

只做 HTTP / SSE 协议转换。上传大小、标题和检索都不在这里。
"""

from __future__ import annotations

import json
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app import cache
from app.application import (
    chat_service,
    conversation_service,
    knowledge_service,
    suggestion_service,
)
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

router = APIRouter()


# ---- Chat ----


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    cid, result = chat_service.run_chat(req.message, req.conversation_id)
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
    cid, events = chat_service.stream_chat_events(req.message, req.conversation_id)

    def event_gen():
        yield _sse({"type": "meta", "conversation_id": cid})
        for event in events:
            yield _sse(event)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---- Documents ----


@router.get("/documents", response_model=list[DocumentInfo])
def list_documents() -> list[dict]:
    return knowledge_service.list_documents()


@router.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    return knowledge_service.ingest_document(file.filename or "", content)


@router.delete(
    "/documents/{document_id}", response_model=DocumentDeleteResponse
)
def delete_document_api(document_id: str) -> dict:
    """删除已入库文档（向量 + 无同名文档时的源文件）。"""
    return knowledge_service.delete_document(document_id)


@router.put(
    "/documents/{document_id}", response_model=DocumentUpdateResponse
)
async def update_document_api(document_id: str, file: UploadFile = File(...)) -> dict:
    """替换文档内容（修改）：同名保持 document_id 不变，内容未变化时幂等返回。"""
    content = await file.read()
    return knowledge_service.update_document(document_id, file.filename or "", content)


# ---- Conversations ----


@router.get("/conversations", response_model=list[ConversationInfo])
def list_conversations() -> list[dict]:
    return conversation_service.list_conversations()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str) -> dict:
    return conversation_service.get_conversation(conversation_id)


@router.patch(
    "/conversations/{conversation_id}", response_model=ConversationRenameResponse
)
def rename_conversation(conversation_id: str, req: ConversationRenameRequest) -> dict:
    """重命名会话（仅标题）。"""
    title = conversation_service.rename_conversation(conversation_id, req.title)
    if title is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"conversation_id": conversation_id, "title": title}


@router.delete(
    "/conversations/{conversation_id}", response_model=ConversationDeleteResponse
)
def delete_conversation(conversation_id: str) -> dict:
    """删除会话及其全部消息。"""
    return conversation_service.delete_conversation(conversation_id)


@router.post(
    "/conversations/batch-delete", response_model=ConversationBatchDeleteResponse
)
def batch_delete_conversations(req: ConversationBatchDeleteRequest) -> dict:
    """批量删除会话（前端“全选删除”使用）。"""
    return conversation_service.delete_conversations(req.conversation_ids)


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
    return {
        "questions": suggestion_service.suggest_questions(
            req.conversation_id, req.context_text
        )
    }
