# 问题与解决记录（Troubleshooting Log）

记录项目自开始以来遇到的**每一个真实问题**：现象 → 根因 → 解决方法 → 状态 → 影响。
所有结论均来自**实测**（日志 / 命令输出 / 测量数据），不含推测。

## 0. 总览

| 类别 | 编号 | 数量 | 状态 |
| --- | --- | ---: | --- |
| 环境与依赖 | P1–P2 | 2 | ✅ 全部解决 |
| 数据库 | P3–P5 | 3 | ✅ 全部解决 |
| RAG / 向量库 | P6–P7 | 2 | ✅ 全部解决 |
| Agent / 工作流 | P8–P12 | 5 | ✅ 全部解决 |
| 接口 / 前端 | P13–P15 | 3 | ✅ 全部解决 |
| **性能** | P16 | 1（含 5 项子优化） | ✅ 已解决（**6.7×**） |
| 测试与工程流程 | P17–P23 | 7 | ✅ 解决 6、缓解 1 |
| 负结论（不实施） | N1–N3 | 3 | ⭕ 已决策 |

---

## 1. 环境与依赖

### P1. `uv sync` 报「拒绝访问 (os error 5)」

- **现象**：无法删除 `.venv/Lib/site-packages/websockets/speedups.cp312-win_amd64.pyd`
- **根因**：① 早先 smoke test 遗留的 `uvicorn.exe` 仍持有该文件；② `websockets-17.1.dist-info` 缺 `RECORD`（半损坏）导致每次同步都尝试重装
- **解决**：`taskkill //F //IM uvicorn.exe` 清理遗留进程；删除损坏的 `websockets-17.1.dist-info`
- **状态**：✅ 已解决（复验：`uv sync` → `Checked 113 packages`）
- **影响**：阻塞依赖安装

### P2. `.env` 被模板覆盖 → 连不上 MySQL

- **现象**：`pymysql.err.OperationalError (1045, "Access denied for user 'root'@'localhost' (using password: NO)")`
- **根因**：执行 README 的 `Copy-Item .env.example .env` 时**覆盖了已有 `.env`**，密码被清空（错误里的 `using password: NO` 即为证据）
- **解决**：恢复 `.env`；README 改为**仅当 `.env` 不存在才复制**；`init_db` 增加友好提示（密码为空 / 连接失败时输出检查清单，而非长 traceback）
- **状态**：✅ 已解决
- **影响**：阻塞数据库初始化

---

## 2. 数据库

### P3. `mysql_url()` 无参调用返回**不含库名**的连接串

- **现象**：`AssertionError` in `has_table`，`dialect.default_schema_name: None`
- **根因**：`mysql_url(database=None)` 的默认参数让 URL 里没有库名
- **解决**：拆分为 `mysql_url()`（含 `mysql_db`）与 `mysql_server_url()`（建库用，不含库名）
- **状态**：✅ 已解决

### P4. 种子灌入外键失败

- **现象**：`IntegrityError (1452) Cannot add or update a child row ... inventory → products`
- **根因**：未定义 `relationship()` 时，SQLAlchemy **不保证跨表 INSERT 顺序**，`inventory` 先于 `products` 落库
- **解决**：种子灌入**按实体分组显式 `session.flush()`**（父表先落库）
- **状态**：✅ 已解决

### P5. 中文乱码风险

- **现象**：错误信息中中文显示为 `������`
- **根因**：连接串未显式指定字符集
- **解决**：URL 增加 `query={"charset": "utf8mb4"}`
- **说明**：控制台显示乱码是 Windows GBK 代码页问题，`PYTHONIOENCODING=utf-8` 可验证数据本身正确
- **状态**：✅ 已解决

---

## 3. RAG / 向量库

### P6. 知识库仅支持 `.md`

- **现象**：需求要求支持 docx / pdf / txt / md
- **解决**：新增 `app/rag/loaders.py`（`pypdf` 按页提取、`python-docx` 提取段落、纯文本 utf-8 优先兼容 gbk）；`chunk_id` 改为**按 document_id 全局唯一**（避免 PDF 多页冲突）
- **状态**：✅ 已解决（端到端验证：docx 中文 + pdf 英文均入库并检索命中，清理无残留）

### P7. 引用「重复展示」（同一文档出现两次、顺序不同）

- **现象**：回答下方「参考来源」里同一份文档出现两次
- **根因**：`citations` 是**片段级**的——`退货政策.md` 被切成 2 个 chunk 且都被命中 → 两张卡片；chunk 顺序随相似度变化 → 看起来"重复"
- **解决**：前端按 `document_id` **分组**（一份文档一张卡片 + chunk 列表，去重排序），标题改为「N 篇 / M 个片段」
- **状态**：✅ 已解决（用 Node 提取真实函数验证：5 片段 → 3 张卡片 / 4 个去重 chunk）

---

## 4. Agent / 工作流

### P8. 子图 custom 事件不传播（拿不到工具状态）

- **现象**：流式输出里没有 `tool` 事件
- **根因**：LangGraph `stream_mode="custom"` 默认 **`subgraphs=False`**，不流式子图内部事件
- **解决**：改用 `subgraphs=True` 并处理其 3 元组 `(namespace, mode, data)`；**只透出子图的 custom（工具）事件**，其余内部事件不外泄（符合规范第 19 条）
- **状态**：✅ 已解决

### P9. `save_memory` 空返回导致流式解析报错

- **现象**：`TypeError: 'NoneType' object is not iterable`（`state.update(update)`）
- **根因**：节点返回空 dict 时，LangGraph 的 updates 流里该节点值为 `None`
- **解决**：加空值守卫 `if update:`
- **状态**：✅ 已解决

### P10. Query Rewrite 丢失实体

- **现象**：`"那运费谁承担？"` 被改写成「退货时运费由谁承担？」，丢了 `K87`
- **根因**：提示词未强调"保留历史实体"
- **解决**：提示词增加「保留并补全历史中提到的具体实体」+ 完整示例
- **状态**：✅ 已解决（改写结果含 `K87`）

### P11. Supervisor 把 mixed 误判为 tool

- **现象**：`"订单 ORD20260002 显示待发货，你们什么时候发货？"` 被路由为 `tool`
- **根因**：提示词缺少「具体业务数据 + 政策疑问 → mixed」的判定规则
- **解决**：补充该规则与 mixed 示例
- **状态**：✅ 已解决

### P12. mixed 路由未回写 `tool_calls`（我引入的 bug）

- **现象**：mixed 请求触发**3 轮无意义重试**，单题耗时飙到 **40+ 分钟**
- **根因**：`mixed_node` 只写了 `tool_result`，未写 `state["tool_calls"]`；QA 规则看到 `expects_tool=True` 但 `tool_calls=[]` → 判失败 → 反复重试
- **解决**：节点同时回写 `tool_calls`
- **状态**：✅ 已解决（mixed 47.5s、`retry=0`）

---

## 5. 接口 / 前端

### P13. 以 `file://` 打开页面报「已禁止，文件不在受信任的文件夹中」

- **现象**：点击「知识库管理」报该提示；发送消息无反应
- **根因**：**直接用浏览器打开 `app/static/index.html`**（而非走 FastAPI 服务器），浏览器拦截了 `/api` 调用
- **解决**：前端检测 `location.protocol === 'file:'` 显示醒目提示条；README 强调必须访问 `http://127.0.0.1:8000/`
- **状态**：✅ 已解决（使用方式问题 + 已加防护）

### P14. ⚠️ `querySelector` 漏写 `#`（我引入的严重 bug）

- **现象**：点击输入框不显示 FAQ、**消息无法发送**、侧栏会话列表空白（看起来"对话记录消失"）
- **根因**：我用 sed 批量把 `getElementById("x")` → `querySelector("x")`，**漏掉了 `#`**。
  `querySelector("messages")` 按**标签名**查找 → 8 个元素全为 `null` → 绑定事件时抛错 → **整个脚本失效**
- **数据是否丢失**：**否**（`messages` 表 645 条消息 / 266 个会话完好）
- **解决**：补回 `#`；并新增 **Node DOM 冒烟测试** `tests/smoke_app_js.cjs`——用最小 DOM stub 真实执行 `app.js` 顶层初始化，且 `querySelector` **严格按 `index.html` 真实 id 解析**，非 `#id` 形式或 id 不存在即失败
- **验证**：故意复现 bug → 测试 `exit=1` 并指出 `messages（应为 #id 选择器）`；修复后 `exit=0`
- **状态**：✅ 已解决，且已用测试**防止复发**

### P15. 会话列表顺序不稳定

- **现象**：同一秒创建的会话顺序随机跳动
- **根因**：`created_at` 为秒级精度，同秒并列时无确定顺序
- **解决**：`ORDER BY max(created_at) DESC, max(id) DESC`（id 单调，作兜底）
- **状态**：✅ 已解决

---

## 6. 性能（收益最大的一节）

### P16. 单题耗时 2–4 分钟，不符合智能客服场景

**实测分解（共 189.2s）**：

| 节点 | 耗时 | 占比 | 说明 |
| --- | ---: | ---: | --- |
| `rag.generate` | 69.9s | 37% | RAG 生成回答 |
| `synthesis` | 57.7s | 30% | **把上一版再重写一遍** |
| `qa` | 33.3s | 18% | LLM 质检 |
| `supervisor` | 23.9s | 13% | 结构化路由 |
| `rag.retrieve` | 4.3s | 2% | 向量检索 |

**关键结论：大头是重复劳动与显存溢出，不是"模型太慢"。** 分项解决：

| 子问题 | 根因 | 解决 | 实测效果 |
| --- | --- | --- | --- |
| LLM 质检每次都跑 | 33s/次，而评估中 QA 通过率 100% | **QA 规则先行**：结构可判定则跳过 LLM，仅可疑时回落 | 189.2s → 160.9s |
| **显存溢出** | Ollama 对 qwen3 默认 `num_ctx=32768`，KV cache 约 **4.8GB** → 8GB 卡装不下 → **仅 59% 驻留显存** | `num_ctx=4096` + `keep_alive=30m`，统一 LLM 工厂 `app/llm.py` | **100% 驻留**；mixed 205.8s → **47.5s** |
| 输出过长 | 回答约 700 字，生成耗时随字数线性增长 | `num_predict=512` + prompt 要求「简明扼要 ≤300 字」 | 199 字；RAG 单题 160.9s → **28.2s** |
| 重复提问重复计算 | — | **回答缓存**（键 = 模型名 + 归一化问题；知识库变更时自动失效） | 重复问题 **0.0s** |

- **状态**：✅ 已解决（累计 **189.2s → 28.2s ≈ 6.7×**）

---

## 7. 测试与工程流程

### P17. 测试套件 30+ 分钟，每阶段全量跑不现实

- **解决**：引入 pytest `slow` 标记 + `--run-slow` 开关；默认只跑**快速测试（约 1.3s）**
- **状态**：✅ 已解决

### P18. 评估脚本无进度、超时则结果全丢

- **解决**：逐题进度日志（`[i/N] id route=... latency=...`）+ 子集参数 `--limit` / `--ids`
- **状态**：✅ 已解决

### P19. 缓存命中导致测试不再走完整链路

- **现象**：`test_streaming` 因命中缓存而失败（无 `tool` 事件、无"分析问题"状态）
- **解决**：新增 `fresh_cache` fixture（用例级清缓存）；评估脚本默认清缓存（`use_cache=False`）
- **状态**：✅ 已解决

### P20. E2E 测试用例隔离缺陷

- **现象**：多能力共用 `conversation_id`，累积历史把 `mixed` 带偏为 `tool`
- **解决**：各能力用独立会话，仅多轮记忆测试共用同一会话
- **状态**：✅ 已解决

### P21. 慢测试偶发 flaky（LLM 措辞波动）

- **现象**：`"那第二种呢？"` 的改写偶发不含"质量问题"
- **解决**：提示词增加序数指代示例（**降低概率，非根除**——LLM 输出本质有随机性）
- **状态**：⭕ 已缓解

### P22. 测试产生的会话污染前端列表

- **现象**：跑完测试后前端「历史会话」出现一堆 `mem-*` / `api-*` 测试数据
- **解决**：`tests/conftest.py` 在会话结束**自动清理测试前缀会话**（保留 `demo-*` 与真实用户会话）；另提供 `seed_demo` 一键重置
- **状态**：✅ 已解决（复验：测试后仅剩 `demo-xingchen`）

### P23. 静态检查阻塞项批量修复

- **清单与处理**：
  - `innerHTML`（XSS）→ 改 `DOMParser` + `replaceChildren`，绕开 sink
  - `alert()` → 改页内 toast
  - `appendChild` / `getElementById` → `append` / `querySelector`
  - `.rowcount` 类型不存在 → 改 `func.count()`
  - `typing.Iterator` → `collections.abc.Iterator`
  - `json.load` 无异常保护 → 加 `try/except` + 日志
  - `TypedDict(total=False)` 导致 key 可选性歧义 → 改必需 key
  - LangChain `with_structured_output` 返回类型 → 用 `cast`
  - 生成器返回标注、import 排序、`!important`、代码块语言、表格对齐
- **状态**：✅ 全部解决（各文件均 `✓ Python / JavaScript / Markdown / CSS clean`）

### P24. 思考型模型的思考 token 吃满输出预算，结构化输出为空（模型职责重构期间发现）

- **现象**：`LLMReranker` 与 `align_citations` 在**长提示**下必然抛
  `OutputParserException: Invalid json output: `（空串）；短提示下却正常，表现为"时好时坏"
- **定位**：直接打印模型原始响应 → `content` 长度 **0**、`done_reason=length`、
  `eval_count=512`（正好等于 `OLLAMA_NUM_PREDICT`）。即 Qwen3 把全部输出预算
  花在思考上，可见内容为空
- **解决**：新增 `get_structured_model()`（`reasoning=False`，关闭思考），
  用于**结构化判定类**调用：重排、逐句归因、QA 质检、Router 短提示词。
  **生成类**调用（RAG 生成 / Synthesis / Query Rewrite）保留思考
- **效果**：LLM 重排由"3/3 失败"变为 3/3 成功，且单次由超时失败降到 **0.7–1.0s**
- **状态**：✅ 已解决（连带修复 `test_rerank.py`、`test_citation_align.py` 两个既有失败）

### P25. 路由关闭思考的准确率/延迟权衡

- **现象**：Router 关闭思考后延迟 6.1s → 1.1s，但 20 题准确率 16/20 → 14/20
- **解决**：**两级不同设置**——轻量级（短提示词）关思考求快；
  完整提示词复判一级开思考求准（该级仅在轻量级输出无效/冲突时触发，成本可摊销）
- **效果**：分层路由（现已固化）下 20/20（100%），平均 **0.07s**（19 题走规则 0ms）
- **状态**：✅ 已解决

### P26. SSE 流式端点下 contextvars 埋点失效

- **现象**：`/api/chat/stream` 抛 `ValueError: Token ... was created in a different Context`；
  修掉报错后发现分阶段耗时**全部丢失**（只剩 `total_ms`）
- **根因**：同步生成器被 ASGI 服务器放线程池**逐次迭代**，每次 `next()` 运行在事件循环
  Context 的不同副本中——`set` 的值传不到下一次迭代，`reset(token)` 也跨副本失败
- **解决**：`stream_chat` 把整条流放进**一个工作线程**跑完，事件用 `queue` 按序桥接
  （SSE 事件顺序与协议不变）；`profiler_scope` 的 reset 同时加跨副本容错
- **效果**：流式路径恢复完整埋点（precheck/embedding/retrieval/rag_gen/synthesis/qa 齐全）
- **状态**：✅ 已解决

### P27. 评测集残留旧语料导致 ground truth 污染

- **现象**：大知识库评测里「带商城前缀」类问题命中率异常低（29%），且**所有重排方法数值完全相同**
  —— 这是"答案根本不在候选池"的典型特征，但逐题检查却发现答案稳定排第 1
- **根因**：语料结构改版后，**旧版文件仍留在 `data/eval/kb_large/`**（13 个文件而非 5 个）。
  新旧语料用同一批数值公式 → 同一"标注定位串"在两个片段中都出现，
  `next(...)` 定位到错误片段，评测的 ground truth 被污染
- **解决**：生成脚本改为**整体重建自有目录**（带路径断言），并新增**标注定位串全库唯一性自检**
- **状态**：✅ 已解决（教训：评测语料变更必须清理旧产物 + 校验标注唯一性）

### P28. `file_hash` 按文件去重，按 chunk 分批入库会静默丢数据

- **现象**：修正 P27 后重跑，base 规模 baseline Hit@1 从 92.86% 掉到 66.67%，
  且"45% 的问题连 top-10 都召回不到"——看起来是检索能力大幅退化
- **根因**：`ingest_documents` 按 **`file_hash`（整个文件内容的 MD5）** 去重。
  为规避"一次提交 382 条 embedding 导致 Ollama 报错"，把入库改成了**按 chunk 数量分批**，
  于是同一文件第二批之后的 chunk 全部被当作"已入库"跳过：
  **130 个 chunk 实际只入库 104 个，且不报任何错**
- **解决**：改为**按文件分批**（保持 `file_hash` 去重语义正确），
  并断言"实际入库数 == 预期数"，不再信任返回值
- **状态**：✅ 已解决（教训：核对隔离环境的**实际规模**，不要相信代码返回值；
  分批切分要理解批处理键的语义）

---


| 编号 | 项 | 结论 | 依据（实测） |
| --- | --- | --- | --- |
| N1 | mixed 路由并行 | **固化串行**（不再保留并行开关） | 单卡 GPU 上 Ollama 推理本身串行：并行 180.7/205.8s vs 串行 148.7/199.3s，**无收益** |
| N2 | 专用 Reranker（`bge-reranker-v2-m3`） | **不引入该模型** | 显存合计 ≈9GB > 空闲 6.8GB；后续 ONNX 方案在中文语料效果不如 LLM-as-Reranker，现已移除 |
| N3 | QA 逐句归因默认开启 | **仅离线 `--align`** | 每题约 **+100s**；改作离线质检（`evaluate_system --align`） |
| N4 | 用 Ollama 跑 Qwen3-Reranker-0.6B 做专业重排 | **改走 ONNX** | Ollama 0.34 **无原生 rerank 端点**（`/api/rerank` → 404）；社区 GGUF 以普通因果 LM 加载后打分头不可用（实测输出为标点乱码、`logprobs` 全为 0），拿不到相关度分数 |
| N5 | Qwen3-Reranker-0.6B（ONNX q4）作默认重排器 | **改用 bge-reranker-base int8** | 同一检索集实测：bge-base **MRR 0.80**、Qwen3-0.6B q4 **MRR 0.70**，且 Qwen3 慢约 **5 倍**（21.4s vs 3.9s / 5 题）。Qwen3-Reranker 的 ONNX 仓只有 q4/quantized 量化版，yes/no logit 精度受损（官方推荐 Q8_0；Ollama 版正是 Q8_0 但无法取分） |
| N6 | 在线 RAG 默认启用重排 | **固化 LLM 重排** | 在线始终向量 Top-K → LLM → Top-N；仅保留在中文语料中效果更好的实现 |

> 记录负结论比"假装优化过"更有工程价值。

### 8.1 Reranker 补充说明（模型职责重构后）

- **统一实现**：线上仅保留 `LLMReranker`，由主 Qwen3 模型完成候选排序。
- **结构化稳定性**：重排使用 `get_structured_model(reasoning=False, num_predict=128)`，避免思考 token 耗尽导致空结构化输出。
- **文本覆盖**：候选片段截断从 300 提高到 800 字符，减少政策条件位于段尾时被漏判的风险。

---

## 9. 最终成果

### 9.1 功能（15 个阶段全部完成）

- **多 Agent 工作流**：Supervisor（结构化路由）→ RAG / Tool / Mixed → Synthesis → QA → 有限 Retry
- **企业知识库 RAG**：多格式入库（md / markdown / txt / pdf / docx）→ 切分 → BGE-M3 → Chroma（`file_hash` 去重）
- **可验证的检索**：baseline vs optimized（LLM Reranker），**MRR 0.9 → 1.0**
- **可靠的工具调用**：`Agent → Tool → queries → Repository → MySQL`（6 个业务工具，禁止 LLM 生成 SQL）
- **多轮对话**：Query Rewrite 指代消解 + MySQL 会话记忆（重命名 / 单删 / **全选批量删除**）
- **质检与溯源**：QA/Validator + 有限重试；citation 可追踪 chunk，前端按文档分组展示
- **流式输出**：SSE 只暴露用户可见事件（`status` / `tool` / `token` / `done`）
- **前端（无构建）**：Markdown 流式渲染、source card、工具状态、常见问题面板（输入框上方，聚焦时展示）、知识库管理页
- **接口**：`chat` / `chat/stream` / `documents` / `conversations`（含重命名·删除·批量删除）/ `cache` / `faq` / `health`

### 9.2 性能

| 阶段 | RAG 单题 | mixed 单题 |
| --- | ---: | ---: |
| 初始 | 189.2s | 205.8s（带 P12 bug 时 40+ 分钟） |
| ＋ QA 规则先行 | 160.9s | — |
| ＋ `num_ctx` 显存驻留 | — | **47.5s** |
| ＋ 输出长度限制 | **28.2s** | — |
| 命中缓存 | **0.0s** | — |

### 9.3 质量

| 指标 | 结果 |
| --- | --- |
| 显存驻留 | **100%**（qwen3 5.58GB + bge-m3 0.66GB） |
| 测试 | **114 个**（73 快速 + 41 slow）；快速套件约 **1.3s** |
| 评估指标 | Routing 100% / Retrieval Hit 100% / Citation 100% / Answer 100% / QA Pass 100% |
| 静态检查 | Python / JavaScript / Markdown / CSS 全部 clean |
| 文档 | README + `docs/`（ARCHITECTURE / API / METRICS / INTERVIEW / TROUBLESHOOTING） |

---

## 10. 经验教训

1. **先测量，再优化**：189.2s 的分解显示检索只占 2%，大头是重复生成与显存溢出——凭直觉优化会做错方向。
2. **本地部署先确认「是否全在显存」**：`num_ctx` 一个参数造成 59% → 100% 驻留差异，收益超过换模型。
3. **「内容检查」不等于「运行时验证」**：P14 那个 bug 正是只 grep 了 served 文件字符串；现已固化为 Node 运行时冒烟测试。
4. **批量替换后必须验证**：sed 批量改 `querySelector` 漏 `#` 造成整站失效——机械替换后必须跑一次运行时校验。
5. **负结论也要记录**：并行无收益、专用 Reranker 不可行，写进文档比"假装优化过"更有价值。
6. **测试要能自己收尾**：缓存、测试会话等副作用必须显式隔离/清理，否则会污染后续测试与前端展示。
