# API 文档

- Base URL：`http://127.0.0.1:8000`
- 交互式文档：`GET /docs`（Swagger UI）
- 前端页面：`GET /`（客服）、`GET /admin`（知识库管理）

## 端点索引

| 方法 | 路径 | 说明 | 是否耗时 |
| --- | --- | --- | --- |
| POST | `/api/chat` | 完整 Workflow，返回结构化结果 | 慢（本地 LLM） |
| POST | `/api/chat/stream` | SSE 流式返回 | 慢（本地 LLM） |
| POST | `/api/documents` | 上传文档并增量入库 | 中（需向量化） |
| GET | `/api/documents` | 列出已入库文档 | 快 |
| PUT | `/api/documents/{id}` | 替换文档内容（修改），内容未变化时幂等跳过 | 中（需向量化） |
| DELETE | `/api/documents/{id}` | 删除文档（向量 + 无同名文档时的源文件） | 快 |
| GET | `/api/conversations` | 列出会话（按最近消息时间倒序） | 快 |
| GET | `/api/conversations/{id}` | 查看会话消息 | 快 |
| PATCH | `/api/conversations/{id}` | 重命名会话 | 快 |
| DELETE | `/api/conversations/{id}` | 删除会话及其全部消息 | 快 |
| POST | `/api/conversations/batch-delete` | 批量删除会话（列表「全选删除」） | 快 |
| GET | `/api/cache` | 回答缓存统计 | 快 |
| DELETE | `/api/cache` | 清空回答缓存 | 快 |
| GET | `/api/faq` | 常见问题列表（输入框上方，聚焦时展示） | 快（静态） |
| GET | `/health` | 健康检查 | 快 |

---

## POST /api/chat

运行完整 Workflow（Rewrite → Supervisor → RAG/Tool/Mixed → Synthesis → QA → Retry）。

### 请求体

```json
{
  "message": "K87 键盘支持退货吗？",
  "conversation_id": "conv-abc123"
}
```

- `message`：必填，非空
- `conversation_id`：可选；缺省自动生成，**传入相同值即为多轮对话**（会读取历史做 Query Rewrite）

### 响应

```json
{
  "conversation_id": "conv-abc123",
  "route": "rag",
  "reason": "涉及企业知识/政策",
  "tasks": [
    { "agent": "rag", "query": "K87 键盘退货政策" }
  ],
  "rewritten_query": "K87 键盘支持退货吗？",
  "final_answer": "星辰 K87 机械键盘支持 7 天无理由退货……",
  "citations": [
    {
      "document_id": "doc_d39f940c",
      "chunk_id": "doc_d39f940c_chunk_000",
      "file_name": "售后与退换货.md",
      "source": "售后与退换货.md",
      "page": 1
    }
  ],
  "qa_passed": true,
  "retry_count": 0,
  "degraded": false,
  "route_decision": "rules",
  "profile": {
    "precheck_ms": 0.1,
    "cache_lookup_ms": 1.2,
    "embedding_ms": 39.3,
    "retrieval_ms": 1.2,
    "rag_gen_ms": 6724.4,
    "synthesis_ms": 6775.9,
    "qa_ms": 0.1,
    "llm_calls_count": 3,
    "total_ms": 23533.8
  }
}
```

`citations` 为**片段级**（同一文档可能命中多个 chunk，因而重复出现）；
前端展示时会按 `document_id` 分组为「一篇文档一张卡片 + 其 chunk 列表」，避免同一文档重复展示。

增量字段说明（模型职责重构后新增，均为**向后兼容**的附加字段）：

| 字段 | 含义 |
| --- | --- |
| `route_decision` | 路由决策路径：`rules`（规则命中，0 次 LLM）/ `llm_short` / `llm_full` / `fallback` |
| `degraded` | 是否为**安全降级回答**（QA 重试耗尽后返回的兜底话术，而非未过检草稿） |
| `profile` | 各阶段耗时（ms）与 LLM 调用次数，用于性能剖析；见 [`METRICS.md`](METRICS.md) |

### 示例

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"商城退货政策是什么？"}'
```

---

## POST /api/chat/stream

Server-Sent Events 流式返回；只暴露**用户可见事件**，内部节点事件不外泄。

### 请求体

同 `/api/chat`

### 响应

`text/event-stream`，每条事件为 `data: {json}\n\n`

| type | 示例 | 说明 |
| --- | --- | --- |
| `meta` | `{"type":"meta","conversation_id":"conv-abc123"}` | 会话 ID |
| `status` | `{"type":"status","message":"正在分析问题"}` | 用户可见状态 |
| `tool` | `{"type":"tool","name":"get_order","args":{"order_id":"ORD20260001"}}` | 工具调用 |
| `token` | `{"type":"token","content":"星辰"}` | 最终回答 token 流 |
| `done` | `{"type":"done","final_answer":"...","citations":[...],"qa_passed":true,"retry_count":0,"degraded":false,"profile":{...}}` | 完成 |

### 示例

```bash
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"商城退货政策是什么？"}'
```

---

## POST /api/documents

上传知识库文档并增量入库（按**文件内容 hash** 去重）。

支持格式：`.md` / `.markdown` / `.txt` / `.pdf` / `.docx`。

- PDF 按页提取文本（溯源 `page` 为真实页码），其余文件视为单页
- 纯文本 utf-8 优先、兼容 gbk

### 请求

`multipart/form-data`，字段名 `file`

### 响应

```json
{
  "file_name": "新品政策.md",
  "documents": 5,
  "chunks": 9,
  "ingested_chunks": 2
}
```

- `documents`：当前 `data/documents/` 下文档总数
- `chunks`：切分后总片段数
- `ingested_chunks`：**本次新增**入库片段数（已存在则为 0）

### 示例

```bash
curl -X POST http://127.0.0.1:8000/api/documents -F "file=@售后与退换货.md"
```

### 错误

不支持的格式返回 `400`：

```json
{ "detail": "仅支持 .docx、.markdown、.md、.pdf、.txt 格式" }
```

---

## GET /api/documents

列出已入库文档（按 `document_id` 聚合）。

```json
[
  {
    "document_id": "doc_d39f940c",
    "file_name": "售后与退换货.md",
    "file_hash": "31a33cfe...",
    "chunk_count": 2
  }
]
```

---

## PUT /api/documents/{document_id}

替换文档内容（管理页「修改」按钮）。

### 行为

- **同名替换**：`document_id` 由文件名派生、保持不变（已有溯源不失效）；先清旧版本全部 chunk 再入库新内容，避免新旧 chunk 混存
- **换名替换**：入库新文档，成功后删除旧文档（向量 + 源文件）
- **内容未变化**（提取文本的 md5 与现有 `file_hash` 一致）：幂等返回 `changed: false`，不做任何写操作
- 知识库变化时自动清空回答缓存（与上传/删除一致）

### 请求

`multipart/form-data`，字段名 `file`；格式需受支持（`.md` / `.markdown` / `.txt` / `.pdf` / `.docx`）

### 响应（内容有变化）

```json
{
  "changed": true,
  "old_document_id": "doc_d39f940c",
  "new_document_id": "doc_d39f940c",
  "file_name": "售后与退换货.md",
  "deleted_chunks": 2,
  "ingested_chunks": 3,
  "documents": 5,
  "chunks": 10
}
```

### 响应（内容未变化）

```json
{
  "changed": false,
  "old_document_id": "doc_d39f940c",
  "new_document_id": "doc_d39f940c",
  "file_name": "售后与退换货.md",
  "deleted_chunks": 0,
  "ingested_chunks": 0
}
```

### 错误

- `400`：格式不支持
- `404`：`document_id` 不存在

### 示例

```bash
curl -X PUT http://127.0.0.1:8000/api/documents/doc_d39f940c -F "file=@退货政策-新版.md"
```

---

## DELETE /api/documents/{document_id}

删除已入库文档：移除其全部向量；若剩余文档中已无同名文档，则一并删除 `data/documents/` 下的源文件（防止下次全量重建时被重新灌回）。同时清空回答缓存。

### 响应

```json
{
  "document_id": "doc_d39f940c",
  "file_name": "售后与退换货.md",
  "deleted_chunks": 2,
  "file_removed": true
}
```

### 错误

- `404`：`document_id` 不存在（重复删除同样返回 `404`）

### 示例

```bash
curl -X DELETE http://127.0.0.1:8000/api/documents/doc_d39f940c
```

---

## GET /api/conversations

列出会话，**按最近消息时间倒序**（同秒以消息 id 兜底）。

```json
[
  {
    "conversation_id": "conv-abc123",
    "message_count": 4,
    "last_message_at": "2026-03-12T10:00:00",
    "title": "退货咨询"
  }
]
```

`title` 为空表示未重命名（前端回退展示 `conversation_id`）。

---

## PATCH /api/conversations/{conversation_id}

重命名会话（仅标题，不影响消息）。

### 请求

```json
{ "title": "退货咨询" }
```

`title`：必填，1–128 字符。

### 响应

```json
{ "conversation_id": "conv-abc123", "title": "退货咨询" }
```

### 示例

```bash
curl -X PATCH http://127.0.0.1:8000/api/conversations/conv-abc123 \
  -H "Content-Type: application/json" \
  -d '{"title":"退货咨询"}'
```

### 错误

| 状态码 | 场景 |
| --- | --- |
| 400 | `title` 去除空白后为空 |
| 404 | 会话不存在 |
| 422 | `title` 缺失或超出 1–128 长度 |

---

## DELETE /api/conversations/{conversation_id}

删除会话及其**全部消息**（不可撤销）。

### 响应

```json
{ "conversation_id": "conv-abc123", "deleted_messages": 4 }
```

### 示例

```bash
curl -X DELETE http://127.0.0.1:8000/api/conversations/conv-abc123
```

### 错误

| 状态码 | 场景 |
| --- | --- |
| 404 | 会话不存在（无消息） |

---

## GET /api/conversations/{conversation_id}

查看会话消息（最近 50 条，按时间正序）。

```json
{
  "conversation_id": "conv-abc123",
  "messages": [
    { "role": "user", "content": "K87 支持退货吗？" },
    { "role": "assistant", "content": "支持……" }
  ]
}
```

---

## GET /health

```json
{ "status": "ok", "service": "星辰数码商城智能客服" }
```

---

## POST /api/conversations/batch-delete

批量删除会话及其全部消息（前端「全选 → 删除选中」使用）。

### 请求

```json
{ "conversation_ids": ["conv-a", "conv-b"] }
```

`conversation_ids`：必填，至少 1 个。

### 响应

```json
{ "deleted_conversations": 2, "deleted_messages": 7 }
```

### 示例

```bash
curl -X POST http://127.0.0.1:8000/api/conversations/batch-delete \
  -H "Content-Type: application/json" \
  -d '{"conversation_ids":["conv-a","conv-b"]}'
```

### 错误

| 状态码 | 场景 |
| --- | --- |
| 422 | `conversation_ids` 缺失或为空 |

---

## GET /api/faq

对话页输入框上方的问题列表（仅在点击/聚焦输入框时展示）。**纯静态数据**：不调用 LLM、不查数据库，
仅在页面加载时读一次文件（进程内缓存），对对话延迟零影响。

```json
[
  {
    "id": "faq_001",
    "question": "星辰数码商城的退货政策是什么？",
    "category": "退货政策"
  }
]
```

数据源：`data/faq.json`（可直接编辑，无需重启即可在下次请求生效——文件内容有进程内缓存，重启后重读）。

> 前端行为：面板位于**输入框上方**，**仅在点击/聚焦输入框时展示**，点击面板外或选中后收起；
> 点选问题仅**填入输入框**（不自动发送），避免未确认就触发一次耗时调用。

---

## GET /api/cache

回答缓存统计（精确匹配：模型名 + 归一化问题）。

```json
{ "entries": 12, "hits": 37 }
```

---

## DELETE /api/cache

清空回答缓存（返回清除条数）。上传/删除知识库文档时会**自动**清空，通常无需手动调用。

```json
{ "cleared": 12 }
```

---

## 错误约定

| 状态码 | 场景 | 响应体 |
| --- | --- | --- |
| 400 | 参数校验失败 / `ValidationError`（含 Tool 参数非法） | `{"detail": "..."}` |
| 404 | 数据不存在 / `NotFoundError` | `{"detail": "..."}` |
| 422 | 请求体不符合 schema（FastAPI 校验） | FastAPI 标准格式 |
| 500 | 未预期异常 | FastAPI 标准格式 |

> 服务端日志会记录完整异常；API 不静默吞异常。
