# ORL 项目交接

> 写给下一个编辑器 / Agent（Cursor、Trae、DSH 等）。请先读本文，再动代码。
> 更新日期：2026-10-21（Asia/Shanghai）
> 本机路径：`D:\learn\project\ORL`
> 远程：`https://github.com/me10dy01/ORL.git`（`me10dy01/ORL`）

## 一句话

「星辰数码」客服演示：机器人先答（RAG + 工具），再建工单并转人工；坐席可认领 / 回复 / 解决 / 关闭。

## 当前阶段

| 层 | 状态 | 说明 |
| --- | --- | --- |
| Git 已提交 | API 基础 MVP | FastAPI + Postgres、JWT、会话、工单状态机、转人工；tip ≈ `df9017a` |
| 本机工作区 | 演示向功能中后期（未提交干净） | Next.js 前端、RAG、LLM 工具环、SSE、库存工具、MCP、Redis、LangGraph 等大量在磁盘上 |
| Phase 3 多 Agent | 明确延后 | 见下方设计文档，本轮不做 |

**标签建议：** Demo-capable mid-build，尚未收成可发布版。

## 分支与 Git 风险（必读）

- 当前分支：`feat/demo-gaps`（与 `main` / `origin/main` tip 对齐过，但**工作区很脏**）
- **最大风险：** Web + RAG + 流式等演示能力大半只在本机未提交/未跟踪文件里。换机器或只拉远程会丢进度。
- 下一步优先：确认测试通过后，把 WIP **commit（并可 push）**，再换 Trae / Cloud。
- 本地未见 tag `legacy-qa-demo`（早期计划提过，可后补）。

## 关键路径

```
ORL/
  apps/api/          # FastAPI（已提交基础 + 大量未提交扩展）
  apps/web/          # Next.js 15 + Ant Design（基本整棵未跟踪）
  data/documents/    # 政策类 markdown（RAG 语料）
  docs/superpowers/specs/2026-10-18-demo-gaps-design.md  # 演示缺口设计（已确认）
  docker-compose.yml # db / redis / api / web（工作区有扩展）
  README.md          # 含演示 curl 流程（工作区有改）
```

## 已具备能力（按真相来源）

### 已在 Git 历史里

- `apps/api` 脚手架（uv）、健康检查、登录、工单流转、会话持久化与 handoff 测试、query rewrite / entity slots、种子用户/目录相关基础提交。

### 主要在本机工作区（未全部进远程）

- **Web：** 登录、聊天、坐席台等页面（Ant Design + Next 15）
- **API：** `rag`、`llm`、`tools`/`tool_loop`、`bot`、`graph`、`assist`、`profile`、`redis_bus`、`mcp_*`、`scripts/ingest_docs`
- **路由倾向：** `/api/auth/login`、conversations、`/chat` + `/chat/stream`、工单 claim/list/detail/messages/transition（含事件流）
- **基础设施：** API/Web Dockerfile、Compose 含 Redis、若干 pytest（streaming / rag / mcp / stock / graph 等）
- 源码内 TODO/FIXME：上次检查为 0（排除 venv / node_modules）

## 设计约定

- 主规格：`docs/superpowers/specs/2026-10-18-demo-gaps-design.md`（已确认）
- 该文档范围内的演示缺口优先；**Phase 3 多 Agent 不做**
- 运行依赖本机 **Ollama**（README/Compose 侧常见模型名：`qwen3.5:4b`、`qwen3-embedding:0.6b`——以当前 README / `.env.example` 为准）

## 建议验收（接手后先跑）

1. `docker compose` 起依赖；API health 正常  
2. 在 `apps/api`：`uv run pytest -q`（或项目文档指定的测试环境，如 `orl_test`）  
3. 浏览器路径：登录 → 库存/订单类问答 → 转人工 → 坐席处理并关闭  
4. 对照 `2026-10-18-demo-gaps-design.md` 的 exit criteria 打勾  

（上次只做只读盘点，**未实际跑测试 / 未验证镜像构建**。）

## 环境与配置

- 参考：`.env.example`、根目录 `README.md`、`docker-compose.yml`
- 需要本机 Ollama 与文档中的嵌入/对话模型
- Windows 本机路径：`D:\learn\project\ORL`

## 建议下一步（按优先级）

1. 跑通测试与 Compose；修失败项  
2. 整理并 **commit** 当前 WIP（必要时拆成 api / web / docs 几次提交），再 **push** `feat/demo-gaps`  
3. 对照 demo-gaps 规格收尾 UI/流式/RAG 缺口  
4. 需要跨编辑器时：保持打开同一目录，勿复制第二份工作区  
5. Phase 3 多 Agent：等演示稳定后再开  

## 不要做的事

- 不要假设远程已有完整 Web/RAG——先看 `git status`  
- 不要在未读 `2026-10-18-demo-gaps-design.md` 的情况下扩大到多 Agent  
- 不要另开平行目录改同一项目，避免分叉  

## 给 Agent 的开场指令（可复制）

```
请阅读 HANDOFF.md 与 docs/superpowers/specs/2026-10-18-demo-gaps-design.md。
工作区在 D:\learn\project\ORL，分支 feat/demo-gaps。
先 git status，确认大量未提交演示代码仍在；不要丢掉工作区。
优先：跑通 compose + pytest + 登录→聊天→转人工→坐席关闭；通过后再整理提交。
本轮不做 Phase 3 多 Agent。
```

---

*本文档仅描述交接当时磁盘与 Git 状态；以仓库实际文件为准。*
