"""API 请求 / 响应模型（Phase 13）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="用户消息")
    conversation_id: str | None = Field(
        default=None, description="会话ID，缺省自动生成"
    )


class ChatResponse(BaseModel):
    conversation_id: str
    route: str
    reason: str = ""
    tasks: list[dict] = Field(default_factory=list)
    route_decision: str = ""
    rewritten_query: str = ""
    final_answer: str
    citations: list[dict] = Field(default_factory=list)
    qa_passed: bool = False
    retry_count: int = 0
    degraded: bool = False
    # 各阶段耗时（ms）与 LLM 调用次数，用于性能剖析
    profile: dict = Field(default_factory=dict)


class DocumentInfo(BaseModel):
    document_id: str
    file_name: str
    file_hash: str | None = None
    chunk_count: int


class DocumentUploadResponse(BaseModel):
    file_name: str
    documents: int
    chunks: int
    ingested_chunks: int


class DocumentDeleteResponse(BaseModel):
    document_id: str
    file_name: str | None = None
    deleted_chunks: int
    file_removed: bool = False


class DocumentUpdateResponse(BaseModel):
    changed: bool
    old_document_id: str
    new_document_id: str
    file_name: str | None = None
    deleted_chunks: int = 0
    ingested_chunks: int = 0
    documents: int = 0
    chunks: int = 0


class ConversationInfo(BaseModel):
    conversation_id: str
    message_count: int
    last_message_at: str | None = None
    title: str = ""


class ConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128, description="新的会话标题")


class ConversationRenameResponse(BaseModel):
    conversation_id: str
    title: str


class ConversationDeleteResponse(BaseModel):
    conversation_id: str
    deleted_messages: int


class ConversationBatchDeleteRequest(BaseModel):
    conversation_ids: list[str] = Field(min_length=1, description="要删除的会话ID列表")


class ConversationBatchDeleteResponse(BaseModel):
    deleted_conversations: int
    deleted_messages: int


class ConversationDetail(BaseModel):
    conversation_id: str
    messages: list[dict] = Field(default_factory=list)


class FaqItem(BaseModel):
    id: str
    question: str
    category: str = ""


class SuggestRequest(BaseModel):
    conversation_id: str = Field(description="当前会话 ID")
    context_text: str | None = Field(
        default=None, description="前端直传的对话上下文（user + assistant），有则优先使用"
    )


class SuggestResponse(BaseModel):
    questions: list[str] = Field(default_factory=list, description="推荐的后续问题")
