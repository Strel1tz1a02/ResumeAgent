# AI Chat 有界会话记忆与上下文设计

**状态：** Implemented

**日期：** 2026-08-08

**范围：** `apps/backend/app/ai_chat` 与 Experience Adapter

**目标：** 长会话不再全量回放，模型输入始终受 Token 硬上限约束。

---

## 1. 核心结论

这个项目的对话是绑定具体 Experience 字段的编辑会话，不需要复杂的跨会话长期记忆。

模型可见内容只保留三层：

1. **Transcript**：数据库中的完整原始对话，用于展示和审计；
2. **Short-term Memory**：最近若干个完整对话轮次，按 Token 预算选择；
3. **Conversation Memory**：超出短期窗口的旧内容，经有损摘要后保存。

为避免请求加载时总要等待摘要，系统额外维护内部 **Staged Memory Snapshot**：提前压缩短期窗口内最老的两个完整 Run，但在这些 Run 真正移出窗口前，Snapshot 不进入主模型上下文。

模型每轮看到：

```text
System / Tools
+ 当前的业务数据
+ Conversation Memory
+ 最近完整 RunBundle
+ Pending Tool Call/Result
+ 当前用户输入
```

Conversation Memory 只维持当前 Conversation 的连续性。新 Conversation 从最新业务数据重新开始，不自动继承旧 Memory。

---

## 2. 当前问题

当前系统每轮读取全部 completed 消息，并把完整 `model_messages` 放入 Experience Graph State。

```text
全部 completed 消息
+ Experience 业务数据
+ pending Tool Result
→ model_messages
→ LLM 与 LangGraph checkpoint
```

主要问题：

- 对话越长，模型输入越大；
- `max_tokens` 只限制输出，不能限制输入；
- Tool Result、业务数据和历史消息可能重复表达相同内容；
- 当前 Tool Result 与业务上下文都可能携带完整 `ExperienceDetail`，空历史也可能超限；
- checkpoint 会重复保存包含历史的 Graph State。

本设计首先解决模型输入无界增长。checkpoint 的进一步瘦身可以单独实施，但不能再把 checkpoint 当作会话记忆。

---

## 3. 数据边界

| 数据 | 职责 | 是否完整进入模型 |
|---|---|---:|
| Transcript | 保存原始对话 | 否 |
| Domain Truth | Experience、Evidence 等当前业务事实 | 按现有 Adapter 逻辑完整进入 |
| Conversation Memory | 保存旧对话的有损摘要 | 是，受独立预算限制 |
| Staged Memory Snapshot | 保存未来可晋升的候选 Memory | 否 |
| Recent RunBundle | 保存近期完整交互 | 是，受短期预算限制 |
| Checkpoint | 恢复未完成的 Graph 执行 | 否 |

边界规则：

- Experience 与 Evidence 表始终是业务事实源；
- Memory 不能覆盖业务事实，也不能绕过 Tool 审批；
- Transcript 不因摘要而修改或删除；
- Memory 内容是派生上下文，不作为 System 指令注入；
- Tool Call、审批状态和执行位置属于 Run/Checkpoint，不属于 Memory；
- 压缩器只读取 completed RunBundle，不感知 Pending Tool、审批状态或 Tool delivery；
- Staged Snapshot 只有晋升为 Active Conversation Memory 后才允许进入主模型上下文。

---

## 4. Token 预算

### 4.1 最终请求必须受硬上限约束

Token 边界针对最终模型请求，而不是只计算历史消息：

```text
System Prompt
+ Tool Schema
+ 当前业务数据
+ Conversation Memory
+ Recent RunBundles
+ Pending Tool Call/Result
+ 当前用户输入
+ 输出预留
+ 安全余量
<= 模型上下文上限
```

定义：

```text
effective_output_cap = min(configured_output_reserve, model_max_output_tokens)

input_cap_candidates = [model_max_input_tokens]
if configured_total_context_window exists:
    input_cap_candidates += [configured_total_context_window - effective_output_cap]

effective_input_cap = min(input_cap_candidates) - safety_margin

recent_run_budget = effective_input_cap
    - system_tokens
    - tool_schema_tokens
    - domain_tokens
    - current_user_tokens
    - pending_tool_tokens
    - memory_token_cap
```

Conversation Memory 预留固定最大预算。短期窗口只使用剩余预算，因此摘要更新不会突然挤掉已经选中的近期轮次。

所有 Token 数使用最终模型、Messages 和 Tools 的真实格式估算。LiteLLM 的 `max_input_tokens` 与 `max_output_tokens` 分开使用，旧 `max_tokens` 只作为输出上限 fallback，不能误当上下文窗口。预检生成一次不可变、可序列化的 `ModelRequestSpec`，其中固定实际模型、非敏感配置 fingerprint、有效 Tools、`max_tokens` 与 reasoning 参数；最终调用校验 fingerprint 后直接消费它，不能在 Graph 内二次构造。API Key、Router 和 LLMConfig 不进入 checkpoint。有效输出上限必须等于真实请求的 `max_tokens`。

### 4.2 必须保留的内容

以下内容不能因为历史过长而静默删除：

- System Prompt 与 Tool Schema；
- 当前用户输入；
- Adapter 当前传入的完整业务数据；
- resolved 且等待模型投递的 Tool Call/Result 原子对。

Awaiting-approval Tool Call 此时还没有 Result，只属于 Run/Checkpoint；它不会伪装成 Tool Result 进入新的模型请求。

如果这些必要内容自身已经超限，直接返回 `context_full`，不能把请求交给模型碰运气。

### 4.3 本期冻结业务输入

本期只解决对话历史无界增长，不修改主模型收到的业务数据：

- `SAVED_EXPERIENCE_DATA` 保持现有内容和序列化方式；
- Pending Tool Result 保持现有 arguments、result 和原子消息结构；
- Tool Schema 保持现有内容；
- 上述固定内容仍纳入最终 Token 计数。

因此，历史窗口有界不保证任意单个业务 Payload 都能进入模型。如果完整业务数据或原始 Pending Tool Result 自身已经占满预算，直接返回 `context_full`，本期不通过静默投影规避。

---

## 5. Short-term Memory

### 5.1 裁剪单位

短期记忆按 Token 计算边界，但裁剪单位是完整 `RunBundle`，不是单条 Message。

RunBundle 由相同 `run_id` 下的稳定记录组成：

```text
文本 Run：completed Run + User + completed Assistant
工具 Run：completed Run + User + optional Assistant + resolved Tool 决策/紧凑结果
Opening Run：completed Run + completed Assistant
```

判定规则：

- 当前 `run_id` 不参与历史候选，当前 User 始终单独加入；
- 文本 Run 必须有 completed Assistant；Tool Run 的 Assistant 可以为空或 cancelled；
- 压缩器只查询 `status=completed` 的 Run，failed、cancelled、running、suspended Run 不会成为压缩候选；
- 压缩候选不检查 Pending Tool、审批状态或 Tool delivery；审批未完成时 Run 本身不会 completed，也不会进入查询结果。

规则：

- 从最新 RunBundle 向前选择，直到达到 `recent_run_budget`；
- 不能截断半个 RunBundle；
- failed、cancelled 或仅有流式残片的 Assistant 不进入历史窗口；
- 非工具的文本 Run 没有 completed Assistant 时不构成稳定 RunBundle，也不参与摘要；
- 当前用户输入不属于可裁剪历史，始终单独保留；
- 裁剪只影响模型视图，不影响数据库中的 Transcript。

Pending Tool Result 不属于普通历史，也不属于压缩流程。Context Assembler 仍按现有协议把它作为独立的 `Assistant Tool Call + Tool Result` 原子块加入最终请求。

### 5.2 Active 水位与预压缩重叠

Active Conversation Memory 指向一个 Snapshot。该 Snapshot 的 `covered_through_sequence` 表示此前完整 Run 已经进入主模型可见的 Memory。

每次请求先按 Token 得到候选边界，再与 Active 水位取较新者，形成单调的 `short_term_boundary_sequence`：

```text
short_term_boundary_sequence = max(token_boundary, active_covered_through_sequence)
```

即使模型预算后来变宽，已经摘要的 Run 也不会重新退回原文窗口。

```text
sequence <= short_term_boundary_sequence   → 应由 Active Memory 覆盖
sequence >  short_term_boundary_sequence   → 仍以完整 RunBundle 进入短期窗口
```

系统固定配置：

```text
overlap_budget = 2  # 完整 RunBundle 数量，不是 Token
```

预压缩目标是短期窗口内最老的两个完整 RunBundle。它们形成 Staged Memory Snapshot，但仍以原始 RunBundle 进入主模型上下文；对应摘要不会进入主模型。可用 Run 不足两个时只预压缩已有 Run，不等待未来 Run。

因此最终请求不存在摘要与原文重叠：

```text
Active Memory：刚好覆盖短期窗口外的 Run
Recent RunBundles：完整覆盖短期窗口内的 Run
Staged Snapshot：只入库，不进入请求
```

---

## 6. Conversation Memory 数据模型

### 6.1 Active Pointer

每个 Conversation 只维护一个主模型可见入口：

```text
ai_chat_conversation_memories
├── id
├── conversation_id          UNIQUE, FK
├── active_snapshot_id       FK
├── created_at
└── updated_at
```

主模型只能读取 `active_snapshot_id` 指向的 Snapshot，禁止按创建时间读取“最新 Snapshot”。Active Pointer 只能沿规范链前进，不能回退。

Conversation 首次需要 Memory 时创建空 Root Snapshot，并让 Active Pointer 指向它。Conversation 删除时级联删除 Pointer 与 Snapshot；任何 Memory 操作都不得修改 Transcript。

### 6.2 Staged Memory Snapshot

Active 与 Staged 共用 Snapshot 表。Active 是 Pointer 指向的节点；Staged 是它后方尚未晋升的规范链节点：

```text
ai_chat_conversation_memory_snapshots
├── id
├── conversation_id              FK
├── parent_snapshot_id           NULLABLE, FK
├── source_run_id                NULLABLE, FK
├── source_bundle_hash           NULLABLE
├── covered_through_sequence     INTEGER
├── operations                   JSON
├── core                         JSON
├── other                        JSON
├── memory_token_count           INTEGER
└── created_at

UNIQUE(conversation_id, parent_snapshot_id)
UNIQUE(conversation_id, source_run_id)
```

Root Snapshot 的 Parent 与 Source Run 为空，水位为初始值。Root 之后每个 Snapshot 必须：

1. 只消费 Parent 后紧邻的一个 completed RunBundle；
2. 保存该 RunBundle 的稳定 Hash；
3. 保存累计的完整 `core + other`，而非孤立增量；
4. 让水位恰好推进到 Source Run，不能跳 Run。

摘要 LLM 返回 Operations 后，服务先完成 Schema、路径、冲突、Source Bundle Hash 和 Token 校验，再把 Operations 应用到 Parent，形成新的完整 Snapshot。若 completed RunBundle 在摘要期间发生异常变化，结果作废并重新读取。

不能只保存孤立 Operations。后续 Snapshot 必须看到 Parent 的完整候选 Memory，否则 Operations 的字段基准可能已经过期。

当某个 Run 移出短期窗口时，晋升不再调用 LLM：先重新计算本次将晋升节点的 Source Bundle Hash，再以 `expected_active_snapshot_id -> target_snapshot_id` 的 CAS 前移 Active Pointer。一次移出多个 Run 时，直接指向其中最后一个连续 Snapshot。Hash 不一致时废弃该节点及其后代，并从变化处重新生成，不能晋升陈旧摘要。

前后台共用同一个 `ensure_chain()`。摘要 LLM 调用不持数据库事务；同一 Conversation 使用可过期的压缩租约，数据库再用唯一 Edge 与 CAS 拒绝分叉。并发冲突方直接采用胜出的 Snapshot，不重复覆盖。

后台只创建 Staged Snapshot，不能移动 Active Pointer。晋升只能由持有当前 Run reservation 的前台请求执行。晋升前必须验证 Target 是当前 Active 的连续后代，并恰好覆盖本轮窗口外边界。

Snapshot 的 Source、Hash、水位、Operations、Core 与 Other 写入后不可修改。晋升事务完成 Pointer CAS 后，允许把新 Active 的 `parent_snapshot_id` 原子置空，再删除旧祖先；这是唯一允许的拓扑 Rebase，不修改 Memory Payload。晋升后保留 Active 与其后最多两个可用 Staged Snapshot。`memory_token_count` 只是缓存，模型或 Tokenizer 配置变化后必须重新计算。

### 6.3 Core

`core` 使用固定、扁平的结构：

```json
{
  "current_goal": null,
  "constraints": [],
  "preferences": [],
  "confirmed_decisions": [],
  "open_questions": []
}
```

字段含义：

- `current_goal`：当前对话正在解决的问题，只保留一个；
- `constraints`：必须满足或禁止违反的要求；
- `preferences`：用户明确表达的措辞、风格和侧重点；
- `confirmed_decisions`：双方已经确认、后续仍需遵循的选择；
- `open_questions`：尚未解决的问题。

被否定的方案也是已确认决策，使用明确的否定句保存，例如“不要强调团队管理能力”。

约束：

- `current_goal` 只能是字符串或 `null`；
- 其他字段只能是扁平字符串数组；
- 每一项只表达一个意思；
- 相同内容不能重复出现在多个字段；
- 只能记录用户明确表达或对话已经确认的内容；
- 已保存的 Experience/Resume 事实、scope/revision、Tool arguments/result 与助手猜测不复制进 Core 或 Other；
- `confirmed_decisions` 只保存已经确认的会话控制决策。

### 6.4 Other

`other` 保存重要但无法归入 Core 的低频内容。

```json
{
  "preferred_wording": "希望保留原句中的口语感",
  "example_to_follow": ["示例 A", "示例 B"]
}
```

约束：

- `other` 是单层 JSON Object；
- Key 由摘要 LLM 生成，使用稳定、简短的 `snake_case` 名称；
- Value 只能是字符串或字符串数组；
- 禁止嵌套对象；
- 必须优先复用语义相同的已有 Key；
- 限制 Key 数量、单字段 Token 和 `other` 总 Token；
- `other` 不能变成自由文本版完整 Transcript。

---

## 7. 摘要生成机制

### 7.1 触发条件

采用“后台预压缩 + 请求内追赶”：

```text
正常路径：Run completed → 后台补齐短期窗口内最老的 2 条 Stage
追赶路径：新请求发现晋升目标缺失 → 暂停主模型 → 连续补齐 Snapshot → 自动继续
```

后台任务不移动 Active Pointer，只创建 Staged Snapshot。一个 Conversation 同时最多运行一个压缩任务。

请求开始时计算三个位置：

1. `short_term_boundary_sequence`：短期窗口外的最后一个 Run；
2. `promotion_target`：需要晋升进 Active Memory 的最后一个 Run；
3. `precompression_target`：从 `promotion_target` 向后数最多 `overlap_budget=2` 个已有完整 Run 后的位置。

正常请求只要求 Snapshot Chain 覆盖 `promotion_target`。只要晋升目标已经预压缩，即使剩余 Stage 少于两个，本轮也直接晋升并继续；后台在 Run 完成后恢复余量。

只有 Snapshot Chain 连 `promotion_target` 都未覆盖，才说明后台已落后到本轮必须使用尚未摘要的旧 Run。此时当前 SSE 保持打开，前端显示“正在整理对话记忆…”。服务逐 Run 调用摘要模型，一次追到 `precompression_target`，即同时补齐晋升目标与最多两个已有 Run 的预压缩余量；它不会等待尚未产生的 Run。

追赶路径先确保整段 Chain 到达 `precompression_target`，再晋升 `promotion_target`。后续 Stage 生成失败时，已校验 Snapshot 保留供重试使用，但 Active Pointer 不动，本轮也不调用主模型。

每个摘要调用只生成一个 RunBundle 的下一条 Stage。摘要 LLM 的输入：

```text
完整的 Parent Memory Snapshot（Active 或前一 Stage）
+ 一个完整 RunBundle
+ Memory Schema 与操作规则
```

如果单个 RunBundle 已经超过摘要模型预算，则不能拆分它，直接返回 `context_full`。

摘要调用禁用业务 Tools，使用受 Schema 约束的 JSON 输出。其输入上限为 `min(summary_input_cap, resolved_summary_input_budget)`，后者复用主模型的输入/输出分离解析规则；真实摘要请求的 `max_tokens` 等于解析后的有效 summary output reserve。

### 7.2 Operations

摘要 LLM 不返回整份新 Memory，只返回简单 Operations：

```json
{
  "operations": [
    {
      "op": "update",
      "path": "core.current_goal",
      "value": "优化当前项目经历描述"
    },
    {
      "op": "add",
      "path": "other.preferred_wording",
      "value": "保留自然口语感"
    }
  ]
}
```

操作规则：

- `add`：只能创建不存在的 `other.<key>`；
- `update`：可以替换固定 Core 字段或已存在的 Other 字段；
- `delete core.current_goal`：把值清为 `null`；
- `delete` 其他 Core 字段：把值清为 `[]`；
- `delete other.<key>`：同时删除字段和值；
- 未定义的 Core 路径直接拒绝；
- 同一批 Operations 不能对同一路径产生冲突操作；
- 整批 Operations 先校验，再应用到基准 Snapshot；
- 校验或应用失败时，不创建当前 Snapshot，Active Pointer 和既有 Snapshot Chain 都保持不变。

Core 字段初始存在但内容为空，因此首次写入 Core 使用 `update`。
`update` 替换整个字段内容；需要增删数组元素时，由 LLM 返回更新后的完整数组。

合法的空 Operations 表示该 Run 没有必要改变 Memory，仍然创建一条内容不变的 Candidate Snapshot，使规范链可以继续前进。

路径、类型、字段存在性、冲突与 Token 上限由代码强校验。“复用语义相同 Key”“只记录明确内容”等语义规则属于摘要 Prompt 和质量测试，不假装由普通 Schema 完全保证。

### 7.3 有损摘要策略

摘要允许有损。Transcript 负责完整性，Memory 只负责维持后续任务连续性。

摘要 LLM 应遵循：

1. 优先更新 Core；
2. 重要但不属于 Core 的内容才进入 Other；
3. 不影响后续任务的内容不生成 Operation，只留 Transcript；
4. 已被新信息替代、已经解决、明确失效或重复的内容可以删除；
5. 暂不预测用户下一轮会问什么，也不能因为“可能用不到”而删除内容；
6. 不推断长期人格、隐含偏好或未经确认的业务事实。

### 7.4 Memory 预算

Memory 有独立 `memory_token_cap`。

生成 Operations 后必须重新计算完整 Candidate `core + other` 的 Token：

- 未超过上限：保存 Candidate Snapshot，但不改变 Active Pointer；
- 超过上限：拒绝该 Snapshot，Active Pointer 和 Snapshot Chain 都不推进；
- Memory 无法继续容纳必要摘要时：返回 `context_full`。

---

## 8. 每轮上下文组装流程

```text
1. 创建不含 Message 的 Run reservation
2. 读取 Active Pointer、Snapshot Chain 与 completed RunBundles
3. 加载当前业务数据并计算最终请求 Token 预算
4. 从新到旧按 Token 选择完整 Recent RunBundles
5. 计算 promotion_target 与 2 个 Run 的 precompression_target
6. promotion_target 缺失时发送 memory.compaction.* 事件并同步追赶到 precompression_target
7. CAS 前移 Active Pointer 到 promotion_target 对应 Snapshot
8. 重验业务 revision，必要时重载业务数据并重新计算边界
9. 组装 Domain Truth + Active Memory + Recent RunBundles
10. 追加 Pending Tool Call/Result 原子块
11. 将 Current User 放在最后
12. 对最终 Messages + Tools 重新计数
13. 未超限则发送 context.usage 并调用主模型
```

第 8 步若发现业务 Revision 或 Token 边界变化，则回到第 5 步重新计算，直到业务版本与边界稳定；已经前移的 Active Pointer 不回退。

组装器必须保证：

- Active Memory 刚好结束在短期窗口边界，Recent RunBundles 从下一条 Run 开始；
- Staged Snapshot 绝不进入主模型请求；
- 同一 Run 不会同时通过 Active Memory 和原始 RunBundle 进入主模型；
- 必需内容优先于 Memory 与普通历史；
- 追赶压缩失败时不调用主模型，也绝不回退到全量历史。

主模型完成并持久化 Run 后，后台 Memory Maintainer 使用本轮实际短期窗口边界补齐下一轮需要的两条 Stage；此后台任务不延迟 `assistant.completed`。

---

## 9. `context_full` 行为

固定必要内容可以在压缩追赶前预检：它们自身超限时直接返回。晋升目标缺失不是 `context_full`，而是保持当前请求等待，直到补齐晋升目标和 `overlap_budget=2` 的预压缩余量。晋升目标已存在时不为补足余量阻塞本轮请求。

可能原因：

- Memory 已达到上限，无法继续吸收必要内容；
- 单个完整 RunBundle 已经超过摘要模型预算，无法形成下一条 Stage；
- 当前用户输入本身过长；
- 当前 scope 的必要业务数据过大；
- System、Tools 和必要状态已经占满可用预算。

提示必须根据原因给出下一步：

- Active Memory 已满或单 Run 无法压缩：提示新建对话，已保存的经历数据不会丢失；
- 当前输入过长：提示缩短本次输入；
- 必要业务数据过大：提示缩小编辑范围；
- System、Tools 或固定状态过大：返回配置错误，不能误导用户新建对话。

新 Conversation 创建新的空 Memory，只读取最新 Domain Truth，不自动携带旧 Conversation Memory。

`overlap_budget` 是固定的完整 RunBundle 数量，本期不根据剩余 Token 动态扩大或缩小。

---

## 10. 失败处理

- 后台预压缩失败：保留 Active Pointer 与既有 Snapshot Chain，记录失败；不影响刚完成的主模型响应；
- 请求内追赶时摘要调用失败：停止追赶，本轮返回可重试的 `memory_compaction_failed`；
- Operations JSON、路径或类型非法：不创建当前 Snapshot，Active Pointer 与既有 Snapshot Chain 保持不变；
- Candidate Memory 超限：拒绝当前 Snapshot；如果本轮必须依赖它完成晋升，则返回 `context_full`；
- Snapshot Edge 或 Active Pointer CAS 冲突：重新读取规范链并继续，不能覆盖胜出的连续 Snapshot；
- Token 估算失败：禁止调用主模型，返回稳定错误；
- 主模型仍返回供应商上下文溢出：记录指标并映射为 `context_full`；
- 任何摘要失败都不能修改 Transcript、Experience 或 Tool 状态。

摘要失败后，下一次后台任务或用户重试仍从规范链末端处理同一个完整 RunBundle。成功摘要的主动取舍可以有损，异常不能跳过 Run 或制造断链。压缩租约允许过期接管，避免进程崩溃后永久占用。

为保证同一个 `client_message_id` 可以真正重试，目标流程调整为：

```text
1. 先创建不含 Message 的 Run reservation，阻止并发 Run
2. 使用原始 user_content 完成 Token 预检、Stage 追赶、晋升和上下文准备
3. 预检失败：run.failed，不创建 User/Assistant Message，不占用 client_message_id
4. 预检成功：再原子创建 completed User 与 generating Assistant
```

因此相同请求可以在预检失败后重试。Run reservation 后的普通异常和客户端取消也必须分别收敛为 failed/cancelled，不能遗留 running Run。User/Assistant 创建后的执行失败，仍按现有规则收敛为 `run.failed + assistant.failed`，Conversation 必须解除 busy 状态。

追赶完成后必须用 Adapter 的 scope/collection revision 重新验证业务上下文；若等待期间业务数据变化，重载原样业务块并重新选择历史、计算 Token。持续变化时返回可重试错误，不把旧业务快照交给主模型。

`context.usage` 与 `assistant.started` 两次 yield 都在 admission 的 `try/finally` 内；调用方在任一事件后关闭生成器也要收敛 Run/Assistant。应用启动时回收“running 且没有 Message”的 stale preflight reservation，处理进程崩溃留下的 busy 状态。

Pending Tool Result 的生成、注入与消费协议保持现状。Memory 模块只接收已完成 RunBundle，不读取或修改 Pending、审批与 Tool delivery 状态；其 exactly-once/at-least-once 语义不在本期改造范围。

---

## 11. 组件职责

| 组件 | 职责 |
|---|---|
| Token Counter | 估算最终 Messages、Tools 和 Memory Token |
| Run Bundle Builder | 只查询 completed Run，并按 `run_id` 组装完整 RunBundle |
| Context Assembler | 计算预算、选择近期轮次、晋升 Active Snapshot、执行最终硬校验 |
| Memory Pointer Repository | 读取并 CAS 前移主模型唯一可见的 Active Pointer |
| Snapshot Repository | 保存、连接和清理不可变 Candidate Snapshot |
| Memory Summarizer | 基于一个完整 RunBundle 生成受 Schema 约束的 Operations |
| Memory Maintainer | 后台维持 2 条 Stage 余量，请求内追赶缺口 |
| Memory Service | 校验 Operations、物化 Candidate Snapshot、控制 Memory Token 上限 |
| Experience Adapter | 原样提供当前 Prompt、业务数据、Tool Result 与 Graph revision state |

共享 AI Chat 层负责预算与记忆机制。Experience Adapter 保持现有业务输入，不自行决定历史窗口。

---

## 12. 可观测性

每次主模型调用至少记录：

- 最终输入估算 Token；
- System、Tools、Domain、Memory、Recent RunBundles、Pending Tools、Current User 各自 Token；
- 选中的 RunBundle 数量；
- Memory Token 与 Other Key 数量；
- Active 水位、Snapshot Chain 尾部与当前短期边界；
- Stage 余量（目标 2、实际数量）；
- 后台预压缩和请求内追赶各自的 Run 数量与耗时；
- Active 晋升的 Run 数量；
- 摘要失败原因；
- `context_full` 原因。

日志只记录计数、ID、耗时和错误码，不记录完整对话、Memory 或业务正文。

请求内需要追赶时发送：

```text
memory.compaction.started
memory.compaction.progress   # completed_runs / target_runs
memory.compaction.completed
```

前端在原有会话加载区域显示“正在整理对话记忆…”，不新增大面积面板。追赶完成后同一 SSE 自动继续到 `context.usage` 与主模型响应。

每次完成最终 Token 计算后，通过 SSE 发送；成功时事件位于 `assistant.started` 前，`context_full` 时位于 `run.failed` 前：

```json
{
  "event": "context.usage",
  "data": {
    "used_tokens": 8120,
    "budget_tokens": 13072,
    "percent": 62
  }
}
```

`budget_tokens` 是按 `max_input_tokens`、可选总窗口 fallback、有效输出上限与 safety margin 解析出的最终输入预算，`used_tokens` 是最终 `Messages + Tools` 输入 Token。前端只在会话标题区显示单行小字，例如“上下文 62%”；不增加进度条或大卡片。`context_full` 使用本次超限计数；`memory_compaction_failed` 因没有最终计数而把旧占用率清为 unknown，不能继续显示上一轮值。

---

## 13. 测试与验收

### 13.1 短期窗口

- 不同长度消息下均按 Token 而不是固定轮数裁剪；
- RunBundle 不会被拆开；
- Tool Call/Result 不会被拆开；
- 主模型收到的业务数据、Pending Tool Result 和 Tool Schema 与改造前一致；
- 当前 Run 不会作为历史再次加入；
- failed、cancelled Run 和流式残片不会进入历史窗口；
- opening Run 的 Assistant-only Bundle 可以正常保留；
- 压缩查询只返回 completed RunBundle，不包含任何 Pending/审批分支；
- 当前用户输入始终保留；
- Transcript 不因裁剪而变化。

### 13.2 Memory

- `overlap_budget` 固定等于 2 个完整 RunBundle，不按 Token 计算；
- 短期窗口内最老的两条 Run 可以预压缩，但 Staged Snapshot 不进入主模型；
- 可用 Run 少于两条时只生成现有 Snapshot，不等待未来 Run；
- 主模型只读取 Active Pointer 指向的 Snapshot，不能误读最新 Staged Snapshot；
- 每条非 Root Snapshot 只对应一个完整 Run，并基于唯一 Parent；
- Source Bundle Hash 变化时拒绝过期摘要结果；
- 并发前后台压缩只能形成一条规范链，不能产生可晋升分叉；
- Active Pointer 与水位只能前进；预算变宽也不把已摘要 Run 放回原文；
- 晋升时重验本次覆盖的 Bundle Hash，并在同一事务完成 Pointer CAS、Active Rebase 与旧祖先清理；
- `add` 只能创建 Other 字段；
- `update` 不能创建未知 Core 字段；
- 删除 Core 只清空内容，删除 Other 会移除 Key；
- 合法空 Operations 仍会形成内容不变的连续 Snapshot；
- 非法 Operations 不创建 Snapshot，也不产生部分更新；
- Run 移出窗口时只晋升已校验 Candidate，不再次调用摘要模型；
- 一次移出多个 Run 时晋升最后一个连续 Stage；
- 后台只补 Staged Snapshot，不能晋升 Active；
- 后台失败不修改 Active；只有晋升目标缺失时请求才等待，并追到最多 2 条已有 Run 余量；
- 晋升目标已存在但 Stage 余量不足时，本轮不等待，由后台补齐；
- 追赶后半段失败时保留已完成 Snapshot，但 Active 不动；
- Summary 失败时 Active 水位和 Snapshot Chain 都不跳跃；
- Memory 永远不超过独立 Token 上限；
- 不重要内容可以只保留在 Transcript。

### 13.3 最终上下文

- 本地计数判定超限的 `Messages + Tools` 请求绝不发送；供应商仍判定溢出时稳定映射为 `context_full`；
- 100 轮以上会话的输入 Token 不再随总轮数线性增长；
- Domain Truth 被明确标记为当前权威数据，Memory 没有写业务事实的权限；
- Active Memory 只覆盖短期窗口外 Run，主模型看不到任何 Stage；
- 请求内追赶失败不会调用主模型，也不会回退到全量历史；
- 追赶时前端显示“正在整理对话记忆…”，完成后同一请求自动继续；
- 上下文预检失败只终结 Run reservation，不创建失败的 User/Assistant Message；
- 相同 `client_message_id` 可以在预检失败后重新执行；
- User/Assistant 创建后的失败会正常收敛，不锁死 Conversation；
- 无法形成合法请求时稳定返回 `context_full`；
- 新 Conversation 不自动继承旧 Memory。
- 前端占用率与后端最终 Token 计数一致，新 Conversation 会清空旧值。

---

## 14. 实施边界

首期实现：

1. Token Counter 与最终请求硬预算；
2. RunBundle 构建和近期窗口选择；
3. Active Pointer 与 Payload 不可变的 Conversation Memory Snapshot 表；
4. Core、Other 与 Operations 校验；
5. `overlap_budget=2` 的逐 Run 预压缩、规范 Snapshot Chain 与无 LLM 晋升；
6. Run 完成后的后台补齐与请求内同步追赶；
7. `context_full`、`memory_compaction_failed` 与监控；
8. 通过 SSE 暴露压缩状态和上下文占用率。

首期不做：

- 跨 Conversation 长期记忆；
- 用户画像与隐式偏好推断；
- Semantic、Episodic、Procedural Memory 分类；
- RAG、Embedding 或向量检索；
- 根据下一轮问题预测相关性；
- 超出 Active + 两条 Staged Candidate 的长期版本历史；
- 外部任务队列、Outbox 与跨数据库 exactly-once；
- Pending Tool、审批状态或 Tool delivery 感知的压缩策略；
- 用 Memory 替代 Transcript、Domain Truth 或 Checkpoint；
- 因摘要而删除原始消息。
- 裁剪、投影或改写主模型收到的 Experience 业务数据、Pending Tool Result 与 Tool Schema。

这套最小闭环已经可以解决当前的上下文无界增长问题。

---

## 15. 实现索引

- `app/ai_chat/model_request.py`：冻结真实模型请求并计算 `Messages + Tools` Token；
- `app/ai_chat/context.py`：按完整 Run 裁剪、同步追赶、晋升与最终硬校验；
- `app/ai_chat/memory/`：Operations、RunBundle、摘要器、Snapshot Chain 与后台预压缩；
- `app/ai_chat/repositories/memory_repository.py`：Active Pointer、租约、CAS 与 Rebase；
- `app/ai_chat/services/service.py`：无 Message 的 Run reservation、SSE 与失败收敛；
- Experience AI Chat 前端：压缩状态、上下文占用率与 `context_full` 提示。
