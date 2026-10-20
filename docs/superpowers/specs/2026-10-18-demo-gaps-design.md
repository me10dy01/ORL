# 补齐演示缺口

**日期：** 2026-10-18  
**状态：** 已确认  
**范围：** Docker Compose 同时启动数据库、API 和网页；聊天最终回答按模型生成顺序推送；公开库存查询；独立 MCP 用登录 JWT 查订单。

Phase 3 的多 Agent 不在本文。全局测试通过后再单独设计。

## 1. 启动

`docker compose up` 启动三个服务：

| 服务 | 作用 | 端口 |
| --- | --- | --- |
| `db` | 现有 Postgres 16 + pgvector | `5432` |
| `api` | FastAPI | `8000` |
| `web` | Next.js | `3000` |

Ollama 留在本机。API 容器用 `http://host.docker.internal:11434` 访问它。Compose 为 API 设置 `extra_hosts: ["host.docker.internal:host-gateway"]`。

API 的数据库地址是 `postgresql+psycopg://orl:orl@db:5432/orl`。JWT 密钥沿用现有演示默认值。网页在构建时把 `NEXT_PUBLIC_API_URL` 设为 `http://127.0.0.1:8000`，因为浏览器跑在宿主机上。

`db` 提供健康检查。`api` 在数据库健康之后启动。`web` 在 `api` 启动之后启动。

独立 MCP 不进 Compose。它仍由本机 `uv run python -m app.mcp_server` 监听 `127.0.0.1:8001`，并使用映射出来的 `127.0.0.1:5432`。

本机直接运行 `uvicorn` 和 `next dev` 的方式保留。测试继续在本机对 `orl_test` 运行，不放进容器。

知识库不在容器启动时自动灌入。数据库起来后执行：

```powershell
docker compose run --rm api uv run python -m app.scripts.ingest_docs
```

API 镜像内包含 `docker-compose.yml`、`apps/api` 和 `data/documents`，使现有的 `repo_root()` 能找到知识库原文。改了 API 或文档后需要重新构建镜像。网页镜像在构建阶段执行 Next.js 生产构建。README 写明构建、启动和灌库的顺序。

## 2. 流式回答

`POST /api/chat` 保持一次返回完整 JSON。`POST /api/chat/stream` 是页面使用的流。两条路径共用同一次工具循环，最终写入数据库的正文相同。

事件顺序：

1. `meta`：带 `conversation_id`。
2. 已转人工或本次建单：`status` 为「正在转人工」，然后 `ticket_created`，然后 `done`。这条路径没有 `token`。
3. Bot 回答：模型每一轮都向 Ollama 打开流。

工具轮次不把文字送进聊天。服务端发送：

| 工具 | `status.message` |
| --- | --- |
| `search_knowledge` | 正在检索知识 |
| `lookup_orders` | 正在查订单 |
| `lookup_stock` | 正在查库存 |

一轮里只要出现工具调用，该轮已产生的文字都不作为回答。如果这一轮已经向前端发过 `token`，服务端再发 `{"type":"reset"}`。页面只清空当前这条回复的草稿，保留更早的消息。

没有工具调用的一轮，按模型产出的顺序发送 `{"type":"token","content":...}`。流结束后发送 `done`。`done.final_answer` 是这些 token 拼成的全文，也是写入消息表的正文。页面以 `done.final_answer` 覆盖草稿。

模型流在产出任何 token 之前失败时，正文用「抱歉，当前服务暂时不可用，请稍后再试。」已经发出部分 token 后又失败时，先 `reset`，再以这句作为 `done.final_answer`。不把半截文字写入数据库。

只返回字符串的假模型表示不调用工具。流式接口把该字符串作为回答，token 拼起来等于 `done.final_answer`。假模型若提供逐段输出，测试要能看到多段 `token` 出现在 `done` 之前。

## 3. 库存查询

新增工具 `lookup_stock(query)`。网页给模型的工具列表里加入它，可见参数只有 `query`。不读取登录用户。网页进程内调用和独立 MCP 都提供它。

匹配顺序：

1. 去掉首尾空白。空字符串返回「未找到」。
2. 从 query 中取出全部 `P` 加三位数字。其中已存在的商品按编号升序返回，不再做名称匹配。
3. 否则选出名称命中的商品，按编号升序返回。命中指：名称包含整段 query，或 query 包含整个名称，或 query 里有一段至少两个字符出现在名称中。沿用订单查询里的忽略词：那单、这个、订单、我的、到哪、一下、状态。
4. 没有命中返回「未找到」。

每条商品一行：`商品 P001 星辰 K87 机械键盘，库存 12`。多条用换行连接。不返回价格，不返回订单。查询抛错时也返回「未找到」，不让本次聊天失败。

没有请求上下文时，工具自己打开数据库会话并在结束后关闭。

## 4. 独立 MCP 的身份

网页聊天继续在进程内调用工具，不连接 8001。进程内的 `lookup_orders` 仍使用当前登录用户、用户原话和会话槽位。模型传入的 `user_id`、原话和槽位都忽略。

独立 MCP 的 HTTP 请求读取 `Authorization: Bearer <JWT>`。JWT 与网页登录使用同一密钥、同一算法。`sub` 必须是现有用户的 id，并且未过期。

| 情况 | `lookup_orders` | `search_knowledge` 与 `lookup_stock` |
| --- | --- | --- |
| JWT 有效 | 只查 `sub` 对应用户 | 可用 |
| 缺少、无效、过期，或用户不存在 | 返回「未找到」 | 可用 |

工具参数里的 `user_id` 和 `slots` 一律忽略。独立 MCP 没有进程内原话时，使用调用方传入的 `message` 作为原话，槽位视为空。归属规则与网页相同：订单号来自这句原话，金额只在单笔且归属通过时出现。不因为调用方传入槽位而记住订单。

无效 JWT 不关闭 MCP 连接，查单工具返回「未找到」。

## 5. 测试

`apps/api` 中 `uv run pytest -q` 全部通过，并且只连接 `orl_test`。

至少覆盖：

- 假模型先调用工具，再逐段给出最终回答；页面事件里的 token 拼起来等于 `done.final_answer`。
- 询问「键盘还有货吗」得到键盘的「库存 12」，结果中没有鼠标的「库存 20」。这条查询不检查登录用户。query 为 `P001` 时只返回键盘。
- 独立 MCP 在 JWT 属于 user1 时，即使工具参数写了别人的 `user_id`，结果里仍有该用户自己的订单，且没有李四那单的金额 `199.00`。
- 独立 MCP 不带 JWT 时，查单返回「未找到」。
- 现有「转人工」流里，`done` 之前没有 `token`。

实现后在浏览器走通：顾客登录、询问库存、询问自己的订单、转人工、坐席接单并结案。Compose 能完成配置检查；Docker 可用时实际启动，并确认 API 健康检查与网页登录页可打开。
