"""Phase 14 验收测试：前端页面与静态资源可访问，覆盖所需功能点。"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_page_served() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "智能客服" in resp.text
    assert "/static/app.js" in resp.text
    # Markdown 渲染依赖的本地 vendor 脚本
    assert "/static/vendor/marked.min.js" in resp.text
    assert "/static/vendor/purify.min.js" in resp.text


def test_admin_page_served() -> None:
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "知识库管理" in resp.text
    assert "/static/admin.js" in resp.text


def test_app_js_covers_required_features() -> None:
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    body = resp.text
    assert "chat/stream" in body  # streaming
    assert re.search(r"""case\s+['\"]token['\"]""", body)  # 流式 token 事件
    assert "renderCitations" in body  # citation
    assert "addToolChip" in body  # tool status
    assert "/conversations" in body  # conversation
    # Markdown 流式渲染 + XSS 防护
    assert "setMarkdown" in body
    assert "DOMPurify.sanitize" in body
    assert "DOMParser" in body
    assert "AbortController" in body  # 停止生成


def test_app_js_covers_conversation_management() -> None:
    body = client.get("/static/app.js").text
    assert "groupCitations" in body  # 引用按文档分组（避免同一文档重复展示）
    assert "renameConversation" in body  # 会话重命名
    assert "deleteConversation" in body  # 会话删除
    assert "toast" in body  # 非阻塞提示（替代 alert）


def test_session_restore_and_suggestions_handle_storage_and_races() -> None:
    """会话恢复降级安全，旧会话的推荐请求不能覆盖当前会话。"""
    body = client.get("/static/app.js").text
    assert "function getSessionStorage" in body
    assert "CONVERSATION_STORAGE_KEY" in body
    assert "suggestionRequestId" in body
    assert "cid !== conversationId" in body
    assert "renderSuggestions([]);" in body


def test_faq_panel_above_composer_focus_visible() -> None:
    """常见问题：位于输入框上方，默认隐藏，聚焦输入框时展示，点击填入输入框。"""
    html = client.get("/").text
    assert 'id="faq-panel"' in html
    assert "faq-panel hidden" in html  # 默认隐藏
    # 面板位于输入框（composer）之前
    assert html.index('id="faq-panel"') < html.index('class="composer"')

    body = client.get("/static/app.js").text
    assert "loadFaq" in body
    assert "/faq" in body
    assert "inputEl.value = question" in body  # 仅填入，不自动发送
    assert "setFaqVisible(true)" in body  # 聚焦时展示
    assert "setFaqVisible(false)" in body  # 失去焦点时收起


def test_app_js_initializes_in_stub_dom() -> None:
    """Node 冒烟：app.js 顶层初始化不抛异常，且选择器全部有效。

    回归背景：曾因 `querySelector` 漏写 `#`（写成 `querySelector("messages")`）
    导致 8 个元素均为 null、整个脚本失效（发送按钮 / FAQ / 会话列表全部不可用）。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("未安装 Node，跳过前端冒烟测试")
        return
    script = Path(__file__).parent / "smoke_app_js.cjs"
    result = subprocess.run(
        [node, str(script)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        f"app.js 冒烟测试失败:\n{result.stdout}\n{result.stderr}"
    )


def test_conversation_batch_delete_ui() -> None:
    """会话列表：勾选框、全选、删除选中。"""
    html = client.get("/").text
    assert 'id="conv-select-all"' in html
    assert 'id="conv-delete-selected"' in html

    body = client.get("/static/app.js").text
    assert "deleteSelectedConversations" in body
    assert "conversations/batch-delete" in body
    assert "selectedConversations" in body


def test_markdown_vendor_assets_served() -> None:
    marked = client.get("/static/vendor/marked.min.js")
    assert marked.status_code == 200
    assert "marked" in marked.text.lower()

    purify = client.get("/static/vendor/purify.min.js")
    assert purify.status_code == 200
    assert "dompurify" in purify.text.lower()


def test_admin_js_covers_upload() -> None:
    resp = client.get("/static/admin.js")
    assert resp.status_code == 200
    assert "/documents" in resp.text


def test_admin_document_management_ui() -> None:
    """管理页：文档列表带删除/修改按钮，修改经隐藏文件选择器提交。"""
    html = client.get("/admin").text
    assert 'id="doc-action-result"' in html  # 操作结果状态行
    assert 'id="modify-input"' in html  # 修改用的隐藏文件选择器
    assert "<th>操作</th>" in html

    body = client.get("/static/admin.js").text
    assert "deleteDocument" in body  # 删除
    assert "updateDocument" in body  # 修改
    assert "buildDeleteButton" in body
    assert "buildModifyButton" in body
    assert 'method: "DELETE"' in body
    assert 'method: "PUT"' in body
    assert "window.confirm" in body  # 删除前确认（与会话删除一致）


def test_admin_js_initializes_in_stub_dom() -> None:
    """Node 冒烟：admin.js 顶层初始化不抛异常，且选择器全部为页面真实 #id。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("未安装 Node，跳过前端冒烟测试")
        return
    script = Path(__file__).parent / "smoke_admin_js.cjs"
    result = subprocess.run(
        [node, str(script)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        f"admin.js 冒烟测试失败:\n{result.stdout}\n{result.stderr}"
    )


def test_style_css_served() -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert ".msg" in resp.text
    assert ".markdown-body" in resp.text
