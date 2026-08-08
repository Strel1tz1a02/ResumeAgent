# AI Chat 有界会话记忆与上下文设计

**状态：** Accepted

**日期：** 2026-08-08

**范围：** `apps/backend/app/ai_chat` 与 Experience Adapter

**目标：** 长会话不再全量回放，模型输入始终受 Token 硬上限约束。

---

## 1. 核心结论

这个项目的对话是绑定具体 Experience 字段的编辑会话，不需要复杂的跨会话长期记忆。

本设计只保留三层：

1. **Transcript**：数据库中的完整原始对话，用于展示和审计；
2. **Short-term Memory**：最近若干个完整对话轮次，按 Token 预算选择；
3. **Conversation Memory**：超出短期窗口的旧内容，经有损摘要后保存。

模型每轮看到：

```text
System / Tools
+ 当前 scope 的业务数据
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
| Domain Truth | Experience、Evidence 等当前业务事实 | 按 scope 投影 |
| Conversation Memory | 保存旧对话的有损摘要 | 是，受独立预算限制 |
| Recent RunBundle | 保存近期完整交互 | 是，受短期预算限制 |
| Checkpoint | 恢复未完成的 Graph 执行 | 否 |

边界规则：

- Experience 与 Evidence 表始终是业务事实源；
- Memory 不能覆盖业务事实，也不能绕过 Tool 审批；
- Transcript 不因摘要而修改或删除；
- Memory 内容是派生上下文，不作为 System 指令注入；
- Tool Call、审批状态和执行位置属于 Run/Checkpoint，不属于 Memory。
- 面向前端的完整 Tool Result 不能原样进入模型上下文。

---

## 4. Token 预算

### 4.1 最终请求必须受硬上限约束

Token 边界针对最终模型请求，而不是只计算历史消息：

```text
System Prompt
+ Tool Schema
+ 当前业务投影
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
effective_input_cap = min(
    configured_input_cap,
    model_context_window - output_reserve - safety_margin
)

recent_run_budget = effective_input_cap
    - system_tokens
    - tool_schema_tokens
    - domain_tokens
    - current_user_tokens
    - pending_tool_tokens
    - memory_token_cap
```

Conversation Memory 预留固定最大预算。短期窗口只使用剩余预算，因此摘要更新不会突然挤掉已经选中的近期轮次。

所有 Token 数使用最终模型、Messages 和 Tools 的真实格式估算。最后发送前必须重新计算一次总量。

### 4.2 必须保留的内容

以下内容不能因为历史过长而静默删除：

- System Prompt 与 Tool Schema；
- 当前用户输入；
- 当前 scope 的最小业务数据；
- resolved 且等待模型投递的 Tool Call/Result 原子对。

Awaiting-approval Tool Call 此时还没有 Result，只属于 Run/Checkpoint；它不会伪装成 Tool Result 进入新的模型请求。

如果这些必要内容自身已经超限，直接返回 `context_full`，不能把请求交给模型碰运气。

### 4.3 业务数据与 Tool Result

历史窗口有界并不能解决单个业务 Payload 过大的问题：

- Experience Adapter 只注入当前 scope 所需的最小业务投影；
- Pending Tool Result 只注入模型需要的紧凑结果；
- 前端刷新所需的完整业务结果不进入模型上下文；
- 单个 Domain Projection 和 Tool Result 都有独立 Token 上限。

Experience Tool Result 的模型投影通常只需要：操作结果、操作类型、scope、revision 与 changed IDs，不应再次携带完整 Experience。

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

Pending Tool Result 不属于普通历史。它在被消费前作为独立的
`Assistant Tool Call + Tool Result` 原子块进入上下文；审批结果写入业务表后，最新 Domain Truth 才是事实来源。

判定规则：

- 当前 `run_id` 不参与历史候选，当前 User 始终单独加入；
- 文本 Run 必须有 completed Assistant；Tool Run 的 Assistant 可以为空或 cancelled；
- failed、cancelled、running、suspended Run 整体排除；
- 没有待审批 Tool Call、没有待投递 Tool Result 时，RunBundle 才能进入摘要前缀。

同一个 Tool Result 只能注入一次：

- `delivery_status=pending` 时，只通过独立 Pending Block 注入，RunBundle 不携带同一结果；
- Result 已投递后，RunBundle 才可以保留其紧凑决策/结果；
- Context Assembler 使用 `tool_call_id` 去重。

规则：

- 从最新 RunBundle 向前选择，直到达到 `recent_run_budget`；
- 不能截断半个 RunBundle；
- resolved 且 pending-delivery 的 Tool Call/Result 必须同时保留，不能参与普通历史裁剪；
- failed、cancelled 或仅有流式残片的 Assistant 不进入历史窗口；
- 非工具的文本 Run 没有 completed Assistant 时不构成稳定 RunBundle，也不参与摘要；
- 当前用户输入不属于可裁剪历史，始终单独保留；
- 裁剪只影响模型视图，不影响数据库中的 Transcript。

### 5.2 摘要水位

Memory 表维护 `covered_through_sequence`。

它表示：该序号之前的稳定完整轮次已经被 Conversation Memory 覆盖。水位取最后一个已处理 RunBundle 的最大 Message Sequence。

每轮只处理：

```text
covered_through_sequence 之后
+ 本轮被挤出短期窗口的完整 RunBundle
```

这样可以避免旧内容被重复摘要，也能保证 Memory 与近期原文之间没有空洞。

---

## 6. Conversation Memory 数据模型

每个 Conversation 最多维护一条 Memory：

```text
ai_chat_conversation_memories
├── id
├── conversation_id              UNIQUE, FK
├── core                         JSON
├── other                        JSON
├── covered_through_sequence     INTEGER
├── memory_token_count           INTEGER
├── created_at
└── updated_at
```

Conversation 删除时级联删除 Memory；摘要更新或单独删除 Memory 不得修改 Transcript。Conversation 自身的删除仍遵循现有级联规则。

`memory_token_count` 只是写入时生成的缓存。模型或 Tokenizer 配置变化后必须重新计算，不能把缓存当作最终预算依据。

### 6.1 Core

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
- 已保存的 Experience 事实不复制进 Core 或 Other。

### 6.2 Other

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

采用按需摘要：

```text
有完整 RunBundle 移出短期窗口 → 调用摘要 LLM
没有 RunBundle 移出             → 不调用摘要 LLM
```

摘要 LLM 的输入：

```text
完整的当前 Memory（core + other）
+ 本批刚移出窗口的完整 RunBundle
+ Memory Schema 与操作规则
```

如果待摘要内容本身过大，按完整 RunBundle 分成受 Token 限制的批次处理。
如果单个 RunBundle 已经超过摘要模型预算，则不能拆分它，直接返回 `context_full`。

摘要调用禁用业务 Tools，使用受 Schema 约束的 JSON 输出，并遵守独立的输入、输出 Token 上限。

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
- 整批 Operations 先校验，再在一个事务中应用；
- 校验或应用失败时，Memory 与摘要水位都保持不变。

Core 字段初始存在但内容为空，因此首次写入 Core 使用 `update`。
`update` 替换整个字段内容；需要增删数组元素时，由 LLM 返回更新后的完整数组。

合法的空 Operations 表示本批内容没有必要进入 Memory，仍然可以推进摘要水位。

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

生成 Operations 后必须重新计算完整 `core + other` 的 Token：

- 未超过上限：提交 Operations 并推进 `covered_through_sequence`；
- 超过上限：拒绝整批 Operations，不推进水位；
- Memory 无法继续容纳必要摘要时：返回 `context_full`。

---

## 8. 每轮上下文组装流程

```text
1. 读取当前 Memory 与摘要水位
2. 查询水位后的稳定完整 RunBundle
3. 计算最终请求的各类 Token 预算
4. 从新到旧选择 Recent RunBundles
5. 找出被挤出窗口的旧 RunBundles
6. 如存在旧 RunBundles，生成并应用 Memory Operations
7. 组装 Domain Truth + Memory + Recent RunBundles
8. 追加 Pending Tool Call/Result 原子块
9. 将 Current User 放在最后
10. 重新计算最终 Messages + Tools Token
11. 未超限则调用模型；仍超限则返回 context_full
```

组装器必须保证：

- Memory 覆盖前缀与 Recent RunBundles 连续；
- 同一历史内容不会既作为摘要来源又重复作为近期原文；
- 必需内容优先于 Memory 与普通历史；
- 摘要失败时不调用主模型，也绝不回退到全量历史。

---

## 9. `context_full` 行为

固定必要内容可以在摘要前预检：它们自身超限时直接返回。只有历史或 Memory 导致的超限，才先完成摘要、裁剪和最终 Token 校验，再提示上下文已满。

可能原因：

- Memory 已达到上限，无法继续吸收必要内容；
- 当前用户输入本身过长；
- 当前 scope 的必要业务数据过大；
- System、Tools 和必要状态已经占满可用预算。

提示必须根据原因给出下一步：

- 历史或 Memory 已满：提示新建对话，已保存的经历数据不会丢失；
- 当前输入过长：提示缩短本次输入；
- 必要业务投影过大：提示缩小编辑范围；
- System、Tools 或固定状态过大：返回配置错误，不能误导用户新建对话。

新 Conversation 创建新的空 Memory，只读取最新 Domain Truth，不自动携带旧 Conversation Memory。

---

## 10. 失败处理

- 摘要模型调用失败：保留旧 Memory，不推进水位，本轮返回可重试的 `memory_compaction_failed`；
- Operations JSON 无法解析：保留旧 Memory，不推进水位，本轮返回 `memory_compaction_failed`；
- Operation 路径或类型非法：拒绝整批操作并返回 `memory_compaction_failed`；
- 更新后的 Memory 超限：拒绝整批操作并返回 `context_full`；
- Token 估算失败：禁止调用主模型，返回稳定错误；
- 主模型仍返回供应商上下文溢出：记录指标并映射为 `context_full`；
- 任何摘要失败都不能修改 Transcript、Experience 或 Tool 状态。

摘要失败后，下一次重试仍处理同一批未推进水位的旧内容。成功摘要的主动取舍可以有损，摘要过程异常不能静默制造历史缺口。

为保证同一个 `client_message_id` 可以真正重试，目标流程调整为：

```text
1. 先创建不含 Message 的 Run reservation，阻止并发 Run
2. 使用原始 user_content 完成 Token 预检、摘要和上下文准备
3. 预检失败：run.failed，不创建 User/Assistant Message，不占用 client_message_id
4. 预检成功：再原子创建 completed User 与 generating Assistant
```

因此相同请求可以在预检失败后重试。User/Assistant 创建后的执行失败，仍按现有规则收敛为 `run.failed + assistant.failed`，Conversation 必须解除 busy 状态。

失败前没有实际投递的 Pending Tool Result 不能被标记为 consumed。

---

## 11. 组件职责

| 组件 | 职责 |
|---|---|
| Token Counter | 估算最终 Messages、Tools 和 Memory Token |
| Run Bundle Builder | 按 `run_id` 组装稳定 Message 与紧凑 Tool 结果 |
| Context Assembler | 计算预算、选择近期轮次、执行最终硬校验 |
| Memory Repository | 读取和事务更新单条 Conversation Memory |
| Memory Summarizer | 调用 LLM，生成受 Schema 约束的 Operations |
| Memory Service | 校验 Operations、应用更新、推进摘要水位 |
| Experience Adapter | 提供当前 scope 的最小业务投影和紧凑 Tool Result |

共享 AI Chat 层负责预算与记忆机制。Experience Adapter 只负责业务数据选择，不自行决定历史窗口。

---

## 12. 可观测性

每次主模型调用至少记录：

- 最终输入估算 Token；
- System、Tools、Domain、Memory、Recent RunBundles、Pending Tools、Current User 各自 Token；
- 选中的 RunBundle 数量；
- Memory Token 与 Other Key 数量；
- 摘要触发次数、输入轮次数量与耗时；
- 摘要失败原因；
- `context_full` 原因。

日志只记录计数、ID、耗时和错误码，不记录完整对话、Memory 或业务正文。

---

## 13. 测试与验收

### 13.1 短期窗口

- 不同长度消息下均按 Token 而不是固定轮数裁剪；
- RunBundle 不会被拆开；
- Tool Call/Result 不会被拆开；
- Tool Result 模型投影不会携带完整 Experience；
- 当前 Run 不会作为历史再次加入；
- failed、cancelled Run 和流式残片不会进入历史窗口；
- opening Run 的 Assistant-only Bundle 可以正常保留；
- 存在待审批或待投递结果的 Run 不会进入摘要前缀；
- 当前用户输入始终保留；
- Transcript 不因裁剪而变化。

### 13.2 Memory

- 只有刚移出窗口且尚未覆盖的轮次参与摘要；
- 没有轮次移出时不调用摘要 LLM；
- `add` 只能创建 Other 字段；
- `update` 不能创建未知 Core 字段；
- 删除 Core 只清空内容，删除 Other 会移除 Key；
- 合法空 Operations 可以正常推进摘要水位；
- 非法 Operations 不产生部分更新；
- Summary 失败时摘要水位不推进；
- Memory 永远不超过独立 Token 上限；
- 不重要内容可以只保留在 Transcript。

### 13.3 最终上下文

- 本地计数判定超限的 `Messages + Tools` 请求绝不发送；供应商仍判定溢出时稳定映射为 `context_full`；
- 100 轮以上会话的输入 Token 不再随总轮数线性增长；
- Domain Truth 被明确标记为当前权威数据，Memory 没有写业务事实的权限；
- 摘要失败不会调用主模型，也不会回退到全量历史；
- 上下文预检失败只终结 Run reservation，不创建失败的 User/Assistant Message；
- 相同 `client_message_id` 可以在预检失败后重新执行；
- User/Assistant 创建后的失败会正常收敛，不锁死 Conversation；
- 无法形成合法请求时稳定返回 `context_full`；
- 新 Conversation 不自动继承旧 Memory。

---

## 14. 实施边界

首期实现：

1. Token Counter 与最终请求硬预算；
2. RunBundle 构建和近期窗口选择；
3. Conversation Memory 表；
4. Core、Other 与 Operations 校验；
5. 按需摘要和摘要水位；
6. `context_full` 错误与监控；
7. Experience 业务数据与 Tool Result 的模型侧投影。

首期不做：

- 跨 Conversation 长期记忆；
- 用户画像与隐式偏好推断；
- Semantic、Episodic、Procedural Memory 分类；
- RAG、Embedding 或向量检索；
- 根据下一轮问题预测相关性；
- 多版本 Memory Snapshot；
- 异步摘要任务、Outbox 与复杂 CAS；
- 用 Memory 替代 Transcript、Domain Truth 或 Checkpoint；
- 因摘要而删除原始消息。

这套最小闭环已经可以解决当前的上下文无界增长问题。
