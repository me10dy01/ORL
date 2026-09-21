# AGENTS.md

# 智能客服工单处理系统

> **架构实现细节**请参阅 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（分层设计、主图流程、子图嵌入、数据模型、流式协议）。
> **评估指标与性能数据**请参阅 [docs/METRICS.md](docs/METRICS.md)。
> **问题排查记录**请参阅 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 1. 项目定位

这是一个运行在 Windows 本机、无 Docker、全进程本地运行的企业级智能客服工单处理 Demo。

项目定位：

- 大模型应用开发工程项目
- LangGraph Multi-Agent 架构演示
- 企业知识库 RAG
- Tool Calling
- Workflow State
- Conversation Memory
- Query Rewrite
- Rerank
- QA / Validation
- Retry
- Streaming
- FastAPI
- 本地 LLM / Embedding

虚拟企业：

星辰数码商城

业务类型：

数码配件电商。

所有虚拟商品、订单、库存、知识库文档和测试数据必须围绕“星辰数码商城”建立，并保持数据逻辑一致。

---

# 2. 核心业务目标

系统必须最终实现：

管理员上传企业文档
→ 文档解析
→ Chunk
→ Embedding
→ Chroma

用户提出自然语言问题
→ Supervisor 分析任务
→ RAG Agent / Tool Agent / RAG + Tool
→ 获取知识或业务数据
→ 生成回答
→ QA / Validation
→ 必要时 Retry
→ 最终回答
→ Citation / Source 溯源

最终目标：

“入库 → 检索 → 多 Agent 协作 → 质检 → 溯源 → Streaming → API → 前端”

---

# 3. 明确的 Agent 边界

系统只允许存在以下核心 Agent：

1. Supervisor Agent
2. RAG Agent
3. Tool Agent
4. QA / Validator

禁止为了增加“Agent 数量”而额外创建：

- General Agent
- Planner Agent
- Search Agent
- Reflection Agent
- Summary Agent
- Critic Agent
- Router Agent

除非后续经过明确的架构评审，确认该 Agent 解决了现有架构无法合理解决的问题。

当前设计原则：

Agent 数量少而职责清晰。

---

# 4. Supervisor 职责

Supervisor 不负责直接回答用户问题。

Supervisor 的职责：

1. 理解用户请求
2. 判断任务类型
3. 判断是否需要 RAG
4. 判断是否需要 Tool
5. 判断是否需要 RAG + Tool
6. 生成结构化路由结果

Supervisor 不允许：

- 直接访问 MySQL
- 直接访问 Chroma
- 直接执行 SQL
- 直接实现 RAG 检索
- 直接执行业务 Tool

Supervisor 应输出结构化数据。

示例：

{
    "route": "rag",
    "reason": "用户询问商城退货政策",
    "tasks": [
        {
            "agent": "rag",
            "query": "星辰数码商城退货政策"
        }
    ]
}

混合任务：

{
    "route": "mixed",
    "tasks": [
        {
            "agent": "tool",
            "query": "查询订单 ORD20260001 状态"
        },
        {
            "agent": "rag",
            "query": "查询商城发货规则"
        }
    ]
}

---

# 5. RAG Agent 职责

RAG Agent 负责：

- Query 接收
- Query Rewrite
- Embedding
- Chroma Retrieval
- Top-K
- Rerank
- Context 构建
- LLM Generation
- Source / Citation 返回

RAG Agent 不允许：

- 查询 MySQL
- 修改 MySQL
- 直接调用业务 Tool
- 修改 Conversation Memory
- 修改全局状态

RAG 的回答必须优先基于检索结果。

如果知识库没有足够依据：

不要强行生成事实。

应该明确说明知识库中没有找到足够依据。

---

# 6. Tool Agent 职责

Tool Agent 负责：

- 理解用户业务查询
- 判断需要使用哪个 Tool
- 生成结构化 Tool 参数
- 执行 Tool
- 根据 Tool Result 生成结果

Tool Agent 不允许直接操作数据库连接。

正确的数据访问链路：

Agent
→ Tool
→ Service
→ Repository
→ MySQL

禁止：

Agent
→ SQL
→ MySQL

禁止 LLM 自由生成 SQL 并直接执行。

---

# 7. Tool 设计原则

Tool 必须表达明确的业务能力。

例如：

get_product_info(product_id)

get_inventory(product_id)

get_order(order_id)

get_order_items(order_id)

get_user_orders(user_id)

不要设计：

execute_sql(sql)

query_database(sql)

Tool 参数必须进行验证。

Tool 必须处理：

- 参数缺失
- 参数非法
- 数据不存在
- 数据库异常
- 超时
- 空结果

---

# 8. MySQL 架构

采用：

Tool
→ Service
→ Repository
→ SQLAlchemy
→ MySQL

推荐数据库实体：

users
products
inventory
orders
order_items

根据实际业务需要可以增加：

warehouses
after_sales
shipments

但不要无意义扩展数据库表。

数据库必须使用虚构的“星辰数码商城”数据。

---

# 9. RAG 数据架构

知识库文档必须包含明确 metadata。

推荐：

{
    "document_id": "...",
    "file_name": "...",
    "version": 1,
    "chunk_id": "...",
    "page": 1,
    "source": "..."
}

必须支持基本的：

- 文档唯一标识
- 文件 hash
- 版本
- Chunk 标识
- Source 信息

避免重复上传造成重复向量。

---

# 10. RAG Pipeline

基础版本：

Document
→ Loader
→ Cleaner
→ Chunker
→ BGE-M3
→ Chroma
→ Retriever
→ Qwen3

优化版本：

Query
→ Query Rewrite
→ BGE-M3
→ Chroma Top-K
→ Reranker
→ Top-N
→ Context
→ Qwen3
→ Citation

必须先实现 baseline，再实现优化。

不要跳过 baseline。

---

# 11. Embedding 与 LLM

默认本地模型：

LLM：

Qwen3 8B

Embedding：

BGE-M3

模型通过 Ollama 本地运行。

禁止：

- OpenAI API
- 云端 LLM API
- 硬编码 API Key
- 未经确认自动替换模型

如果模型名称、LangChain API 或 Ollama API 发生版本差异：

优先检查当前官方文档和当前安装版本。

禁止使用已经废弃的旧 API。

---

# 12. Chroma

Chroma 用作本地 Demo 的向量数据库。

要求：

- 持久化存储
- collection 明确命名
- metadata 完整
- document_id 可追踪
- chunk_id 可追踪

不得把 Chroma 当作关系数据库。

---

# 13. LangGraph

LangGraph 用于 Workflow Orchestration。

推荐主流程：

START
→ Supervisor
→ Route
→ RAG / Tool / Mixed
→ Synthesis
→ QA
→ Retry / Final
→ END

使用 State 管理：

- user_query
- conversation_id
- route
- tasks
- retrieved_docs
- tool_results
- draft_answer
- citations
- qa_result
- retry_count
- final_answer

State 只保存当前 Workflow 所需要的信息。

性能优化：

- 单意图 RAG 查询跳过 Synthesis：当 `route=='rag'` 且无 QA 反馈时，跳过 Synthesis LLM 调用，节省约 6 秒延迟。

---

# 14. State 与 Memory 必须分离

Workflow State：

当前请求执行过程中使用。

Conversation Memory：

跨请求保存的历史对话上下文。

禁止把永久对话历史无限塞入 Workflow State。

Memory 必须考虑：

- conversation_id
- 最近消息
- 历史摘要
- 上下文长度控制

---

# 15. Query Rewrite

Query Rewrite 主要用于：

- 多轮对话
- 指代消解
- 上下文缺失
- 查询表达不完整

例如：

历史：

用户：
K87 支持退货吗？

用户：
那运费谁承担？

Rewrite：

“星辰数码商城 K87 退货时的运费由谁承担？”

Query Rewrite 不应该改变用户原始意图。

---

# 16. Rerank

Rerank 必须在基础 Retrieval 完成后加入。

推荐流程：

Query
→ Vector Top-K
→ Reranker
→ Top-N

例如：

Top-K = 10
Rerank
Top-N = 3

具体参数必须通过测试集验证，不允许声称某个参数“必然最好”。

必须保留 baseline 与优化版本，用于效果比较。

默认配置：

- `RAG_RERANK_ENABLED=true`（默认启用重排）
- 在线唯一实现是 `LLMReranker`（主 LLM，`reasoning=False`）。失败时保留向量召回顺序
- `RERANKER_IMPL` 只作兼容字段；非 `llm` 会被忽略，不得再接回 ONNX 或空重排实现

协议：

`Reranker.rerank(query, documents, top_n)`，工厂 `get_reranker()` 始终返回 `LLMReranker`。

---

# 17. QA / Validator

QA Agent 负责检查最终回答。

至少检查：

1. 是否回答用户问题
2. 是否存在明显事实错误
3. 是否与 RAG Context 冲突
4. 是否与 Tool Result 冲突
5. Citation 是否有效
6. 是否出现无依据事实

输出结构化结果：

{
    "passed": true,
    "issues": [],
    "citation_valid": true,
    "reason": ""
}

失败时进入 Retry。

Retry 必须有限制。

默认：

MAX_RETRY = 2

禁止无限循环。

---

# 18. Citation / Source

RAG 回答必须保留来源。

例如：

{
    "source": "退货政策.md",
    "document_id": "doc_001",
    "chunk_id": "chunk_007",
    "page": 2
}

最终回答应该能够展示：

“根据《退货政策》第 2 页……”

前端可以进一步显示 source card。

---

# 19. Streaming

最终用户只看到有意义的状态。

例如：

正在分析问题
→ 正在检索知识库
→ 正在查询订单
→ 正在验证回答
→ 最终答案

不要直接把所有内部 LangGraph Debug Event 暴露给用户。

内部事件与用户可见事件必须分离。

---

# 20. FastAPI

FastAPI 是系统统一 API 层。

至少规划：

POST /api/chat
POST /api/chat/stream

POST /api/documents

GET /api/documents

GET /api/conversations

根据实际需要扩展。

API 层不能包含核心业务逻辑。

正确：

API
→ Service
→ Graph / RAG / Tool

---

# 21. 错误处理

必须区分：

- LLM Error
- Ollama Error
- Embedding Error
- Chroma Error
- MySQL Error
- Tool Error
- Validation Error
- Workflow Error

不要使用：

except Exception:
    pass

禁止静默吞掉异常。

错误必须：

- 记录日志
- 返回合理错误信息
- 必要时触发 Retry

---

# 22. 测试原则

每个 Phase 完成后必须测试。

测试类型：

1. Unit Test
2. Integration Test
3. RAG Retrieval Test
4. Tool Test
5. Graph Test
6. E2E Test

任何 Phase 没有通过验收标准，不进入下一 Phase。

---

# 23. Evaluation

最终建立统一测试集。

至少包含：

- RAG 问题
- Tool 问题
- Mixed 问题
- Multi-turn 问题
- 不存在信息
- 错误订单号
- 模糊问题
- 检索困难问题
- Tool 异常
- RAG 无答案

评估：

- Routing Accuracy
- Tool Calling Accuracy
- Retrieval Hit Rate
- Recall@K
- Answer Accuracy
- Citation Accuracy
- QA Pass Rate
- Retry Rate
- Latency

---

# 24. Vibe Coding 工作规则

这是最重要的部分。

AI Coding Agent 不允许一次性实现整个项目。

必须严格按照 Phase 顺序推进。

每次只处理当前 Phase。

开始 Coding 前：

1. 阅读 AGENTS.md
2. 阅读当前项目代码
3. 检查已经完成的 Phase
4. 理解当前架构
5. 制定本 Phase 实施计划
6. 再开始修改代码

禁止：

- 未阅读代码直接重构
- 无理由改变项目架构
- 大规模删除已有代码
- 自动引入新的框架
- 自动更换模型
- 使用 Docker
- 引入云端 API
- 使用 OpenAI API Key
- 使用过时 LangChain / LangGraph API
- 为了“高级感”增加无意义 Agent

---

# 25. 修改代码原则

优先：

小步修改
→ 运行测试
→ 修复
→ 再继续

而不是：

生成大量代码
→ 最后统一运行
→ 出现几十个错误

每次修改后必须说明：

- 修改了什么
- 为什么修改
- 影响哪些模块
- 如何验证
- 测试结果

---

# 26. 依赖原则

优先使用已经存在的依赖。

只有当现有依赖无法合理完成任务时才增加新依赖。

增加依赖前必须说明：

- 为什么需要
- 解决什么问题
- 是否存在已有依赖可以替代
- 是否与 Windows 本地环境兼容

---

# 27. 文件组织原则

不要创建巨型文件。

单个 Python 文件如果职责明显过多，应拆分。

但也不要为了“架构漂亮”创建大量只有几十行的无意义文件。

优先保证：

高内聚
低耦合
职责明确
容易测试

---

# 28. 代码质量

代码必须：

- 类型提示
- 清晰命名
- 合理异常处理
- 日志
- 配置集中管理
- 环境变量管理
- 可测试

禁止硬编码：

- 数据库密码
- 模型地址
- API Key
- 文件绝对路径

---

# 29. Windows 环境约束

项目运行环境：

Windows 11

禁止假设：

- Linux shell
- bash
- Docker
- systemd
- Linux-only dependency

命令优先兼容：

PowerShell

Python 项目使用：

uv

---

# 30. 最终架构目标

最终目标不是代码量最大。

目标是：

清晰的数据边界
+
清晰的 Agent 边界
+
可解释的 Workflow
+
可验证的 RAG
+
可靠的 Tool Calling
+
有限 Retry
+
可追踪 Citation
+
可测试 Evaluation
+
良好的 Streaming UX

所有实现都必须服务于这些目标。

---

# 31. 当前开发策略

遵循：

数据
→ 单能力
→ Agent
→ Multi-Agent
→ 优化
→ 可靠性
→ API
→ UI
→ Evaluation

不要反过来。