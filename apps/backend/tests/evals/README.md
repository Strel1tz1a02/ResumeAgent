# Eval 测评说明

普通测试回答“代码是否按预期执行”，Eval 回答“模型输出的质量是否真的达标”。本目录覆盖旧版简历定制、会话记忆压缩，以及召回、JD 导入、经历导入、简历生成、经历改写五条核心能力链路。

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
| Evidence 召回 | `golden/retrieval_cases.py` | `test_retrieval_quality.py` | Hit/MRR/nDCG@3、macro/micro Recall@5、MAP@5、切片指标 | 生产 dense/sparse 模型 + 内存 Qdrant + RRF |
| JD 导入 | `golden/jd_import_cases.py` | `test_jd_import_quality.py` | 字段/要求/优先级准确率、quote 接地率、禁止事实 | 生产 `LangChainJDImportModel` + Evidence assessor |
| 经历导入 | `golden/experience_import_cases.py` | `test_experience_import_quality.py` | 字段准确率、事实召回率、Evidence 数量、禁止事实命中 | 生产 `ExperienceTextExtractor` |
| 简历生成 | `golden/resume_generation_cases.py` | `test_resume_generation_quality.py` | 独立 JD 覆盖率、数字接地率、bullet 数、禁止事实、Judge | 生产 auto Graph；使用 oracle 候选隔离召回误差 |
| 经历改写 | `golden/experience_rewrite_cases.py` | `test_experience_rewrite_quality.py` | 事实召回率、数字接地率、目标类型、禁止事实、Judge | 生产 Prompt、ContextAssembler、Tool Schema 和流式 Tool Call |

设计上刻意拆开两种误差：召回 eval 单测检索器；生成 eval 固定完整候选集，单测规划、证据判断和文案。否则生成失败时无法判断根因是“没找到”还是“不会写”。

每个能力文件同时包含默认执行的确定性评分器测试，以及显式标记为 `eval` 的真实链路测试。默认执行不触网、不调用模型：

```powershell
python -m pytest tests/evals -q
```

真实能力评测必须显式选择 `eval`：

```powershell
python -m pytest tests/evals/test_retrieval_quality.py tests/evals/test_jd_import_quality.py tests/evals/test_experience_import_quality.py tests/evals/test_resume_generation_quality.py tests/evals/test_experience_rewrite_quality.py -m eval -s -q
```

首次召回评测可能下载配置中的 FastEmbed 模型。召回黄金集包含 24 条 Evidence 和 17 个查询，覆盖五类 Search Intent、多正例、中英文混合查询，以及共享父 Experience 上下文的难负例。LLM 相关评测使用当前开发者配置；没有可用模型时安全跳过。所有样本均为人工虚构数据，不访问开发者业务数据库。

当前门槛：

- 召回：Hit@3 = 100%，MRR@3 不低于 0.80，nDCG@3 不低于 0.85；macro Recall@5 不低于 0.95、micro Recall@5 不低于 0.90、MAP@5 不低于 0.85；最差案例 Recall@5 不低于 0.50，多正例切片 macro Recall@5 不低于 0.80；
- JD 导入：字段准确率 100%，要求召回率不低于 90%，优先级准确率不低于 75%，quote 接地率 100%，禁止事实为 0；
- 经历导入：字段准确率和事实召回率均不低于 90%，Evidence 数量正确，禁止事实为 0；
- 生成：独立 JD 覆盖率不低于 75%，数字接地率 100%，至少 2 条 bullet，Judge 的 grounding/overall 不低于 4/5；
- 改写：事实召回率和数字接地率均为 100%，禁止事实为 0，Judge 的 grounding、指令完成度和 overall 不低于 4/5。

真实评测会在断言前写入报告：

```text
tests/evals/results/quality/
├── ..._retrieval.json
├── ..._jd-import.json
├── ..._experience-import.json
├── ..._resume-generation.json
└── ..._experience-rewrite.json
```

报告只记录 provider、model 和 reasoning effort，不写 API Key 或 API Base。

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

运行真实记忆压缩测评：

```powershell
python -m pytest tests/evals/test_memory_compaction_eval.py -m eval -s -q
```

`-s` 会在终端打印报告内容和固化后的绝对路径。

## 黄金样本

- 简历定制样本：`golden/cases.py`
- 记忆压缩样本：`golden/memory_cases.py`
- Evidence 召回样本：`golden/retrieval_cases.py`
- JD 导入样本：`golden/jd_import_cases.py`
- 经历导入样本：`golden/experience_import_cases.py`
- 简历生成样本：`golden/resume_generation_cases.py`
- 经历改写样本：`golden/experience_rewrite_cases.py`

新增样本时应追加新案例，不要改写已有案例。每个案例都需要明确标注应保留、应清除和禁止出现的信息，否则 Judge 的评分没有稳定依据。
