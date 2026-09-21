"""后续问题推荐用例服务。"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app import memory
from app.llm import get_chat_model

logger = logging.getLogger(__name__)


def suggest_questions(conversation_id: str, context_text: str | None = None) -> list[str]:
    """根据直传上下文或会话历史生成最多三条建议；失败时返回空列表。"""
    if context_text:
        history_text = context_text
    else:
        history = memory.get_recent_messages(conversation_id, limit=6)
        if not history:
            return []
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
        response = llm.invoke(
            [
                SystemMessage(
                    content="你是问题推荐助手。根据对话历史预测用户后续可能的问题。只输出问题，每行一个。"
                ),
                HumanMessage(content=prompt),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return [line.strip() for line in content.strip().split("\n") if line.strip()][:3]
    except Exception:
        logger.warning("后续问题推荐失败", exc_info=True)
        return []
