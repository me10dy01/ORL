// 星辰数码商城 · 知识库管理前端逻辑
const API = "/api";

// 防护：若以 file:// 直接打开（而非通过服务器），接口不可用
if (location.protocol === "file:") {
  const banner = document.createElement("div");
  banner.className = "warn-banner";
  banner.append("⚠ 检测到以本地文件方式打开页面，接口不可用。请先启动服务 ");
  const code = document.createElement("code");
  code.textContent = "uv run uvicorn app.main:app";
  banner.append(code, "，然后访问 ");
  const link = document.createElement("a");
  link.href = "/admin";
  link.textContent = "管理后台";
  banner.append(link);
  document.body.prepend(banner);
}

const fileInput = document.querySelector("#file-input");
const uploadBtn = document.querySelector("#upload-btn");
const resultEl = document.querySelector("#upload-result");
const tbody = document.querySelector("#doc-table tbody");
const actionResultEl = document.querySelector("#doc-action-result");
const modifyInput = document.querySelector("#modify-input");

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

// 「修改」正在替换的文档：点击行内修改按钮时记录，待文件选择后提交
let modifyTarget = null;

function setActionResult(message, isError) {
  if (actionResultEl) {
    actionResultEl.textContent = message;
    actionResultEl.classList.toggle("error", !!isError);
  }
}

async function loadDocs() {
  try {
    const resp = await fetch(`${API}/documents`);
    const docs = await resp.json();
    tbody.replaceChildren();
    if (!docs.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 4;
      td.style.textAlign = "center";
      td.style.color = "#9aa3b5";
      td.style.fontFamily = "inherit";
      td.textContent = "暂无已入库文档";
      tr.append(td);
      tbody.append(tr);
      return;
    }
    docs.forEach((d) => {
      tbody.append(buildDocRow(d));
    });
  } catch {
    setActionResult("文档列表加载失败，请检查网络连接", true);
  }
}

function buildDocRow(d) {
  const tr = document.createElement("tr");
  const nameTd = document.createElement("td");
  nameTd.textContent = d.file_name || "-";
  const idTd = document.createElement("td");
  idTd.textContent = d.document_id || "-";
  const cntTd = document.createElement("td");
  cntTd.textContent = d.chunk_count;

  const opTd = document.createElement("td");
  opTd.className = "doc-actions";
  opTd.append(
    buildModifyButton(d),
    buildDeleteButton(d),
  );

  tr.append(nameTd, idTd, cntTd, opTd);
  return tr;
}

function buildDeleteButton(d) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-mini danger";
  btn.textContent = "删除";
  btn.onclick = () => deleteDocument(d);
  return btn;
}

function buildModifyButton(d) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-mini";
  btn.textContent = "修改";
  btn.onclick = () => {
    modifyTarget = d;
    setActionResult(`正在为「${d.file_name}」选择新版本文件…`);
    modifyInput.click();
  };
  return btn;
}

async function deleteDocument(d) {
  const label = d.file_name || d.document_id;
  if (!window.confirm(`确定删除文档「${label}」的全部 ${d.chunk_count} 个 chunk？此操作不可撤销。`)) {
    return;
  }
  setActionResult(`正在删除「${label}」…`);
  try {
    const resp = await fetch(`${API}/documents/${encodeURIComponent(d.document_id)}`, {
      method: "DELETE",
    });
    const data = await resp.json();
    if (resp.ok) {
      setActionResult(
        `已删除「${label}」：移除 ${data.deleted_chunks} 个 chunk` +
          (data.file_removed ? "，源文件已一并清理" : ""),
      );
    } else {
      setActionResult("删除失败：" + (data.detail || `HTTP ${resp.status}`), true);
    }
  } catch (e) {
    setActionResult("删除失败：" + e.message, true);
  }
  loadDocs();
}

async function updateDocument(file) {
  const target = modifyTarget;
  if (!target) return;
  setActionResult(`正在替换「${target.file_name}」→「${file.name}」，切分与向量化中…`);
  const form = new FormData();
  form.append("file", file);
  try {
    const resp = await fetch(
      `${API}/documents/${encodeURIComponent(target.document_id)}`,
      { method: "PUT", body: form },
    );
    const data = await resp.json();
    if (resp.ok && data.changed) {
      setActionResult(
        `已替换：${target.file_name} → ${data.file_name}，` +
          `移除 ${data.deleted_chunks} 个旧 chunk，新增 ${data.ingested_chunks} 个` +
          (data.new_document_id !== data.old_document_id
            ? `（document_id 变为 ${data.new_document_id}）`
            : ""),
      );
    } else if (resp.ok) {
      setActionResult(`内容未变化（hash 一致），已跳过重新入库：${data.file_name}`);
    } else {
      setActionResult("修改失败：" + (data.detail || `HTTP ${resp.status}`), true);
    }
  } catch (e) {
    setActionResult("修改失败：" + e.message, true);
  }
  loadDocs();
}

modifyInput.onchange = () => {
  const file = modifyInput.files[0];
  modifyInput.value = "";
  if (file && modifyTarget) updateDocument(file);
};

uploadBtn.onclick = async () => {
  const file = fileInput.files[0];
  if (!file) {
    resultEl.textContent = "请先选择文件";
    return;
  }
  if (file.size > MAX_FILE_SIZE) {
    resultEl.textContent = `文件过大（${(file.size / 1024 / 1024).toFixed(1)}MB），最大支持 10MB`;
    resultEl.classList.add("error");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  uploadBtn.disabled = true;
  resultEl.textContent = "上传中，正在切分与向量化…";
  try {
    const resp = await fetch(`${API}/documents`, {
      method: "POST",
      body: form,
    });
    const data = await resp.json();
    if (resp.ok) {
      resultEl.textContent = `已入库：${data.file_name}，本次新增 ${data.ingested_chunks} 个 chunk（当前共 ${data.chunks}）`;
      fileInput.value = "";
      loadDocs();
    } else {
      resultEl.textContent = "上传失败：" + (data.detail || resp.status);
    }
  } catch (e) {
    resultEl.textContent = "上传失败：" + e.message;
  } finally {
    uploadBtn.disabled = false;
  }
};

loadDocs();
