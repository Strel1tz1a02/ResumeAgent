# Agent Runtime 统一设计

**状态：** Implemented

**日期：** 2026-08-17

**范围：** AI Chat、Experience、JD Import、Resume Generation

## 1. 核心结论

> 业务继续拥有各自的 Graph；Runtime 统一 Event、Context、Interaction、Run 生命周期和 Graph 执行接口。

本次改造不把所有业务状态塞入同一张表，也不建立一张固定业务 Graph。统一的是控制面协议和状态所有权：

- Runtime 只认识 `Run`、`RuntimeCommand`、`InteractionRequest`、`RuntimeEvent` 和 `GraphOutcome`；
- 业务 Adapter 负责 Graph 拓扑、领域状态、交互载荷校验和业务结果；
- Checkpoint 只保存 Graph 执行位置和领域临时状态，不再作为 Run 或 Interaction 的业务真相源；
- Tool Call、领域实体和生成产物继续由各自仓储维护。

## 2. 统一事件

后端和前端共享一个事件信封：

```text
RuntimeEvent
├─ type
├─ run_id
├─ sequence
└─ payload
```

核心事件集合：

```text
run.started
output.delta
interaction.requested
interaction.resolved
result.available
run.suspended
run.completed
run.failed
run.cancelled
command.replayed
```

审批、问题批次和未来人工确认统一映射为 `interaction.requested`，差异放在 `payload.kind` 与领域载荷中。工具执行和 JD 导入结果统一映射为 `result.available`，差异放在 `payload.kind`。

业务可以增加命名空间事件，但通用 Runtime 不得按业务事件名分支。

## 3. 统一命令与 Interaction

`AdapterInput` 继续作为首次启动 Graph 的内部输入；恢复统一使用：

```text
ResolveInteractionCommand
├─ type = resolve_interaction
├─ interaction_id
├─ client_resolution_id
└─ payload
```

当前 Interaction 由持久化 Tool Call 承载：

- `awaiting_approval` -> `kind=approval`；
- `awaiting_input` -> 领域定义的 Interaction kind，例如 `question_batch`；
- `interaction_id` 使用稳定 Tool Call ID；
- Resolution 必须先持久化，再以只包含身份的 `GraphResumeCommand` 恢复 Graph；
- Graph 恢复后重新读取持久状态，不相信客户端载荷或旧 checkpoint 中的决定。

Adapter 提供领域输入校验和固化方法，Runtime 不导入 JD、Experience 等领域类型。

## 4. 统一 Graph 接口

Graph Runner 的流输出只包含两类值：

```text
RuntimeEvent
GraphOutcome
  ├─ completed
  └─ waiting(InteractionRequest)
```

Graph 使用结构化 `interrupt(InteractionRequest)`。Runner 负责解析 LangGraph 的 `Interrupt`，业务服务不再接触 `_graph.interrupted`、checkpoint 私有字段或领域 State key。

业务 Graph 可以完全不同，但都必须遵守：

- 模型增量使用 `output.delta`；
- 需要外部输入时返回一个已持久化的 `InteractionRequest`；
- 完成时由 Runner 产生 `completed` Outcome；
- 领域结果使用 `result.available`；
- interrupt 前不得执行尚未幂等固化的副作用。

## 5. 统一 Run 生命周期

Runtime 是 Run 生命周期的唯一写入者：

```text
running -> suspended -> running
running -> completed
running -> failed
running/suspended -> cancelled
```

Graph 不直接更新 Run。Runtime 根据 `GraphOutcome` 转换状态：

- `waiting`：原子提交 `suspended`，随后发送 `run.suspended` 和 `interaction.requested`；
- `completed`：提交消息、Tool Result 投递状态和 `completed`，随后发送 `run.completed`；
- 异常或取消：通过同一 Lifecycle Service 收敛到终态。

Resume Generation 的 `previewed/confirmed` 属于产物生命周期，不再冒充 Graph Run 状态；接入时拆分通用运行状态和领域产物状态。

## 6. 统一上下文组装

Runtime 提供固定组装管线：

```text
系统指令
-> 领域事实 Section
-> 长期 Memory
-> 短期消息
-> 待补传 Tool Result
-> 当前用户消息
-> Token 预算校验
```

业务只声明：

- System 指令；
- 有名称的领域事实 Section；
- 本次可见工具。

Runtime 负责数据边界标记、稳定顺序、Tool Result 配对、Memory 插入、Token 预算和最终消息构造。结构化模型调用可以保留领域 Schema，但必须通过同一请求预算与来源标记组件。

## 7. 状态所有权

| 状态 | 唯一真相源 |
|---|---|
| Run 生命周期 | Run Repository |
| Interaction 等待与解决 | Tool Call / 后续独立 Interaction Store |
| Graph 执行位置 | Checkpoint |
| Tool 执行结果 | Tool Call |
| 对话长期记忆 | Memory Snapshot |
| JD、Experience、Resume | 领域数据库 |
| 生成预览与确认 | Resume Generation Repository |

“统一状态”表示每类状态只有一个权威写入者，不表示只有一个物理数据库。

## 8. 迁移顺序

1. 增加 Runtime Event、Command、Interaction 和 GraphOutcome 类型；
2. GraphRunner 输出统一 Outcome，并解析结构化 Interrupt；
3. Experience 与 JD Import 改用统一 Interaction；
4. AiChatService 删除业务事件和 checkpoint 字段分支；
5. 前端共用 Runtime SSE Parser 和事件类型；
6. 提取 ContextAssembler 并迁移 Experience；
7. Resume Generation 接入统一 Graph Driver 与 Run Lifecycle；
8. 删除旧事件、旧恢复入口和兼容分支。

## 9. 完成标准

- `ai_chat` 不导入 `experience`、`jd_import` 或 `resume_generation`；
- Runtime 不识别任何业务事件名或领域 State key；
- 所有 Graph 通过统一 Driver 执行并返回 `GraphOutcome`；
- 所有外部输入通过统一 Interaction 命令解决；
- 前端只使用一套 SSE Parser 和 Runtime Event 信封；
- Experience 对话由统一 ContextAssembler 构造最终模型请求；
- Resume Generation 使用通用运行状态，预览确认保持领域状态；
- 中断、恢复、幂等重放、取消和崩溃恢复测试通过；
- 后端与前端全量测试通过。
