# 简历生成与经历组合规划 Agent 设计

## 1. 目标与边界

本模块读取一条结构化 JD 和经历库中状态为 `ready` 的经历，完成
`JD 分析 -> 检索规划 -> Evidence 召回 -> 证据判定 -> 经历组合
规划 -> 审查与重规划 -> 简历生成 -> 事实校验`，输出可审计的 `ResumePlan` 和
`ResumeData` 预览。用户确认后才创建新的简历记录。

JD 和经历库是只读事实源。生成过程不得修改 JD、ExperienceItem 或 EvidenceItem；
不得把模型推断写回事实源。没有来源证据的能力只能进入 `uncovered_requirements`，
不能进入经历 bullet 或技能栏。

本阶段实现后端领域模块和 HTTP API。检索、模型和评分权重均提供可替换接口；
首版以闭环正确、可解释和可测试为目标，召回与文案效果后续单独调优。

## 2. 输入、输出与快照

输入：

```text
ResumeGenerationRequest
├─ jd_information_id
├─ mode: auto | llm | deterministic
└─ constraints
   ├─ page_count: 1 | 2
   ├─ max_work_experiences
   ├─ max_project_experiences
   ├─ max_bullets_per_experience
   ├─ top_k_per_task
   ├─ max_search_rounds
   └─ min_coverage_ratio
```

运行开始时复制 `JDAnalysisSnapshot` 和 `ExperienceSnapshot[]`。快照中保留源 ID
和 revision/updated_at，用于解释本次结果基于哪个
版本；运行期间不重新读取并静默混入新的源数据。

输出：

```text
ResumeGenerationPreview
├─ run_id
├─ plan: ResumePlan
├─ resume_data: ResumeData
├─ provenance: 每个生成 bullet/技能对应的 evidence_ids
└─ validation: 覆盖率、未覆盖要求、警告和错误
```

## 3. Evidence 检索文档

一条 EvidenceItem 对应一个检索文档，但文档必须携带父经历上下文：

```text
evidence_id / experience_id
kind / title / organization / role / dates
experience.background
evidence.background / action / result
technologies / tags
```

检索粒度是 Evidence，组合与版面分配粒度是 Experience。Retriever 通过 LangChain
接入 Qdrant：`BAAI/bge-small-zh-v1.5` 生成 dense 向量，`Qdrant/bm25` 生成 sparse
向量，Qdrant Query API 使用 RRF 完成服务端融合。应用不自行实现关键词打分、向量
相似度或两路分数融合，Retriever 协议仍与 Planner 解耦。

召回分数只决定候选入围，不直接决定简历内容。

Qdrant 索引不在生成请求中临时重建。Experience、Evidence CRUD 在业务写事务中写入
Outbox；简历生成模块的独立索引 Worker 读取数据库当前状态，按 Experience 聚合替换
points。非 `ready`、归档、删除或无 Evidence 的经历会被清出索引。Worker 启动时会为
功能上线前已有的经历补发一次同步事件。

## 4. 规划状态与主流程

```text
START
  -> analyze_jd
  -> plan_search
  -> retrieve
  -> judge_evidence
  -> assemble_plan
       |-- 规则发现 must/should 缺口且允许继续检索
       |      -> plan_search
       `-- 否则
              -> draft_resume
              -> verify_resume
              -> END
```

Graph State 保存：

```text
ResumeGenerationState
├─ jd_snapshot / experience_snapshots
├─ coverage_items[]
├─ search_tasks[] / retrieved_candidates[]
├─ judgments[] / coverage_matrix
├─ constraints / search_round
├─ plan / gap_coverage_ids / should_search_more
├─ resume_data / provenance
└─ validation / errors
```

`max_search_rounds` 默认 2。达到上限后必须带着明确缺口继续生成，而不是循环或
伪造经历。

## 5. JD 分析与检索计划

JD requirement 被拆为原子 `CoverageItem`：

- `importance`: must / should / nice，由 required / preferred / normal 映射；
- `statement` 与 `capability`；
- `evidence_expectation[]`：期望看到的场景、行动或结果；
- `aliases[]`：术语、缩写和可迁移表达；
- `source_requirement_ids[]`：回指原始 requirement。

Planner 为覆盖项生成带目标的 `SearchTask`，意图包括：

- `exact_skill`：技术或工具精确匹配；
- `responsibility`：岗位职责的语义匹配；
- `scenario`：业务或问题场景匹配；
- `result_evidence`：结果、指标和影响；
- `transferable`：跨领域可迁移能力。

每个任务必须声明 `coverage_item_ids`、query、过滤条件和 top_k。重规划只针对规则拼装
结果中仍未覆盖的 must/should 项生成新查询，不重复 JD 分析。

## 6. Evidence Judge 与组合规划

Judge 对入围 Evidence 输出：相关度、证据强度、独特性、支持的 CoverageItem、
可提取技能、风险和理由。它不能创建源中不存在的 Evidence ID。

Portfolio Planner 在版面约束内选择经历组合，目标为：

```text
最大化 required/preferred 覆盖 + 证据强度 + 独特性 + 经历内连贯性
最小化 重复覆盖 + 页面成本 + 无依据风险
```

硬约束：

- 工作/实习和项目类经历分别遵守数量上限；
- 每段经历遵守 bullet 上限；
- 每个 selected evidence 必须属于对应 experience；
- 一个 bullet 至少绑定一个 Evidence ID；
- `unsupported_risk` 非空的候选不进入最终计划；
- 只使用快照中的 `ready` 经历。

## 7. ResumePlan 与技能提升

`ResumePlan` 是正式、持久化、可查看的中间产物，包含：

- 被选经历、角色、Evidence ID、覆盖项和 bullet 预算；
- 被提升到技能栏的技能及其 Evidence ID；
- 每项 requirement 的覆盖状态；
- 未覆盖 requirement；
- 被淘汰候选及原因；
- 规划轮次和规则决策警告。

低排名经历只有在提供独特 JD 覆盖、事实证据充分、又不足以占用完整经历版面时，
其已证明的技术/能力才可提升到技能栏。技能文案不得把“接触/参与”升级为“熟练/
精通”，且必须保存 Evidence ID。已被主体经历充分覆盖的重复技能不再提升。

## 8. 规则重规划与停止条件

Evidence Judge 是证据语义判断的唯一模型节点。它输出的 coverage、相关性、证据强度、
独特性和无依据风险由 Portfolio Planner 确定性消费；Planner 根据最终入选证据计算
`uncovered_requirements`，不再调用第二个 Critic 模型重新解释同一批证据。

`assemble_plan` 同时执行确定性搜索策略：只把仍未覆盖的 must/should 项写入
`gap_coverage_ids`，并根据轮次和候选变化决定是否继续检索。`ResumePlan` 为兼容已有
API 继续保留动作字段；当前规则只产生：

```text
search_more | move_to_skill | drop_redundant_content | accept_with_gaps
```

只有同时满足以下条件，规则才产生 `search_more` 并回到 `plan_search`：存在
must/should 缺口、当前轮次小于 `max_search_rounds`，并且当前是第一轮或本轮召回了新的
Evidence ID。否则流程进入简历生成，未覆盖项保持显式，不得通过虚构内容补齐。
`min_coverage_ratio` 只产生可见警告，不单独触发补搜。

## 9. 简历生成与事实校验

所选经历生成 workExperience/personalProjects，证据充分但未占用经历版面的独特能力
写入 technicalSkills。本模块不读取或继承主简历。

生产模式可使用 LLM 对 Evidence 做忠实压缩和措辞；模型失败时 `auto` 模式退回
确定性组装。确定性组装以 `action + result` 形成 bullet，不补数字、规模、职责或
熟练度。

验证器必须检查：

- 所有计划中的 Experience/Evidence 归属有效；
- 每个生成 bullet 和新增技能都有 provenance；
- 生成内容没有使用计划外 Evidence；
- 数量和 bullet 预算没有超限；
- 覆盖率和未覆盖 requirement 与计划一致。

验证错误阻止确认；警告允许用户查看并决定是否继续。

## 10. 持久化与 API

`resume_generation_runs` 保存请求、只读快照、ResumePlan、ResumeData、provenance、
validation、状态和最终 resume_id。状态为 `running | previewed | failed | confirmed`。

```text
POST /api/v1/resume-generations/preview
GET  /api/v1/resume-generations/{run_id}
POST /api/v1/resume-generations/{run_id}/confirm
```

`preview` 完成规划和生成但不创建简历。`confirm` 仅接受通过验证的 `previewed` 运行，
创建 `processed_data=ResumeData` 的非 master 简历；重复确认返回同一个 resume_id，
且不自动创建投递记录。

## 11. 错误与测试

- JD 不存在、没有 requirement、没有 ready 经历：422/404 明确返回；
- LLM 输出结构错误修复一次，`llm` 模式仍失败则运行失败；`auto` 模式回退；
- 单次运行失败持久化错误，不留下伪装成功的 ResumePlan；
- 确认使用状态检查和已生成 resume_id 保证幂等。

测试分层：纯函数覆盖文档构造/组合/技能提升/停止条件；Fake VectorStore 验证 Qdrant
查询边界，Fake Model 覆盖 Graph
重规划与事实约束；API 集成测试覆盖预览、读取、确认幂等、源数据只读、缺失输入和
无 LLM 的确定性闭环。
