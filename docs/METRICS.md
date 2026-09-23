# 评估指标（Evaluation Metrics）

> 通过 `uv run python -m app.scripts.evaluate_system` 运行，基于 `data/eval/test_questions.json`。
> 受本地 CPU 推理速度限制，报告为**覆盖关键类别的代表性子集**运行结果。

## 1. 运行方式

```powershell
# 全量测试集（20 题，估算 50 分钟以上，取决于本机推理速度）
uv run python -m app.scripts.evaluate_system

# 指定子集
uv run python -m app.scripts.evaluate_system --ids q_rag_001,q_tool_001,q_mixed_001

# 前 N 题
uv run python -m app.scripts.evaluate_system --limit 5

# 完整 JSON
uv run python -m app.scripts.evaluate_system --json

# 额外开启 Citation 逐句归因（每题多一次 LLM 调用，约 +100s/题）
uv run python -m app.scripts.evaluate_system --align
```

> 脚本会**逐题打印进度**（`[i/N] id route=... qa=... latency=...`），便于观察与中断。

## 2. 指标定义

| 指标 | 定义 |
| --- | --- |
| Routing Accuracy | `实际路由 == 期望路由` 的比例 |
| Tool Calling Accuracy | 需要 Tool 的问题中，实际调用了业务工具的比例 |
| Retrieval Hit Rate | 需要 RAG 的问题中，检索返回非空溯源的比例 |
| Citation Accuracy | RAG 溯源中 `document_id` / `chunk_id` 均有效的比例 |
| Citation Alignment Rate | 逐句归因后**无依据句子为 0** 的比例（需 `--align`；未采集时为 null） |
| Answer Accuracy | 回答命中 `answer_keywords` 的比例 |
| QA Pass Rate | 通过 QA/Validator 质检的比例 |
| Retry Rate | 平均重试次数 |
| Latency | 单题平均 / 最大耗时 |

## 3. 代表性子集结果

子集（覆盖 rag / tool / mixed / multi_turn / error / ambiguous）：

```text
q_rag_001, q_tool_001, q_mixed_001, q_multi_001, q_err_001, q_amb_001
```

| 指标 | 结果 |
| --- | --- |
| 样本数 | 6 |
| Routing Accuracy | **100.00%** |
| Tool Calling Accuracy | **75.00%** |
| Retrieval Hit Rate | **100.00%** |
| Citation Accuracy | **100.00%** |
| Answer Accuracy | **100.00%** |
| QA Pass Rate | **100.00%** |
| Retry Rate | 0.00 |
| Latency Avg / Max | 377.5s / 1542.7s |

说明：

- `q_amb_001`（输入仅“键盘”，模糊问题）触发了较长的工具/质检链路，是延迟最大值的主要来源。
- Tool Calling Accuracy 75% 表示 4 题需要 Tool 的问题中有 1 题未调用业务工具而直接作答。
- 延迟为本地 CPU 推理（Qwen3 8B）结果，**非生产环境指标**。

## 3.1 模型职责重构后的对比测量（6 题子集，同题同口径）

子集：`q_rag_002, q_rag_004, q_tool_001, q_tool_002, q_mixed_001, q_multi_001`（rag×2 / tool×2 / mixed×1 / multi_turn×1）。

| 指标 | Baseline（当时 `ROUTER_MODE=llm`） | Optimized（分层 `auto`，现已固化） |
| --- | ---: | ---: |
| Routing Accuracy | 100.00% | 100.00% |
| Tool Calling Accuracy | 100.00% | 100.00% |
| Retrieval Hit Rate | 100.00% | 100.00% |
| Citation Accuracy | 100.00% | 100.00% |
| Answer Accuracy | 100.00% | 100.00% |
| QA Pass Rate | 100.00% | 100.00% |
| Degrade Rate / Retry Rate | 0.00% / 0.00 | 0.00% / 0.00 |
| Latency Avg | 25.0s | 23.5s |
| Latency P95 | 36.1s | 36.4s |
| `router` 阶段平均 | 5988 ms | **0 ms**（规则命中 6/6） |
| LLM 调用次数 / 请求 | 3.8 | 3.7 |

**路由阶段专项对比**（20 题评测集直测 `route_query`）：

| 方案 | 准确率 | 平均延迟 | 每题 LLM 调用 |
| --- | ---: | ---: | ---: |
| Baseline（legacy 单级 LLM） | 17/20（85%） | 6.79s | 1 次 |
| Optimized（分层升级，现已固化） | **20/20（100%）** | **0.07s** | 19 题 0 次 / 1 题 1 次 |

**延迟剖析前后对比**（`uv run python -m app.scripts.profile_latency`，6 题，清缓存）：

| 阶段 | Baseline | Optimized |
| --- | ---: | ---: |
| total 平均 | 25789 ms | **18372 ms** |
| total P95 | 36520 ms | **31393 ms** |
| `router` | 6946 ms | **0 ms** |
| LLM 调用次数 / 请求 | 3.8 | **2.8** |

> 确定性收益是**消除路由的 LLM 调用**（`router` 5.99–6.95s → 0ms，每请求 LLM 调用 −1.0）；
> 端到端 total 降幅在两次测量中为 −7.4s 与 −1.5s，差异来自生成阶段运行间波动（单次 ±1–2s）。
> 详见 [`ARCHITECTURE.md §2.10`](ARCHITECTURE.md#210-模型职责边界与结构化输出)。

## 3.2 Reranker 选型实测（5 题检索集）

| 实现 | Hit | MRR | 耗时（5 题） | 显存 |
| --- | ---: | ---: | ---: | --- |
| baseline（纯向量检索） | 1.00 | 0.90 | 1.2s | — |
| `llm`（Qwen3 8B） | 1.00 | **1.00** | 4.3s | 复用已驻留显存 |
| `cross_encoder` bge-reranker-base int8 | 1.00 | 0.80 | 3.7s | **0（CPU）** |
| `cross_encoder` bge-reranker-base fp32 | 1.00 | 0.80 | 6.3s | **0（CPU）** |
| `cross_encoder` Qwen3-Reranker-0.6B q4 | 1.00 | 0.80 | 3.6s | **0（CPU）** |

> 评测基于初始知识库（5 文档/9 chunks）。当前知识库已扩展至 13 文档/32 chunks，
> 当前线上已固化 LLM 重排（向量 Top-K → LLM → Top-N）；
> 上表保留为历史选型数据，ONNX cross-encoder 已不再随项目发布。

## 3.3 大知识库重排评测（构造评测集，观察"知识库规模"的影响）

生产知识库只有 9 个 chunk（top-4 覆盖全库 44%），无法评判重排价值。
为此构造同构的大知识库评测集：**12 个共享词根的品类变体 × 9/30 个政策维度**，
每格一条独立事实（数值互不重复）、一段一片、含大量难负例，并混入**口语化转述提问**
（如「插线用的那款耳机，免费保修期多长？」）制造真实的语义检索难度。

```powershell
uv run python -m app.scripts.gen_large_kb --scale base   # 130 chunk
uv run python -m app.scripts.gen_large_kb --scale full   # 382 chunk
uv run python -m app.scripts.eval_rerank_large --label base   # 隔离 collection 评测
```

### 结果（top_k=6 候选 → top_n=3）

| 规模 | 方法 | Hit@1 | Hit@3 | MRR@3 | 单题耗时 |
| --- | --- | ---: | ---: | ---: | ---: |
| **130 chunk**<br>42 题 | baseline（向量 top-3） | 92.86% | 100% | 0.964 | 164ms |
| | pool_control（top-6 取前 3，不重排） | 92.86% | 100% | 0.964 | 167ms |
| | cross_encoder（bge int8） | **95.24%** | 100% | **0.972** | 278ms |
| | cross_encoder（Qwen3-0.6B q4） | 88.10% | 100% | 0.932 | 2112ms |
| | llm（Qwen3 8B） | 90.48% | 100% | 0.952 | 1189ms |
| **382 chunk**<br>82 题 | baseline | 89.02% | 100% | 0.943 | 165ms |
| | pool_control | 89.02% | 100% | 0.943 | 168ms |
| | cross_encoder（bge int8） | **90.24%** | 98.78% | 0.943 | 261ms |
| | cross_encoder（Qwen3-0.6B q4） | 75.61% | 91.46% | 0.825 | 2084ms |
| | llm（Qwen3 8B） | **90.24%** | 100% | **0.951** | 1304ms |

### 按提问风格分组（382 chunk，揭示重排真正有效的场景）

| 提问风格 | baseline Hit@1 | 最佳重排 Hit@1 | 说明 |
| --- | ---: | ---: | --- |
| 直接点名型号（28 题） | 96.43% | 96.43% | 字面可匹配，**重排无增益**（Qwen3-q4 反降至 89.29%） |
| **口语化转述（27 题）** | 74.07% | **81.48%**（llm） | **重排唯一显著有效的场景（+7.4pp）** |
| 带商城前缀（27 题） | 96.30% | 100%（cross_encoder） | 小幅增益 +3.7pp（Qwen3-q4 反降至 70.37%） |

### 召回诊断：瓶颈在召回还是排序？

纯向量检索下答案片段的排名分布：

| 规模 | Recall@1 | Recall@3 | Recall@6/@10 | 排名 2–3 | 排名 4–10 | 未召回(>10) | 重排可改善空间 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 130 chunk | 92.9% | **100%** | 100% | 7.1% | 0% | 0% | **7.1%** |
| 382 chunk | 89.0% | **100%** | 100% | 11.0% | 0% | 0% | **11.0%** |

**结论**：

1. **向量检索已经把答案稳定放进 top-3**（两个规模 Recall@3 均为 100%），
   因此重排**结构上无法改善 Hit@3**，只能改变第 1 位与第 2/3 位的顺序。
2. 而生产链路把 **4 个片段全部**送进生成提示词，顺序对"模型能否看到答案"没有影响
   —— 这解释了为什么在线启用重排实测无收益。
3. 重排的**可改善空间随规模增长但很小**（7.1% → 11.0%），且实测增益基本在其之内
   （最佳 +1.2~+2.4pp Hit@1），代价是每题 +100ms（cross-encoder）到 +1.3s（LLM）。
4. **重排真正有效的场景是"查询与文档存在字面鸿沟"**（口语化转述 +7.4pp），
   而不是"知识库大"本身——这与第 3.2 节小知识库的观察一致。
5. `Qwen3-Reranker-0.6B` 的 q4 量化版在两个规模上**都明显劣化**（Hit@1 −4.8~−13.4pp），
   印证"该模型 ONNX 仓缺少高精度版本"这一分发渠道限制。

## 4. RAG 检索 Baseline vs Optimized

`uv run python -m app.scripts.evaluate`（top_n=2, top_k=6）：

| 指标 | Baseline（纯向量） | Optimized（+Rerank） |
| --- | --- | --- |
| Hit@2 | 1.0 | 1.0 |
| MRR | 0.9 | **1.0** |

Rerank 将部分问题的相关片段从第 2 位提升到第 1 位。

## 5. 扩展项实测

| 扩展 | 状态 | 实测 | 结论 |
| --- | --- | --- | --- |
| **mixed 并行**（RAG ∥ Tool） | 已固化串行 | 并行 180.7 / 205.8s vs 串行 148.7 / 199.3s | 单卡 8GB 上 Ollama 推理本身串行，**并行无收益甚至略慢** |
| **QA Citation 逐句对齐** | 仅离线 `--align` | 单题 **279.2s** vs 未开启约 **180s** | 准确但**约 +100s**，建议离线批量跑 |
| **前端 Markdown 流式渲染** | 默认开启 | 60ms 节流增量渲染 | 回答可读性质变（此前是纯文本），后端零开销 |
| **专用 Reranker** | 不引入 | 显存合计 ≈ 9GB > 空闲 6.8GB | 8GB 卡上**不推荐**（见 [`INTERVIEW.md`](INTERVIEW.md) 6.3） |

## 6. 延迟优化实测

单题延迟分解（`qwen3:8b` + RTX 4060 Laptop 8GB，问题「商城退货政策是什么？」共 **189.2s**）：

| 节点 | 耗时 | 占比 |
| --- | ---: | ---: |
| `rag.generate` | 69.9s | 37% |
| `synthesis`（二次重写） | 57.7s | 30% |
| `qa`（LLM 质检） | 33.3s | 18% |
| `supervisor` | 23.9s | 13% |
| `rag.retrieve` | 4.3s | 2% |

已实施的优化：

| 优化 | 配置 / 行为 | 实测 |
| --- | --- | --- |
| **显存驻留**（`num_ctx` 32768 → 4096） | `OLLAMA_NUM_CTX=4096` | 模型 **59% → 100% 驻留显存**；mixed **205.8s → 47.5s** |
| **QA 规则先行** | 已固化 | **189.2s → 160.9s（−28s）**；`checked_by=rule`（未调 LLM） |
| **输出长度限制** | `OLLAMA_NUM_PREDICT=512` + prompt 简明 | 回答 ~700 字 → **199 字**；RAG 单题 **160.9s → 28.2s** |
| **回答缓存** | `ANSWER_CACHE_ENABLED=true`（默认） | 重复问题 **28.2s → 0.0s**，答案与溯源一致 |

> 评估脚本的 Latency 指标未包含这两项优化前后的对照，因两者均为**可选/命中相关**（缓存对首次提问无效）。

## 7. 结论

- 路由、检索命中与溯源在代表性子集上表现稳定。
- QA 质检全部通过，未出现幻觉导致的失败。
- 模糊输入（“键盘”）：已改为强制检索 + 多结果澄清（见 §7.1），不再依赖 LLM 空口作答。
- 逐句归因适合作为**离线质检**手段：它能定位“哪句没依据”，而不只是“有没有引用”。
- 已如实记录**负结论**（并行无收益、专用 Reranker 受显存限制），避免为“高级感”堆砌无效优化。

### 7.1 品类模糊澄清（已落地）

纯品类短查询（如「键盘」）在 Tool Agent `init` 规则短路：强制 `search_products`（0 次 LLM），
多结果时列选项反问；主图 synthesis 对该终稿跳过二次改写。相对原先整链 LLM+质检，延迟应下降，
且 `tool_calls` 非空，避免 Tool Calling Accuracy 因「空口答」被误伤。
