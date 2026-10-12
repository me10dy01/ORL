# 星辰数码智能客服工单系统 — 设计规格

**日期：** 2026-10-12  
**状态：** 已确认（待实现计划）  
**定位：** 面试 / 作品集向 MVP；本地可演示完整工单闭环  

## 1. 背景与目标

现有 ORL 仓库是「问答型 Multi-Agent Demo」，名称含工单但无真实工单状态机、鉴权与坐席台。本设计要求 **整仓清空后重建**（保留 git 历史作对比），做成真正的 **Bot 先行 + 规则转人工建单 + 坐席处理结案** 系统。

### 1.1 成功标准

- 用户可登录聊天；Bot 能答知识/只读业务问题，或按规则转人工并建单  
- 坐席可登录查看队列、接单、回复、结案  
- Docker Compose 一键起 Postgres + API + Web；Ollama 跑在宿主机  
- 面试能讲清：领域模型、转人工策略、记忆、RAG、可演进的 MCP / Multi-Agent  

### 1.2 明确不做（MVP）

- 准生产级 RBAC / 审计合规 / SLA 引擎  
- `pending_customer` 等待态  
- LangSmith（可观测先用日志与业务事件）  
- Redis（预留二期：pubsub / 限流 / claim 锁）  
- 产品内 Multi-Agent 编排（预留 Phase 3）  
- 产品内 MCP（预留 Phase 2；MVP 用内置 Tool）  

## 2. 产品范围

### 2.1 角色

| 角色 | 端 | 能力 |
| --- | --- | --- |
| `customer` | Next.js 用户聊天页 | 会话、发消息、显式转人工、查看与己相关会话/工单 |
| `agent` | Next.js 坐席台 | 工单队列、接单、回复、状态流转、查看 AI 预填 |

演示账号种子即可（如 `user1` / `agent1`），JWT 鉴权。

### 2.2 智能形态

Bot 先答常见问题；满足触发条件时 **自动建单并进入人工模式**（该会话后续用户消息不再调 Bot）。

Bot MVP 能力：

1. 知识库问答（RAG）  
2. 只读业务工具（订单 / 库存 / 商品等）  
3. 建单时 AI 预填：`category` / `priority` / `summary`（失败则规则默认，不阻断建单）  

### 2.3 建单触发

满足任一即创建 `Ticket(status=open)` 并绑定当前 `Conversation`：

1. 用户点击「转人工」或明确要求人工  
2. Bot 连续无法可靠回答 ≥ 2 次（RAG 无依据 / 明确拒答等）  
3. 命中投诉 / 退款纠纷等关键词 → 建议 `priority=high`  

### 2.4 工单状态机

```text
open ──claim──► in_progress ──resolve──► resolved ──close──► closed
  │                 │
  └──────────── cancel ──────────────────────────────► cancelled
```

- 非法跳转返回 HTTP 409  
- **不包含** `pending_customer`  
- 优先级枚举：`low | normal | high`  

每次状态变更写入 `TicketEvent`（操作者、时间、from→to、可选 payload）。

## 3. 领域模型

```text
User                    # id, username, role, password_hash
Conversation            # id, customer_id, mode: bot|human, active_ticket_id?
Message                 # id, conversation_id, role: user|bot|agent|system, content, created_at
Ticket                  # id, conversation_id, status, priority, category, summary,
                        # suggested_reply?, assignee_id?, created_at, updated_at
TicketEvent             # id, ticket_id, actor_id, type, from_status?, to_status?, payload JSONB
Product / Order / …     # 只读业务种子（Tool 查询）
KnowledgeChunk          # document_id, content, metadata, embedding vector(D)
```

关系约束：

- 一个 `Conversation` 同一时刻最多一张进行中工单（`open` 或 `in_progress`）  
- 消息始终挂在 `Conversation`；坐席回复同时可记 `TicketEvent`  
- 知识原文可放 `data/documents/`；派生向量在 `KnowledgeChunk`  

### 3.1 记忆

| 层 | 存储 | MVP |
| --- | --- | --- |
| 会话记忆 | `messages` 最近 N 条注入 Bot | 必做 |
| 工单记忆 | `tickets` + `ticket_events` + 建单摘要 | 必做 |
| 跨会话长期画像 | 用户档案表 | 不做（二期） |

记忆主存储为 PostgreSQL，不以 Redis / 仅 LLM 上下文为主存储。

## 4. 系统架构

### 4.1 组件

```text
Next.js (web)  ──HTTP/SSE──►  FastAPI (api)
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
              PostgreSQL    Ollama(宿主机)   (可选云端 LLM)
              + PGVector    qwen3-embedding
                            + 对话模型
```

Docker Compose 服务：`db`（Postgres+pgvector）、`api`、`web`。  
Ollama 不进 Compose；API 容器通过 `http://host.docker.internal:11434` 访问宿主机。

### 4.2 技术选型

| 层 | 选型 |
| --- | --- |
| API | FastAPI |
| Web | Next.js（App Router） |
| DB | PostgreSQL 16 + PGVector |
| LLM | Provider 抽象；默认 Ollama，可切 OpenAI 兼容云端 |
| Embedding | Ollama **`qwen3-embedding:0.6b`**（维度以 `ollama show` 为准，写入配置常量；换模型需全量重嵌入） |
| 向量检索 | PGVector（余弦 / L2，MVP 选一种并固定） |
| 鉴权 | JWT（access token） |
| 编排 | MVP 单 Bot 服务函数；非 LangGraph Multi-Agent |
| 可观测 | 结构化日志 + `TicketEvent`；不强制 LangSmith |
| 缓存/实时 | MVP 坐席可用短轮询；Redis 二期可选 |

### 4.3 仓库结构（清空后）

```text
ORL/
  apps/
    api/                 # FastAPI
      app/
        main.py
        config.py
        llm/             # LLM + Embedding providers
        domain/          # ticket 状态机、模型
        services/        # chat、handoff、ticket、rag
        tools/           # 只读业务工具
        api/             # routes + schemas
      tests/
    web/                 # Next.js
      app/
        login/
        (customer)/chat/
        (agent)/tickets/
      components/
  data/
    documents/
    seed/
  docs/
    superpowers/specs/
    ARCHITECTURE.md      # 实现后更新
  docker-compose.yml
  README.md
  .env.example
```

### 4.4 清空策略

1. 打 git tag（如 `legacy-qa-demo`）保留旧问答 Demo 对照  
2. 删除旧业务代码与 `app/static` 等，保留 `.git`  
3. 按上表脚手架重建  
4. **先打通**登录 → 聊天（可先规则/简单 LLM）→ 建单 → 坐席结案，再接 RAG/Tool  

## 5. Bot 在线路径

```text
用户消息 + JWT
  → 若 Conversation.mode == human（已有进行中工单）
       → 只写入 Message，通知坐席侧（轮询可见），不调 Bot
  → 否则：
       1) 规则门：转人工 / 投诉关键词 → handoff 建单
       2) Bot：取最近 N 条记忆 →（可选改写）→ RAG 与/或 Tool → 生成
       3) 若拒答：累计；≥2 → handoff
       4) 否则返回 Bot 回答（可带 citations）
```

建单原子步骤（同一事务）：

1. 插入 `Ticket(open, priority, category, summary, …)`  
2. AI 预填失败则规则默认  
3. `TicketEvent(created)` + system `Message`  
4. `Conversation.mode = human`，绑定 `active_ticket_id`  

不搬旧项目 QA Retry 重图；可靠性策略为「不会就转人」。

## 6. API 草案

### 6.1 鉴权

- `POST /api/auth/login` → `{ access_token, role, user_id }`  
- Header：`Authorization: Bearer <jwt>`  

### 6.2 用户端

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/conversations` | 创建会话 |
| GET | `/api/conversations/me` | 我的会话列表 |
| POST | `/api/chat/stream` | 发消息（SSE） |
| POST | `/api/tickets/handoff` | 显式转人工 |

### 6.3 坐席端

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tickets?status=open` | 队列（priority, created_at） |
| POST | `/api/tickets/{id}/claim` | 接单 |
| GET | `/api/tickets/{id}` | 详情 + 消息 + events |
| POST | `/api/tickets/{id}/messages` | 坐席回复 |
| POST | `/api/tickets/{id}/transition` | resolve / close / cancel |

### 6.4 协议约定

- 错误体：`{ code, message }`；401/403/404/409 语义明确  
- SSE：`meta` / `status` / `token` / `ticket_created` / `done`  
- 流式最终展示必须以服务端 `final_answer` / 建单结果为准，避免重试草稿污染 UI  

## 7. RAG

1. 文档自 `data/documents/` 加载 → 切分 → `qwen3-embedding:0.6b` → 写入 `KnowledgeChunk`  
2. 在线：query 嵌入 → PGVector Top-K → 拼上下文 → LLM 生成  
3. MVP 不做独立 cross-encoder rerank  
4. 嵌入维度写入配置；变更嵌入模型必须重建向量  

## 8. 演进路线（非 MVP，但设计预留）

| 阶段 | 内容 |
| --- | --- |
| Phase 1（本规格） | 工单闭环 + 单 Bot + RAG/Tool + Docker/PG |
| Phase 2 | 业务能力收为 **MCP Server**；Bot 为 MCP Client |
| Phase 3 | LangGraph：**Triage / Resolve / TicketFill**（及可选坐席 Assist） |
| 可选 | Redis（pubsub、限流、claim 锁）；LangSmith 调 prompt；Query Rewrite；用户画像 |

接口边界：`services/tools` 与 `services/rag` 保持稳定，便于换成 MCP 或多 Agent 节点而不改 Ticket API。

## 9. 实现切片顺序

1. Monorepo 脚手架 + Compose（Postgres/pgvector）+ 种子 User  
2. JWT 登录 + 空聊天落库  
3. Ticket 状态机 + handoff API + 坐席队列/结案 UI  
4. LLM Provider + 简单 Bot 回答  
5. RAG（embedding + PGVector）+ 只读 Tool  
6. AI 预填字段、优先级规则、SSE 打磨、README/演示脚本  

## 10. 风险与约束

- Windows Docker 访问宿主机 Ollama 依赖 `host.docker.internal`  
- 单卡 Ollama 吞吐有限；演示控制并发  
- 整仓清空不可逆业务代码；必须先打 tag  
- 面试叙事重点在工单与分流，不在 Agent 数量  

## 11. 决策记录（摘要）

| 决策 | 选择 |
| --- | --- |
| 目标 | 面试 / 作品集 |
| 智能 | Bot → 规则转人工建单 |
| 端 | 用户 + 坐席 |
| 重建 | 整仓清空（方案 3） |
| 栈 | FastAPI + Next.js + Docker |
| DB | PostgreSQL + PGVector（不用本机 MySQL） |
| Embedding | `qwen3-embedding:0.6b` |
| LLM | Ollama 默认，可切云端 |
| MCP | Phase 2 |
| Multi-Agent | Phase 3 可插拔 |
| Redis / LangSmith | 非 MVP |
| 优先级 | low / normal / high |
| 等待态 | 不要 pending_customer |
