# ORL 智能客服工单

星辰数码商城的客服机器人先回答，答不了或用户要求人工时建立工单，人工客服再接单并结案。

## 启动

本机需要已安装 Docker，并且 Ollama 已有 `qwen3-embedding:0.6b` 和 `qwen3.5:4b`。在仓库根目录构建并启动数据库、API 和网页：

```powershell
docker compose build
docker compose up -d
docker compose run --rm api uv run python -m app.scripts.ingest_docs
```

浏览器打开 `http://127.0.0.1:3000`。API 在 `http://127.0.0.1:8000`。改了 API 或知识库文档后需要重新 `docker compose build api`。

独立 MCP 不在 Compose 里。需要时在本机另开终端：

```powershell
cd apps/api
uv run python -m app.mcp_server
```

它监听 `http://127.0.0.1:8001`。查订单要带网页登录得到的 `Authorization: Bearer <JWT>`。

只想在本机跑 API 时，可以单独启动数据库：

```powershell
docker compose up -d db
cd apps/api
uv sync
uv run uvicorn app.main:app --port 8000
```

另开一个终端，把知识库写入向量库：

```powershell
cd apps/api
uv run python -m app.scripts.ingest_docs
```

演示账号密码都是 `demo1234`：`user1` 是顾客，`agent1` 是人工客服。顾客张三的手机号是 `13800001111`，名下有键盘订单 `ORD12345678` 和鼠标订单 `ORD12345679`。

## 演示

下面的命令在仓库根目录执行。`POST /api/chat` 返回完整 JSON。`POST /api/chat/stream` 用 SSE 推送，最后一条 `done.final_answer` 才是要展示的文本。

```powershell
$login = curl.exe -s http://127.0.0.1:8000/api/auth/login -H "Content-Type: application/json" -d "{\"username\":\"user1\",\"password\":\"demo1234\"}"
$token = ($login | ConvertFrom-Json).access_token
$auth = @{ Authorization = "Bearer $token" }

$conv = curl.exe -s http://127.0.0.1:8000/api/conversations -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d "{}"
$cid = ($conv | ConvertFrom-Json).conversation_id

curl.exe -N http://127.0.0.1:8000/api/chat/stream -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d "{\"conversation_id\":\"$cid\",\"message\":\"我的订单到哪了\"}"

curl.exe -s http://127.0.0.1:8000/api/chat -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d "{\"conversation_id\":\"$cid\",\"message\":\"转人工\"}"
```

转人工响应里的 `ticket_id` 交给人工客服：

```powershell
$agentLogin = curl.exe -s http://127.0.0.1:8000/api/auth/login -H "Content-Type: application/json" -d "{\"username\":\"agent1\",\"password\":\"demo1234\"}"
$agent = ($agentLogin | ConvertFrom-Json).access_token

curl.exe -s http://127.0.0.1:8000/api/tickets?status=open -H "Authorization: Bearer $agent"
curl.exe -s -X POST http://127.0.0.1:8000/api/tickets/TICKET_ID/claim -H "Authorization: Bearer $agent"
curl.exe -s -X POST http://127.0.0.1:8000/api/tickets/TICKET_ID/messages -H "Authorization: Bearer $agent" -H "Content-Type: application/json" -d "{\"content\":\"已为您处理\"}"
curl.exe -s -X POST http://127.0.0.1:8000/api/tickets/TICKET_ID/transition -H "Authorization: Bearer $agent" -H "Content-Type: application/json" -d "{\"action\":\"resolve\"}"
curl.exe -s -X POST http://127.0.0.1:8000/api/tickets/TICKET_ID/transition -H "Authorization: Bearer $agent" -H "Content-Type: application/json" -d "{\"action\":\"close\"}"
```

把 `TICKET_ID` 换成转人工返回的工单号。健康检查是 `curl.exe http://127.0.0.1:8000/health`。
