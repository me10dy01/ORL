# 面试讲解文档

星辰数码商城智能客服系统 —— 项目讲解要点与常见问答。

---

## 1. 一句话介绍

一个运行在 Windows 本机、完全本地化（本地 LLM + Embedding）的智能客服 Demo，
用 **LangGraph Multi-Agent** 把 RAG、Tool Calling、质检、溯源、流式输出串成一条可解释、可验证的工程链路。

## 2. 项目亮点

1. **Agent 边界清晰**：仅 4 个核心 Agent（Supervisor / RAG / Tool / QA），职责单一，拒绝为“高级感”堆 Agent。
2. **可解释的路由**：Supervisor 输出结构化 `{route, reason, tasks}`，支持 rag / tool / mixed；
规则优先分层决策（`rules` / `llm_short` / `llm_full` / `fallback`）+ 一致性冲突校验，路径可追溯。
3. **可靠的数据访问**：`Agent → Tool → Service → Repository → MySQL`，禁止 LLM 生成 SQL。
4. **多格式知识入库**：md / markdown / txt / pdf / docx → 切分 → BGE-M3 → Chroma（`file_hash` 去重）。
5. **可验证的 RAG**：baseline 与 optimized（Rerank）并存，用测试集量化对比（MRR 0.9 → 1.0）。
6. **有限的可靠性闭环**：QA 质检 + 有限 Retry（MAX_RETRY=2）+ 可选逐句归因。
7. **可追溯的溯源**：回答保留 `document_id / chunk_id / source`，前端以 source card 展示。
8. **用户可见事件与内部事件分离**：Streaming 只暴露有意义的状态与答案 token。
9. **前端无构建但有体验**：原生 JS + 本地 vendor 实现 Markdown 流式渲染、停止生成。
10. **工程闭环且诚实**：测试集 + 评估脚本 + 指标 + 文档，并如实记录「并行无收益」等负结论。

## 3. 关键设计决策

| 决策 | 原因 |
| --- | --- |
| 本地 Ollama（Qwen3 + BGE-M3） | 需求要求全本地、无云端 API、无 Key |
| LangGraph 自建 StateGraph（非 prebuilt agent） | 便于控制状态、子图嵌入与可解释性 |
| Supervisor 用 `with_structured_output` | 路由必须是结构化、可校验的 |
| Tool 统一返回 `ToolResult` | 参数缺失/非法/不存在/异常统一处理，不静默吞异常 |
| 子图契约 `query → answer(+sources/tool_calls)` | 主图不关心内部实现，可复用、可测试 |
| 知识文档只进 Chroma；MySQL 只存业务数据 + 对话历史 | 数据形态不同 → 检索方式不同 |
| Memory 存 MySQL 而非塞进 State | Workflow State 与跨请求 Memory 必须分离 |
| Rerank 用 cross-encoder（CPU 推理） | 专业 ONNX 模型 ~279MB，CPU 推理零显存，不与 LLM 争抢 GPU |
| 逐句归因默认关闭 | 准确但每题 +约 100s，只适合离线/抽样 |
| mixed 默认串行 | 单卡 GPU 推理串行，实测并行无收益 |
| 前端无构建步骤（原生 JS + vendor） | 满足“无 Docker、无 Node、全进程本地运行” |

## 4. 常见问答

**Q：为什么只有 4 个 Agent？**
A：Agent 数量少而职责清晰。Supervisor 负责路由，RAG 负责知识，Tool 负责业务数据，QA 负责可靠性。
Planner/Critic/Router 等会带来职责重叠与不可解释性，除非有明确架构评审，否则不引入。

**Q：Supervisor 会不会成为瓶颈或越权？**
A：Supervisor 只输出结构化路由，不接触数据库、向量库与工具，因此不会越权。
并且它**已经是"规则 → 轻量 LLM → 完整 LLM → 保守兜底"的分层结构**：
规则前置判定 0 次 LLM（毫秒级），判定不了或与输入实体特征冲突时才升级 LLM 复判。
实测 20 题路由集：准确率 **85% → 100%**、平均延迟 **6.79s → 0.07s**，
`router` 阶段在整链耗时中的占比从约 27% 降到 **0**（详见 [`ARCHITECTURE.md §2.10`](ARCHITECTURE.md#210-模型职责边界与结构化输出)）。
`ROUTER_LLM_MODEL` 还可把 Router 指向更小的模型，无需改代码。

**Q：如何防止 Tool Agent 乱写 SQL？**
A：Tool Agent 只能调用预定义业务工具，工具参数有 schema 校验，数据访问固定在 Service/Repository 层，
LLM 无法自由生成 SQL。

**Q：为什么对话历史（“模型记忆”）放 MySQL？**
A：LLM 本身**无状态**，多轮必须由我们自己存历史。它需要**跨请求持久化**、**按 `conversation_id` 有序取最近 N 条**，
这是关系库强项；而 MySQL 项目里本来就有，复用即**零新增基础设施**。
内存 dict（重启即丢、多进程不共享）、文件（并发/查询差）、Redis（多跑一个服务）都更差。

**Q：知识文档（md/pdf/…）为什么进 Chroma，不进 MySQL？**
A：两者**数据形态与检索方式不同**：政策文档是**非结构化文本**，需要**语义相似召回**（向量库）；
订单/库存是**结构化数据**，需要**精确查询与事务**（关系库）。
`data/documents/*` 是**原始素材**，Chroma 存的是它的**派生向量索引**，原文不进 MySQL。

**Q：RAG 检索不准怎么办？**
A：先 baseline（向量 Top-K），再叠加 Rerank（Top-K → 重排 → Top-N），用测试集比较 Hit@N 与 MRR；
参数（top_k/top_n）经实测决定，不预设“最优”。

**Q：幻觉怎么控制？**
A：三层：① RAG 提示词要求“无依据就明说”；② Tool 结果来自真实数据库；
③ QA/Validator 检查事实错误、上下文冲突与无依据事实，不通过则带反馈重试。

**Q：如何检测“哪一句没依据”？**
A：`citation_align.py` 把回答切句，逐句回溯上下文，输出 `unsupported_sentences` 与 `supported_ratio`。
它比“有没有引用”更细粒度，但每题多一次 LLM 调用（约 +100s），所以**默认关闭**，建议离线评估用。

**Q：为什么引用要按文档分组？**
A：`citations` 是**片段级**的：同一份文档被切成多个 chunk，可能同时命中多个，直接平铺会让**同一文档出现多次**
（且 chunk 顺序随相似度变化），看起来像重复。前端按 `document_id` 分组为「一篇文档一张卡片 + chunk 列表」，
既消除重复观感，又保留 chunk 级溯源。

**Q：Retry 会不会死循环？**
A：不会。`MAX_RETRY=2` 硬上限；LangGraph 另有 `recursion_limit` 兜底。

**Q：多轮对话怎么处理指代？**
A：`Query Rewrite` 在 Supervisor 之前，结合历史把“那运费谁承担？”改写为
“星辰数码商城 K87 退货时的运费由谁承担？”，再交给 Supervisor 路由。
历史存 MySQL，按 `conversation_id` 取最近 N 条，控制上下文长度。

**Q：Streaming 暴露了内部事件吗？**
A：没有。内部 LangGraph 事件被映射为用户可见事件（`status/tool/token/done`），
内部节点（`prepare_*` / `collect_*` / `retry` 等）不暴露。

**Q：前端怎么渲染 Markdown？XSS 怎么防？**
A：本地 vendor `marked` + `DOMPurify`（无构建）。流式时**60ms 节流**增量重渲染，容忍半截 Markdown。
安全上先 `DOMPurify.sanitize(...)`，再用 `DOMParser` + `replaceChildren` 落入 DOM，**完全绕开 `innerHTML` 这个 XSS sink**。

**Q：单卡上 mixed 并行为什么没收益？**
A：Ollama 在**单张 GPU 上推理本身是串行的**，两个子图只是排队；并发请求还可能让 `bge-m3` 与 `qwen3` 争显存。
实测并行 180.7/205.8s vs 串行 148.7/199.3s → **无收益**，故默认串行（保留开关供多卡/远程场景）。

**Q：客服场景延迟怎么降下来？**
A：先实测定位瓶颈（不靠猜）。核心发现是**大头在重复劳动**（答案被生成两遍），而非检索。
累计优化 **189.2s → 28.2s（约 6.7×）**：显存驻留、QA 规则先行（−28s）、输出长度限制、回答缓存、规则优先路由。
下一步（按性价比）：去掉单意图的二次生成 → 路由换小模型 → 换 `qwen3:4b`。
> 详细分解与实测数据见 [`METRICS.md`](METRICS.md) §6。

**Q：缓存怎么保证不返回过期答案？**
A：三重保证：① 缓存键含**模型名**，换模型天然失效；② 上传/删除知识库文档时 `management` **主动清空**缓存；
③ 只缓存**改写后的问题**（已消解指代），避免多轮上下文串味。
缓存是**精确匹配**（不做模糊命中），宁可回落走完整链路，也不返回不相关答案。

**Q：本地部署最容易被忽略的性能杀手是什么？**
A：**上下文窗口（`num_ctx`）**。Ollama 默认 32768 会让 KV cache 占满显存 → 模型被卸载到 CPU → 整体慢数倍。
降到 4096 后全部驻留显存，**单这一项 mixed 就从 3 分钟回到 47.5s**。
验证手段：`nvidia-smi` 与 `curl http://127.0.0.1:11434/api/ps`（看 `size_vram` 是否等于 `size`）。
**排查顺序建议：先确认是否全在显存，再谈模型大小/量化/并行。**
> 详细数据见 [`METRICS.md`](METRICS.md) §6 与 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。

**Q：QA 规则先行会不会漏掉幻觉？**
A：规则只做**结构判定**，且**只会“快速通过”结构完整且与上下文词面一致的答案**；
词面重合度过低（疑似编造）时不直接判失败，而是 `return None` **交回 LLM 复核**——即规则不会误伤好答案。
代价是深度幻觉检测变弱，需要时可切 `QA_MODE=llm`，或用 `--align` 跑逐句归因。

**Q：怎么保证可测试？**
A：分层解耦 + pytest 标记：默认跑**快速测试**（70 个，约 1s），LLM 相关用 `--run-slow` 按需运行；
另配系统级评估脚本产出量化指标。前端另有 **Node DOM 冒烟测试**（`tests/smoke_app_js.cjs`）：
以最小 DOM stub 执行 `app.js` 顶层初始化，并严格校验每个 `querySelector` 都是页面真实存在的 `#id`。

## 5. 可量化指标（评估脚本）

- Routing Accuracy
- Tool Calling Accuracy
- Retrieval Hit Rate / Recall@K
- Citation Accuracy
- Citation Alignment Rate（需 `--align`）
- Answer Accuracy
- QA Pass Rate
- Retry Rate
- Latency

运行：`uv run python -m app.scripts.evaluate_system`
实测结果见 [`METRICS.md`](METRICS.md)。

## 6. 扩展方向

### 6.1 已实现（本轮扩展）

| 方向 | 实现 | 开关 / 用法 | 实测 |
| --- | --- | --- | --- |
| **前端 Markdown 流式渲染** | 本地 vendor `marked` + `DOMPurify`；增量渲染（60ms 节流）、半截 Markdown 容错、流式光标、停止生成 | 默认开启 | 回答可读性质变（此前是纯文本） |
| **mixed 路由并行**（默认关闭） | `mixed_parallel_node` 用线程池并发跑 RAG 与 Tool 子图 | `MIXED_PARALLEL=true` 开启 | 单卡 8GB：并行 180.7/205.8s vs 串行 148.7/199.3s → **无收益**，故默认串行 |
| **QA Citation 逐句对齐**（默认关闭） | `app/agents/citation_align.py`：切句 → 逐句回溯上下文 → 标出无依据句子 | `QA_SENTENCE_ALIGNMENT=true`；评估用 `--align` | 单题 +约 100s（279s vs ~180s） |

### 6.2 待做（按重要性）

1. **QA 逐句对齐的落地方式**：已实现但延迟高 → 建议**离线批量**跑或仅抽样。
2. **异步化与并发**：`async def` 端点 + `astream`、异步 DB、阻塞调用丢线程池。
3. **Memory 摘要（历史压缩）**：`conversation_summaries` 表 + 超阈值压缩，注入「摘要 + 最近 N 条」。
4. **专用 Reranker 已落地**：bge-reranker-base int8（ONNX，CPU 推理，~279MB，零显存），已设为默认。
5. **前端组件化框架**：拆组件 + 响应式状态（代价：引入 Node/构建，与“无构建”取舍冲突）。

### 6.3 硬件约束：加 Reranker 会爆显存吗（实测）

本机：**RTX 4060 Laptop 8GB**（空闲 6.8GB），内存 16GB。

| 组件 | 显存 |
| --- | --- |
| `qwen3:8b`（Q4 权重） | ~5.2 GB |
| `qwen3:8b` KV cache | ~1–2 GB |
| `bge-m3` | ~1.16 GB |
| `bge-reranker-v2-m3`（若加） | ~1.1 GB（FP16） |
| **合计** | **≈ 9 GB > 6.8 GB** |

**结论**：不会崩溃（Ollama 会把层卸载到 CPU/内存），但会**严重掉速 + 模型反复换入换出**。

**建议**：

- 已采用 **cross-encoder reranker**（bge-reranker-base int8 ONNX，CPU 推理，~279MB，零显存占用）
- LLM-as-Reranker 保留为备选方案（复用 Qwen3，但占用推理时间 ~1s/query）
- 若确需专用 reranker → **跑 CPU**（只对 6 个候选打分，约 50–300ms）
- 配置 `OLLAMA_MAX_LOADED_MODELS=1`；用 `nvidia-smi` 与 `curl http://127.0.0.1:11434/api/ps` 观察 `size_vram`
- ⚠️ 若溢出到内存：5.2+1.2+1~2+Python/Chroma/OS(4~6) ≈ 12~15GB，**16GB 易触发 swap**

### 6.4 各方向具体做什么

#### Memory 摘要（历史压缩）

- 现状：`get_recent_messages(limit=10)`，更早的信息直接丢失
- 做：超阈值时把「旧摘要 + 新消息」压成摘要；注入「摘要 + 最近 N 条」
- 收益：上下文有界、长对话不丢信息、延迟可控

#### QA 逐句对齐

- 现状：只看 `citation_valid`（有没有引用），无法定位哪句没依据
- 做：切句 → 每句回溯上下文 → `unsupported_sentences`
- 收益：真正的幻觉检测；可做“逐句高亮来源”；成本：N 句 × 调用

#### 异步化与并发

- 现状：端点多为同步；mixed 可选并行；DB 同步
- 做：`async def` + `astream`、异步 DB、阻塞调用丢线程池
- **诚实边界**：单卡 GPU 上 Ollama 推理串行，并发只省 IO/DB 等待与可并行编排，**不提升 LLM 吞吐**

#### 前端组件化 + 流式渲染

- 组件化：`MessageList` / `MessageBubble` / `CitationCard` / `ToolChip` / `ConversationSidebar`… + 响应式状态（代价：需构建）
- 流式渲染：**已实现**（见 6.1）

### 6.5 优先级

- **按面试价值**：QA 逐句对齐 > 异步并发 > Memory 摘要 > 前端渲染 > 专用 Reranker
- **按性价比**：前端渲染 > mixed 并行 > Memory 摘要 > QA 逐句对齐 > 专用 Reranker
- **按目标选**：
  - 演示好看 → 前端渲染（已做）→ mixed 并行（已做）
  - 技术纵深 → QA 逐句对齐（已做，建议离线化）
  - 性能上量 → 异步并发
  - **专用 Reranker 在 8GB 卡上性价比最低**
