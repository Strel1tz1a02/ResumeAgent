# Eval 测评说明

普通测试回答“代码是否按预期执行”，Eval 回答“模型输出的质量是否真的达标”。本目录覆盖旧版简历定制、会话记忆压缩与多轮容量边界，以及召回、JD 导入、经历导入、简历生成、经历改写五条核心能力链路。

## 两类测评

### 1. 确定性结构检查

这类测试不调用 LLM，不需要网络，也没有费用，默认测试会执行。

简历定制评分器位于 `scorers.py`，检查：

- 原有简历章节是否保留；
- 是否捏造雇主；
- JD 关键词覆盖率；
- 输出是否符合 `ResumeData`；
- 个人身份信息是否被修改。

`test_scorers.py` 会分别使用正确和错误样本，证明每个评分器确实能发现问题。

### 2. 真实 LLM 测评

真实测评使用开发者当前配置的模型，会产生调用费用并受网络和模型波动影响，因此统一标记为 `@pytest.mark.eval`，默认测试不会执行。没有可用模型配置时会安全跳过。

简历定制测评由 LLM Judge 从相关性、真实性和格式三个维度评分。

## 五项核心能力质量评测

每项能力拥有独立的黄金样本和测试文件，共用 `quality_scorers.py` 评分口径、`quality_eval_support.py` 真实模型支持和 `quality_report.py` 报告输出：

| 能力 | 黄金样本 | 测试文件 | 客观指标 | 真实执行边界 |
|---|---|---|---|---|
| Evidence 召回 | `golden/retrieval_cases.py` | `test_retrieval_quality.py` | Hit/MRR/nDCG@3、macro/micro Recall@5、MAP@5、上下文 Token 与无关 Evidence 降幅、切片指标 | 生产 dense/sparse 模型 + 内存 Qdrant + RRF |
| JD 导入 | `golden/jd_import_cases.py` | `test_jd_import_quality.py` | 字段/要求/优先级准确率、quote 接地率、禁止事实 | 生产 `LangChainJDImportModel` + Evidence assessor |
| 经历导入 | `golden/experience_import_cases.py` | `test_experience_import_quality.py` | 字段准确率、事实召回率、Evidence 数量、禁止事实命中 | 生产 `ExperienceTextExtractor` |
| 简历生成 | `golden/resume_generation_cases.py` | `test_resume_generation_quality.py` | JD 覆盖率、Experience/Evidence/技能 P/R/F1、摘要事实与必需章节召回、核心事实召回、逐 provenance 数字接地、禁止事实、Judge、无 fallback | 生产 auto Graph；使用 oracle 候选隔离召回误差，报告明确标记为 generation-only |
| 经历改写 | `golden/experience_rewrite_cases.py` | `test_experience_rewrite_quality.py` | 事实召回率、数字接地率、目标类型、禁止事实、Judge | 生产 Prompt、ContextAssembler、Tool Schema 和流式 Tool Call |

设计上刻意拆开两种误差：召回 eval 单测检索器；生成 eval 固定完整候选集，单测规划、证据判断和文案。否则生成失败时无法判断根因是“没找到”还是“不会写”。

简历生成黄金集包含后端平台、检索评估、零停机迁移、前端设计系统和报表自动化 5 个相互独立的候选人场景，不再共用一个跨职业经历池。每案提供 3 段同职业轨迹经历：2 段应选经历，以及 1 段共享 JD 技术词但证明目标不同的难负例；同时包含至少一项来源不支持的 JD 要求。每案还提供 `reference_resume` 和 `reference_provenance`。参考简历只是项目内固定合成基准，尚未经招聘专家审核，因此只用于稳定回归，不冒充唯一质量真值，也不要求逐字复现。

评测会对照固定 Oracle 检查 Experience、Evidence 和技术技能选择的 Precision/Recall/F1，摘要关键事实召回、必需章节召回、全简历核心事实召回，以及 Summary、每条 Bullet 和每项技能的 provenance 完整性；技能引用还必须指向确实包含该技能的来源经历。真实 LLM 链路同时要求所有阶段均未触发 rule-based fallback。规则模型只承担无网络安全 smoke，不再用放宽门槛冒充质量基线。

生成 eval 的召回器会向 Graph 提供完整候选，以隔离召回误差；所有候选使用相同检索分数和相同任务集合，不再让黄金数据的排列顺序变成隐含排名提示。报告固定写入 `evaluation_scope=generation_only_with_oracle_retrieval` 和 `oracle_retrieval_policy=complete_pool_uniform_score_all_tasks`，不能解释为端到端生成质量。当前生成和 Judge 使用同一运行时模型，报告也会写入 `judge_relation=same_runtime_model`；Judge 只作为次级语义门槛，确定性完整度和真实性门槛不能被 Judge 高分覆盖。

如需独立语义 Judge，至少设置 `RM_EVAL_JUDGE_MODEL`；`RM_EVAL_JUDGE_PROVIDER`、`RM_EVAL_JUDGE_API_BASE` 和 `RM_EVAL_JUDGE_REASONING_EFFORT` 可选。省略 provider 时沿用生成模型的 provider 与密钥，改用另一个模型；切换 provider 时从现有加密密钥库解析对应密钥。只有 provider 或实际模型名不同才标记 `judge_relation=independent_model`；同一模型仅调整 reasoning effort 不算独立。套件报告同时记录独立 Judge 覆盖率。

```powershell
$env:RM_EVAL_JUDGE_MODEL='另一个模型名'
# 可选：$env:RM_EVAL_JUDGE_PROVIDER='另一个 provider'
```

生成报告把结果拆为运行、真实性、选择和成稿四个维度。真实性通过只表示“未发现来源外事实”，不表示内容有用；空输出会同时明确记为成稿失败。每个案例仍先独立落盘；同一次 pytest 模块执行结束时还会写入 `resume-generation-suite` 聚合报告，统一给出套件完整性、总通过率、难负例误选率、空输出率、各维度通过率和 macro 指标。只运行部分 node id 时，聚合报告会明确列出缺失案例并保持 `suite_complete=false`，不能拿局部结果冒充整套结论。

每次模型阶段触发 fallback 时，生产进程会向 `data/logs/backend.log` 写入 ERROR 与堆栈，pytest 会捕获同一条日志；简历生成评测报告还会在 `fallback_errors` 中持久化阶段、主模型、降级模型、异常类型和有限长度的异常信息，避免测试入口未启动应用日志时丢失根因。

套件同时区分 `regression_passed` 与 `quality_claim_ready`。前者只表示模型通过当前固定回归门槛；后者还要求全部参考答案已由领域专家审核，并且全部案例使用独立 Judge。当前样本的 `reference_review.status` 均为 `synthetic_fixture_not_expert_reviewed`，所以无论分数多高，`benchmark_qualified=false`、`quality_claim_scope=synthetic_regression_only`，不能据此声明招聘质量已达标。

每个能力文件同时包含默认执行的确定性评分器测试，以及显式标记为 `eval` 的真实链路测试。默认执行不触网、不调用模型：

```powershell
python -m pytest tests/evals -q
```

真实能力评测必须显式选择 `eval`：

```powershell
python -m pytest tests/evals/test_retrieval_quality.py tests/evals/test_jd_import_quality.py tests/evals/test_experience_import_quality.py tests/evals/test_resume_generation_quality.py tests/evals/test_experience_rewrite_quality.py -m eval -s -q
```

单独运行 5 个简历生成案例时，每案至少经过分析 JD、规划检索、判断 Evidence、起草、事实检查和语义 Judge 6 次模型调用，整套最低 30 次。结构化 JSON 与 Schema 修复重试使应用层理论上限为每案 23 次、整套 115 次；Eval 会关闭 SDK 传输自动重试，避免在这些边界之外产生隐藏调用。报告写入同一 `model_call_contract`，并明确 `actual_usage_instrumented=false`：这些只是调用边界，不是实际 Token 或账单统计。

首次召回评测可能下载配置中的 FastEmbed 模型和 `o200k_base` tokenizer。召回黄金集包含 24 条 Evidence 和 17 个查询，覆盖五类 Search Intent、多正例、中英文混合查询，以及共享父 Experience 上下文的难负例。LLM 相关评测使用当前开发者配置；没有可用模型时安全跳过。所有样本均为合成虚构数据，不访问开发者业务数据库。

RAG 价值指标使用同一批 `EvidenceDocument` 构造公平基线：全量方案向模型暴露全部 Evidence，RAG 方案只暴露混合召回 Top-5；两侧都保留相同的父 Experience 上下文字段，并用 `o200k_base` 对紧凑 JSON 计数。该指标只表示经历事实上下文的 Token 降幅，不冒充完整请求 Token、实际账单或端到端延迟。

当前门槛：

- 召回：Hit@3 = 100%，MRR@3 不低于 0.80，nDCG@3 不低于 0.85；macro Recall@5 不低于 0.95、micro Recall@5 不低于 0.90、MAP@5 不低于 0.85；最差案例 Recall@5 不低于 0.50，多正例切片 macro Recall@5 不低于 0.80；Top-5 平均及最差案例的上下文 Token 降幅均不低于 75%，平均无关 Evidence 降幅不低于 80%；
- JD 导入：字段准确率 100%，要求召回率不低于 90%，优先级准确率不低于 75%，quote 接地率 100%，禁止事实为 0；
- 经历导入：字段准确率和事实召回率均不低于 90%，Evidence 数量正确，禁止事实为 0；
- 生成：JD 覆盖率不低于 75%，全局及逐 provenance 数字接地率均为 100%；Experience、Evidence 和技术技能的 Precision/Recall/F1 均为 100%，摘要事实召回不低于 75%，固定参考答案要求的章节必须全部存在，核心事实召回不低于 85%；所有 Bullet 与技能必须有 provenance，Judge 的 grounding/overall 不低于 4/5，并且不允许任何 fallback；
- 改写：事实召回率和数字接地率均为 100%，禁止事实为 0，Judge 的 grounding、指令完成度和 overall 不低于 4/5。

真实评测会在断言前写入报告：

```text
tests/evals/results/quality/
├── ..._retrieval.json
├── ..._jd-import.json
├── ..._experience-import.json
├── ..._resume-generation.json
├── ..._resume-generation-suite.json
└── ..._experience-rewrite.json
```

报告记录 provider、model、reasoning effort 和评测范围等非敏感元数据，不写 API Key 或 API Base。

简历生成及其套件聚合报告从 `metadata.report_version=2` 起使用中性 Oracle 检索元数据、四维失败分型和流水线诊断；版本 1 报告只能作为历史结果，不能与版本 2 做严格横向对比。

召回文件是质量 Eval，不是生产性能基准。内存 Qdrant 不包含网络、磁盘、并发和服务冷启动成本，因此报告中的 pytest 总耗时不能用作延迟或吞吐 SLO；真实 Qdrant 的冷/热延迟与负载测试应使用独立性能套件。

## 会话记忆压缩测评

记忆测评包含 10 个独立黄金对话案例，覆盖目标切换、偏好更新与撤销、问题关闭、约束累计、失败助手消息和 Tool 数据边界。每个案例执行以下流程：

```text
黄金 Runs
→ MemorySummarizer 逐 Run 压缩
→ 得到最终 Memory
→ Judge LLM 对照原始 Runs 和人工 Oracle 逐条评审
→ 程序计算 token 压缩率并执行质量门槛
```

这里不使用关键词或同义词匹配。人工 Oracle 只描述应保留、已失效和禁止进入 Memory 的语义；Judge 必须逐条返回：

- `preserved`：语义完整保留；
- `partial`：只保留了部分含义；
- `missing`：应保留但没有表达；
- `contradicted`：Memory 表达了相反含义；
- `unsupported_claims`：没有用户依据的声明；
- `stale_claims`：已经更新或解决但仍然存在的声明；
- `forbidden_claims`：领域事实、Tool 数据或助手猜测。

Judge 使用专用结构校验，单次输出上限为 4096 tokens。遇到 JSON 截断或错误返回 Memory 本体时最多重新评审 3 次，不复用通用 enrichment 的重试提示。

MemorySummarizer 遇到 JSON 或 Operations Schema 错误时会重新生成 1 次；例如 `delete` 错误携带 `value` 时，不会直接中断整场测评。若重试后仍失败，报告会记录失败 Run、错误、已完成的 Operations 链和部分 Memory。

当前通过门槛：

- 完整保留率不低于 80%；
- 无依据、过期和禁止内容均为 0；
- Judge 综合评分不低于 4/5；
- 最终 Memory token 不超过原始 Runs 的 35%。

Token 压缩率由程序直接计算，因为它是客观数值，不需要 LLM 判断。

## 多轮会话容量边界测评

`test_conversation_capacity_eval.py` 只测硬容量，不评判压缩后的语义质量。`golden/conversation_capacity_cases.py` 预生成 1000 轮固定、正常的简历协作对话，默认取前 300 轮；内容覆盖目标与事实确认、JD 对齐、逐项审阅和定稿检查，不使用随机串或无意义填充。

每轮都写入隔离测试数据库中的真实 `Conversation`、`Run` 和 `Message`，并走同一条生产链路：

```text
生产 opening Run（计入历史，不计入用户轮数）
→ 当前用户消息
→ ContextAssembler 组装系统提示、领域数据、工具定义与历史
→ 保存固定的正常助手回复并结束 Run
→ 调用生产 MemoryService.compact_run，模拟 Outbox Worker
→ 检查本轮 Snapshot 状态
→ 进入下一轮
```

测试在以下任一位置首次出现容量失败时停止：

- `ContextAssembler` 抛出 `occupied_context_full` 或 `history_context_full`；
- Snapshot 因 `summary_run_too_large`、`memory_full` 或其他 Memory 容量原因变成 `skipped`。

生产 `compact_run()` 会把摘要异常写成 `skipped` Snapshot，而不是直接向调用方抛错。因此测试只把明确的容量原因视为压缩边界；超时、Provider 错误、非法摘要等其他 `skipped` 记为运行错误，不能伪装成容量结论。

上下文容量错误还必须满足一个额外条件：本轮或更早轮次已经实际使用 Snapshot 覆盖了至少一个历史 Run。测试通过包装生产 `MemoryService` 记录其真实历史选择，不修改选择算法。若 `history_context_full` 发生时 `covered_run_count=0`，结果记为 `context_full_before_compression` 运行错误；这通常表示 Token 计数口径或触发条件有问题，不能回答“压缩什么时候也不够用”。如果跑完 N 轮仍从未使用 Snapshot，同样不能给出压缩容量下界。

报告只给出三类结果：

- **上下文容量边界**：第 N 轮在组装请求时失败；最后成功轮次为 N-1；
- **压缩容量边界**：第 N 轮 Snapshot 首次因 Memory 容量原因失败；最后成功压缩轮次为 N-1；
- **容量下界**：N 轮全部完成，只能说明至少支持 N 轮，不能把 N 当成精确上限。

每轮记录实际请求 token、假设完全不压缩时的累计 raw history token、History token、Memory token、Snapshot 状态、`history_selection`、覆盖 Run 数和错误。报告额外记录是否已经发生压缩及首次压缩轮次。没有 4 倍窗口、claim 生命周期或语义 Judge 门槛；压缩质量由独立 eval 负责。

容量报告写入：

```text
tests/evals/results/capacity/
└── ..._realistic-resume-workflow.json
```

只把 `metadata.report_version >= 7` 的报告当作有效容量结论；旧报告没有“压缩已实际生效”的证据，不能用于声明正常对话上限。

真实容量评测会调用当前配置的摘要模型，可能耗时较长并产生多次付费调用；固定助手回复本身不额外调用模型。N 限制总轮数，评测同时关闭摘要模型的 provider 自动重试。即使已经选择 `-m eval`，也必须在 PowerShell 中额外显式开启：

```powershell
$env:RM_MEMORY_CAPACITY_EVAL='1'
# 可选；N 默认 300，可在 1..1000 之间设置
$env:RM_MEMORY_CAPACITY_MAX_ROUNDS='...'
python -m pytest tests/evals/test_conversation_capacity_eval.py -m eval -s -q
```

若不希望覆盖默认轮数，请不要设置 `RM_MEMORY_CAPACITY_MAX_ROUNDS`，或先执行 `Remove-Item Env:RM_MEMORY_CAPACITY_MAX_ROUNDS` 清除当前 PowerShell 会话中的旧值。

## 测评报告

每次真实记忆测评都会在执行质量断言前写入一份 UTF-8 JSON 报告，因此质量不达标时也能保留现场。

报告目录：

```text
tests/evals/results/memory/
└── 20260809T213000.000000Z_preference-revision-and-fact-boundary.json
```

报告包含：

- `model`：首先记录 provider、model 和 reasoning effort；
- `metrics`：集中记录是否通过、事实保留、Judge 分数、问题数量和全部 token 指标；
- `checks`：每项质量门槛是否通过；
- `case`：案例名称、版本和使用的阈值；
- `data`：原始 Runs、Oracle、最终 Memory、Judge 结论及每次尝试；
- `metadata`：最后记录报告格式版本和执行时间。

报告格式版本为 v2。JSON 保留上述字段顺序，不再按照键名字母排序，打开文件时可以先看到模型和指标，再查看具体数据。

API Key 和 API Base 不会写入报告。`results/` 已加入 `.gitignore`，不会被提交到 Git。

## 运行方式

在 PowerShell 中进入后端目录：

```powershell
cd E:\projects\Resume-Matcher\apps\backend
conda activate resume-matcher
```

只运行无网络测试：

```powershell
python -m pytest tests/evals -q
```

这是普通默认测试命令。pytest 配置会排除 `eval` marker，因此会执行简历参考答案自检、评分器回归和容量评测器的契约测试，但不会产出一个所谓“离线容量结果”，也不会调用真实 LLM 或触发默认 300 轮、最多 1000 轮的付费长跑。

运行真实记忆压缩测评：

```powershell
python -m pytest tests/evals/test_memory_compaction_eval.py -m eval -s -q
```

`-s` 会在终端打印报告内容和固化后的绝对路径。

## 黄金样本

- 简历定制样本：`golden/cases.py`
- 记忆压缩样本：`golden/memory_cases.py`
- 多轮容量样本：`golden/conversation_capacity_cases.py`
- Evidence 召回样本：`golden/retrieval_cases.py`
- JD 导入样本：`golden/jd_import_cases.py`
- 经历导入样本：`golden/experience_import_cases.py`
- 简历生成样本：`golden/resume_generation_cases.py`
- 经历改写样本：`golden/experience_rewrite_cases.py`

新增样本时应追加新案例，不要改写已有案例。每个案例都需要明确标注应保留、应清除和禁止出现的信息，否则 Judge 的评分没有稳定依据。
