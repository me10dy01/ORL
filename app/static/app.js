// 星辰数码商城智能客服 · 前端逻辑
// Chat + Streaming + Markdown 渲染 + Citation + Tool Status + Conversation
const API = "/api";
let conversationId = null;
let streaming = false;
let currentAbort = null;
let toastTimer = null;
let suggestionRequestId = 0;
const CONVERSATION_STORAGE_KEY = "orl_conversation_id";

const messagesEl = document.querySelector("#messages");
const statusEl = document.querySelector("#status");
const inputEl = document.querySelector("#input");
const sendBtn = document.querySelector("#send-btn");
const convListEl = document.querySelector("#conv-list");
const newConvBtn = document.querySelector("#new-conv-btn");
const faqListEl = document.querySelector("#faq-list");
const faqPanelEl = document.querySelector("#faq-panel");
const faqStaticSectionEl = document.querySelector("#faq-static-section");
const suggestListEl = document.querySelector("#suggest-list");
const suggestSectionEl = document.querySelector("#suggest-section");
const selectAllEl = document.querySelector("#conv-select-all");
const deleteSelectedBtn = document.querySelector("#conv-delete-selected");

// 会话多选状态（供“全选删除”使用）
const selectedConversations = new Set();
let displayedConvIds = [];
let baseFaqItems = [];
let lastAnswer = "";

// ---- Markdown 渲染（marked + DOMPurify，本地 vendor，无构建）----
if (window.marked) {
  marked.setOptions({ gfm: true, breaks: true });
}

// 唯一允许写入富文本的入口：先 DOMPurify 清洗，再用 DOMParser 转成 DOM 节点。
// 不使用 innerHTML（避免 XSS sink）；无 sanitizer 时降级为纯文本。
function setMarkdown(el, text) {
  if (!text) {
    el.replaceChildren();
    return;
  }
  if (!(window.marked && window.DOMPurify)) {
    el.textContent = text;
    return;
  }
  const safeHtml = DOMPurify.sanitize(marked.parse(text));
  const doc = new DOMParser().parseFromString(safeHtml, "text/html");
  el.replaceChildren(...Array.from(doc.body.childNodes));
}

// 流式渲染器：累积原文 + 节流重渲染（避免逐 token 全量重解析）
function createStreamRenderer(contentEl) {
  let raw = "";
  let timer = null;

  function flush() {
    setMarkdown(contentEl, raw);
  }

  return {
    push(token) {
      raw += token;
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        flush();
      }, 60);
    },
    finish(fallback) {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (!raw && fallback) raw = fallback;
      flush();
      return raw;
    },
    get text() {
      return raw;
    },
  };
}

// ---- 本地文件打开防护 ----
function guardLocalFile() {
  if (location.protocol !== "file:") return;
  const banner = document.createElement("div");
  banner.className = "warn-banner";
  banner.append("⚠ 检测到以本地文件方式打开页面，接口不可用。请先启动服务 ");
  const code = document.createElement("code");
  code.textContent = "uv run uvicorn app.main:app";
  banner.append(code, "，然后访问 ");
  const link = document.createElement("a");
  link.href = "http://127.0.0.1:8000/";
  link.textContent = "http://127.0.0.1:8000/";
  banner.append(link);
  document.body.prepend(banner);
}
guardLocalFile();

// ---- 基础工具 ----
function newConversationId() {
  return "web-" + Math.random().toString(36).slice(2, 10);
}

// sessionStorage 在隐私模式或受限 WebView 中可能不可用；会话恢复不能影响主对话。
function getSessionStorage() {
  try {
    return window.sessionStorage || null;
  } catch {
    return null;
  }
}

function saveConversationId(cid) {
  const storage = getSessionStorage();
  if (!storage) return;
  try {
    storage.setItem(CONVERSATION_STORAGE_KEY, cid);
  } catch {
    /* 存储不可写时仅跳过会话恢复，不影响当前页面。 */
  }
}

function clearSavedConversationId() {
  const storage = getSessionStorage();
  if (!storage) return;
  try {
    storage.removeItem(CONVERSATION_STORAGE_KEY);
  } catch {
    /* 存储不可写时无需阻塞新会话。 */
  }
}

function formatTime(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").slice(5, 16);
}

function scrollDown() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function clearHint() {
  const hint = messagesEl.querySelector(".empty-hint");
  if (hint) hint.remove();
}

function setStatus(text) {
  if (!text) {
    statusEl.textContent = "";
    statusEl.classList.add("hidden");
    return;
  }
  statusEl.textContent = text;
  statusEl.classList.remove("hidden");
}

// ---- 轻量提示（替代 alert，避免阻塞式弹窗）----
function toast(message, type = "info") {
  let el = document.querySelector("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.append(el);
  }
  el.textContent = message;
  el.className = `toast ${type} show`;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.className = "toast";
  }, 2600);
}

function setSendButton(stop) {
  sendBtn.textContent = stop ? "停止" : "发送";
  sendBtn.classList.toggle("danger", stop);
}

// ---- 消息渲染 ----
function addUserMessage(text) {
  clearHint();
  const div = document.createElement("div");
  div.className = "msg user";
  div.textContent = text;
  messagesEl.append(div);
  scrollDown();
}

function addAssistantBubble() {
  const div = document.createElement("div");
  div.className = "msg assistant";
  const content = document.createElement("div");
  content.className = "content markdown-body streaming";
  div.append(content);
  messagesEl.append(div);
  scrollDown();
  return div;
}

function addToolChip(bubble, name, args) {
  const chip = document.createElement("div");
  chip.className = "tool-chip";
  chip.textContent = `🔧 ${name}(${JSON.stringify(args)})`;
  bubble.append(chip);
  scrollDown();
}

// 按文档分组：同一文档的多个 chunk 合并为一张卡片，
// 避免“同一份知识库出现两次”（仅 chunk 顺序不同）造成的重复观感。
function groupCitations(citations) {
  const groups = new Map();
  (citations || []).forEach((c) => {
    const key = c.document_id || c.source || c.file_name || "unknown";
    if (!groups.has(key)) {
      groups.set(key, {
        file_name: c.file_name || c.source || "未知文档",
        document_id: c.document_id || "-",
        chunks: new Set(),
      });
    }
    if (c.chunk_id) groups.get(key).chunks.add(c.chunk_id);
  });
  return [...groups.values()];
}

function renderCitations(bubble, citations) {
  if (!citations || !citations.length) return;

  const groups = groupCitations(citations);
  const box = document.createElement("div");
  box.className = "citations";

  const title = document.createElement("div");
  title.className = "citations-title";
  title.textContent = `参考来源（${groups.length} 篇 / ${citations.length} 个片段）`;
  box.append(title);

  groups.forEach((g) => {
    const card = document.createElement("div");
    card.className = "citation-card";
    const name = document.createElement("strong");
    name.textContent = g.file_name;
    const doc = document.createElement("span");
    doc.textContent = `document_id: ${g.document_id}`;
    const chunks = document.createElement("span");
    chunks.textContent = `chunk_id: ${[...g.chunks].sort().join(", ") || "-"}`;
    card.append(name, doc, chunks);
    box.append(card);
  });

  bubble.append(box);
  scrollDown();
}

// ---- 流式事件处理 ----
function handleEvent(evt, bubble, renderer) {
  switch (evt.type) {
    case "meta":
      if (evt.conversation_id) {
        conversationId = evt.conversation_id;
        saveConversationId(conversationId);
      }
      break;
    case "status":
      setStatus(evt.message);
      break;
    case "tool":
      addToolChip(bubble, evt.name, evt.args);
      setStatus(`正在调用工具 ${evt.name}`);
      break;
    case "token":
      renderer.push(evt.content);
      scrollDown();
      break;
    case "done": {
      const contentEl = bubble.querySelector(".content");
      contentEl.classList.remove("streaming");
      renderer.finish(evt.final_answer);
      renderCitations(bubble, evt.citations);
      lastAnswer = evt.final_answer || "";
      break;
    }
    default:
      break;
  }
}

// ---- 发送 / 停止 ----
async function send() {
  const text = inputEl.value.trim();
  if (!text || streaming) return;
  if (location.protocol === "file:") {
    toast(
      "请通过 http://127.0.0.1:8000/ 访问本页面（当前为本地文件方式，接口不可用）",
      "error",
    );
    return;
  }

  streaming = true;
  setSendButton(true);
  inputEl.value = "";
  addUserMessage(text);
  setStaticFaqVisible(false);

  const bubble = addAssistantBubble();
  const contentEl = bubble.querySelector(".content");
  const renderer = createStreamRenderer(contentEl);
  currentAbort = new AbortController();

  try {
    const resp = await fetch(`${API}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
      signal: currentAbort.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const takeEvents = (flush) => {
      const parts = buffer.split("\n\n");
      // 流结束时尾部可能没有空行，也要解析，否则 done / 会话 ID 会丢掉。
      buffer = flush ? "" : parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        let evt;
        try {
          evt = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        handleEvent(evt, bubble, renderer);
      }
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: !done });
      if (done) {
        buffer += decoder.decode();
        takeEvents(true);
        break;
      }
      takeEvents(false);
    }
  } catch (e) {
    contentEl.classList.remove("streaming");
    if (e.name === "AbortError") {
      renderer.finish(renderer.text || "（已停止生成）");
    } else {
      setMarkdown(contentEl, "**请求失败：** " + e.message);
    }
  } finally {
    setStatus("");
    streaming = false;
    currentAbort = null;
    setSendButton(false);
    scrollDown();
    loadConversations();
    const answerText = lastAnswer || renderer.text || "";
    const contextText = answerText ? `用户: ${text}\n客服: ${answerText}` : "";
    loadSuggestedFaq(conversationId, contextText);
  }
}

function abortStream() {
  if (currentAbort) currentAbort.abort();
}

// ---- 常见问题（静态数据 + 动态推荐；聚焦输入框时展示）----
function setFaqVisible(visible) {
  faqPanelEl.classList.toggle("hidden", !visible);
}

function setStaticFaqVisible(visible) {
  faqStaticSectionEl.classList.toggle("hidden", !visible);
}

inputEl.addEventListener("focus", () => setFaqVisible(true));
document.addEventListener("click", (e) => {
  const target = e.target;
  const insidePanel = target instanceof Node && faqPanelEl.contains(target);
  if (target === inputEl || insidePanel) return;
  setFaqVisible(false);
});

function renderFaqItems(items) {
  faqListEl.replaceChildren();
  items.forEach((item) => {
    const li = document.createElement("li");
    li.title = "点击填入输入框";
    const question = typeof item === "string" ? item : item.question;
    const category = typeof item === "string" ? "" : (item.category || "");
    if (category) {
      const cat = document.createElement("span");
      cat.className = "faq-cat";
      cat.textContent = `[${category}]`;
      li.append(cat);
    }
    li.append(document.createTextNode(question));
    li.onclick = () => {
      inputEl.value = question;
      inputEl.focus();
      setFaqVisible(false);
    };
    faqListEl.append(li);
  });
}

async function loadFaq() {
  try {
    const resp = await fetch(`${API}/faq`);
    const items = await resp.json();
    baseFaqItems = items;
    renderFaqItems(items);
  } catch {
    /* FAQ 加载失败不影响对话主流程 */
  }
}

// 渲染推荐问题到独立的 "猜你想问" 区域
function renderSuggestions(questions) {
  suggestListEl.replaceChildren();
  if (!questions || questions.length === 0) {
    suggestSectionEl.classList.add("hidden");
    return;
  }
  questions.forEach((q) => {
    const li = document.createElement("li");
    li.title = "点击填入输入框";
    const cat = document.createElement("span");
    cat.className = "faq-cat";
    cat.textContent = "[推荐]";
    li.append(cat);
    li.append(document.createTextNode(q));
    li.onclick = () => {
      inputEl.value = q;
      inputEl.focus();
      setFaqVisible(false);
    };
    suggestListEl.append(li);
  });
  suggestSectionEl.classList.remove("hidden");
  // 回答结束后直接展开，不必再点一次历史会话。
  setFaqVisible(true);
}

// 根据对话上下文推荐后续问题（异步，不阻塞）。
// context_text 由前端直传，消除 MySQL 写入竞态。
async function loadSuggestedFaq(cid, contextText) {
  const requestId = ++suggestionRequestId;
  if (!cid) {
    console.warn("[suggest] skipped: no conversation id");
    renderSuggestions([]);
    return;
  }
  try {
    const payload = { conversation_id: cid };
    if (contextText) payload.context_text = contextText;
    const resp = await fetch(`${API}/suggest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      if (requestId !== suggestionRequestId || cid !== conversationId) return;
      console.warn("[suggest] http error", resp.status);
      renderSuggestions([]);
      toast("推荐问题加载失败（HTTP " + resp.status + "），请检查后端服务", "error");
      return;
    }
    const data = await resp.json();
    // 会话切换后，旧请求不得覆盖当前会话的推荐问题。
    if (requestId !== suggestionRequestId || cid !== conversationId) return;
    console.log("[suggest] received", data.questions);
    renderSuggestions(data.questions);
  } catch (err) {
    if (requestId !== suggestionRequestId || cid !== conversationId) return;
    console.warn("[suggest] failed", err);
    renderSuggestions([]);
    toast("推荐问题加载失败，请检查网络连接", "error");
  }
}

// 会话多选：全选 / 删除选中
function updateDeleteSelectedBtn() {
  const count = selectedConversations.size;
  deleteSelectedBtn.disabled = count === 0;
  deleteSelectedBtn.textContent = count ? `删除选中（${count}）` : "删除选中";
  selectAllEl.checked =
    displayedConvIds.length > 0 &&
    displayedConvIds.every((id) => selectedConversations.has(id));
}

function updateConversationChecks() {
  convListEl.querySelectorAll("input.conv-check").forEach((box) => {
    box.checked = selectedConversations.has(box.dataset.convId);
  });
}

async function deleteSelectedConversations() {
  const ids = [...selectedConversations];
  if (!ids.length) return;
  const ok = window.confirm(
    `确定删除选中的 ${ids.length} 个会话及其全部消息？此操作不可撤销。`,
  );
  if (!ok) return;

  const resp = await fetch(`${API}/conversations/batch-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_ids: ids }),
  });
  if (!resp.ok) {
    toast(`批量删除失败：HTTP ${resp.status}`, "error");
    return;
  }
  const data = await resp.json();
  if (ids.includes(conversationId)) {
    conversationId = null;
    clearSavedConversationId();
    suggestionRequestId += 1;
    messagesEl.replaceChildren();
    const hint = document.createElement("div");
    hint.className = "empty-hint";
    hint.textContent = "会话已删除，请输入新的问题。";
    messagesEl.append(hint);
    setStatus("");
    renderSuggestions([]);
  }
  selectedConversations.clear();
  toast(`已删除 ${data.deleted_conversations} 个会话`, "success");
  loadConversations();
}

selectAllEl.onclick = () => {
  if (selectAllEl.checked) {
    displayedConvIds.forEach((id) => selectedConversations.add(id));
  } else {
    selectedConversations.clear();
  }
  updateConversationChecks();
  updateDeleteSelectedBtn();
};
deleteSelectedBtn.onclick = deleteSelectedConversations;

// ---- 会话列表 ----
async function loadConversations() {
  try {
    const resp = await fetch(`${API}/conversations`);
    const list = await resp.json();
    // 防御性排序：按最近消息时间倒序（后端已排序，同秒并列时以该排序兜底）
    list.sort((a, b) =>
      String(b.last_message_at || "").localeCompare(
        String(a.last_message_at || ""),
      ),
    );
    convListEl.replaceChildren();
    if (!list.length) {
      const empty = document.createElement("li");
      empty.className = "conv-empty";
      empty.textContent = "暂无历史会话";
      convListEl.append(empty);
      return;
    }
    const visible = list.slice(0, 30);
    displayedConvIds = visible.map((c) => c.conversation_id);
    // 清理已不在列表中的选中项
    for (const id of [...selectedConversations]) {
      if (!displayedConvIds.includes(id)) selectedConversations.delete(id);
    }
    visible.forEach((c) => {
      const li = document.createElement("li");

      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "conv-check";
      check.dataset.convId = c.conversation_id;
      check.checked = selectedConversations.has(c.conversation_id);
      check.onclick = (e) => {
        e.stopPropagation();
        if (check.checked) selectedConversations.add(c.conversation_id);
        else selectedConversations.delete(c.conversation_id);
        updateDeleteSelectedBtn();
      };

      const main = document.createElement("span");
      main.className = "conv-main";
      const idSpan = document.createElement("span");
      idSpan.className = "conv-id";
      idSpan.textContent = c.title || c.conversation_id;
      const metaSpan = document.createElement("span");
      metaSpan.className = "conv-meta";
      metaSpan.textContent = `${c.message_count} 条 · ${formatTime(c.last_message_at)}`;
      main.append(idSpan, metaSpan);

      const actions = document.createElement("span");
      actions.className = "conv-actions";
      const renameBtn = document.createElement("button");
      renameBtn.className = "icon-btn";
      renameBtn.title = "重命名";
      renameBtn.textContent = "✎";
      renameBtn.onclick = (e) => {
        e.stopPropagation();
        renameConversation(c.conversation_id, c.title);
      };
      const delBtn = document.createElement("button");
      delBtn.className = "icon-btn danger";
      delBtn.title = "删除";
      delBtn.textContent = "🗑";
      delBtn.onclick = (e) => {
        e.stopPropagation();
        deleteConversation(c.conversation_id);
      };
      actions.append(renameBtn, delBtn);

      li.append(check, main, actions);
      li.title = `${c.title || c.conversation_id} · ${c.message_count} 条 · ${formatTime(c.last_message_at)}`;
      li.onclick = () => loadConversation(c.conversation_id);
      if (c.conversation_id === conversationId) li.classList.add("active");
      convListEl.append(li);
    });
    updateDeleteSelectedBtn();
  } catch {
    toast("会话列表加载失败，请检查网络连接", "error");
  }
}

// 重命名会话
async function renameConversation(cid, currentTitle) {
  const next = window.prompt("输入新的会话名称：", currentTitle || "");
  if (next === null) return;
  const title = next.trim();
  if (!title) {
    toast("名称不能为空", "error");
    return;
  }
  const resp = await fetch(`${API}/conversations/${encodeURIComponent(cid)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!resp.ok) {
    toast(`重命名失败：HTTP ${resp.status}`, "error");
    return;
  }
  toast("已重命名", "success");
  loadConversations();
}

// 删除会话
async function deleteConversation(cid) {
  if (!window.confirm(`确定删除会话「${cid}」及其全部消息？此操作不可撤销。`))
    return;
  const resp = await fetch(`${API}/conversations/${encodeURIComponent(cid)}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    toast(`删除失败：HTTP ${resp.status}`, "error");
    return;
  }
  toast("已删除会话", "success");
  if (cid === conversationId) {
    conversationId = null;
    clearSavedConversationId();
    suggestionRequestId += 1;
    messagesEl.replaceChildren();
    const hint = document.createElement("div");
    hint.className = "empty-hint";
    hint.textContent = "会话已删除，请输入新的问题。";
    messagesEl.append(hint);
    setStatus("");
    renderSuggestions([]);
  }
  loadConversations();
}

async function loadConversation(cid) {
  conversationId = cid;
  // 使上一会话的异步推荐结果失效，避免竞态覆盖。
  suggestionRequestId += 1;
  setStatus("正在加载会话…");
  try {
    const resp = await fetch(`${API}/conversations/${encodeURIComponent(cid)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    messagesEl.replaceChildren();
    data.messages.forEach((m) => {
      const div = document.createElement("div");
      div.className = "msg " + (m.role === "user" ? "user" : "assistant");
      if (m.role === "user") {
        div.textContent = m.content;
      } else {
        const content = document.createElement("div");
        content.className = "content markdown-body";
        setMarkdown(content, m.content);
        div.append(content);
      }
      messagesEl.append(div);
    });
    scrollDown();
    saveConversationId(cid);
    // 恢复推荐问题（从知识库返回或刷新页面时）
    const lastMsg = data.messages[data.messages.length - 1];
    if (lastMsg && lastMsg.role === "assistant") {
      const lastUserMsg = data.messages.slice().reverse().find((m) => m.role === "user");
      const contextText = lastUserMsg
        ? `用户: ${lastUserMsg.content}\n客服: ${lastMsg.content}`
        : "";
      loadSuggestedFaq(cid, contextText);
    }
  } catch (e) {
    toast("加载会话失败：" + e.message, "error");
  } finally {
    setStatus("");
  }
}

// ---- 绑定 ----
sendBtn.onclick = () => {
  if (streaming) {
    abortStream();
  } else {
    send();
  }
};
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") send();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setFaqVisible(false);
});
newConvBtn.onclick = () => {
  if (streaming) abortStream();
  conversationId = newConversationId();
  clearSavedConversationId();
  suggestionRequestId += 1;
  messagesEl.replaceChildren();
  const hint = document.createElement("div");
  hint.className = "empty-hint";
  hint.textContent = "已开启新会话，请输入您的问题。";
  messagesEl.append(hint);
  setStatus("");
  renderFaqItems(baseFaqItems);
  setStaticFaqVisible(true);
  renderSuggestions([]);
  lastAnswer = "";
};

loadConversations();
loadFaq();

// 从知识库返回或刷新后自动恢复当前会话
(async function restoreSession() {
  const storage = getSessionStorage();
  let savedCid = null;
  try {
    savedCid = storage ? storage.getItem(CONVERSATION_STORAGE_KEY) : null;
  } catch {
    savedCid = null;
  }
  if (savedCid) {
    conversationId = savedCid;
    await loadConversation(savedCid);
    loadConversations(); // 刷新高亮
  }
})();
