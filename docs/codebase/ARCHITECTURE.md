# 架构

## 1) 架构风格

- 主风格：模块化单体 + 领域插件式 Agent Graph + 横向 Workflow 能力包。
- 一句话边界：Runtime 统一 Executor、Outcome、Run、Interaction、Event、Context 与 Tool 生命周期；业务保留自己的节点、边、领域状态、工具定义与结果模型。
- 约束一：workflow_runtime 不得导入 Conversation 或任何具体业务。
- 约束二：持久化事实必须先提交，事件随后发出；checkpoint 不是业务事实源。
- 约束三：模型输入必须经 ContextAssembler，恢复必须经持久化 Interaction 身份。

证据：docs/superpowers/specs/2026-08-17-agent-runtime-unification-design.zh-CN.md；apps/backend/tests/unit/test_agent_runtime_boundaries.py。

## 2) 本轮统一改造的总图

~~~text
Experience/JD HTTP ──┬─> ConversationService（创建、用户轮次、关闭）
                     └─> InteractionCoordinator（固化、恢复、继续）
                              │
             ┌────────────────┴────────────────┐
             ▼                                 ▼
 Experience BusinessGraph              JD BusinessGraph
   （ExperienceState）                    （JDImportState）
      checkpoint=Run ID                   checkpoint=Run ID
             └────────────────┬────────────────┘
                              ▼
                       ┌──────────────▼───────────────┐
                       │ workflow_runtime toolkit     │
                       │ Command / Interaction        │
                       │ InteractionCoordinator       │
                       │ Workflow/Graph Executor      │
                       │ RunStateMachine / Lifecycle  │
                       │ ContextAssembler             │
                       │ ToolLifecycle / Store ports  │
                       │ RuntimeEvent / SSE           │
                       └──────────────┬───────────────┘
                                      │
                   Run DB + ToolCall/Interaction + checkpoint + domain DB
                                      │
                    frontend parseRuntimeSse -> domain projections

Resume one-shot service ──> 按需使用 GraphExecutor / RunStateMachine /
                            ContextAssembler / ToolLifecycleService /
                            InMemoryToolCallStore / RuntimeEvent
~~~

所以“Graph 放上游”不等于把三张业务图合成一张；上游只持有可执行 Graph 的端口，具体图由 Workflow 注入。

## 3) 输入为什么不是一套对象

ConversationInput 解决每个新 Run 的可信内部输入；新 Run 启动与已有 Run 的 Interaction 恢复是两个不同边界。当前分四层：

| 层 | 解决的问题 | 是否包含业务载荷 | 证据 |
|----|------------|------------------|------|
| HTTP domain schema | 前端提交的业务请求是否合法 | 是 | experience/routers/ai_chat.py；jd_import/schemas/agent.py |
| ConversationInput | 每个新 Conversation Run 的完整内部快照 | messages、tools、subject 等轮次数据 | ai_chat/types/conversation_input.py:8 |
| ResolveInteractionCommand | 解决哪个 Run 的哪个 Interaction，如何幂等 | 是；含 client_resolution_id 与领域 payload | workflow_runtime/protocol.py |
| GraphResumeCommand | 唤醒哪个 checkpoint 等待点 | 否；只含 run_id、interaction_id | workflow_runtime/protocol.py |

不能把新 Run 和 Interaction resolution 合并：前者初始化并启动一张新的业务 Graph，后者必须带 interaction 身份与幂等键恢复同一个 Run checkpoint。Interaction 的正确路径是：

~~~text
client payload
  -> domain Workflow validates
  -> CAS persists decision/result + client_resolution_id
  -> Graph receives identity-only resume
  -> resumed node reloads durable Tool Call
~~~

这避免把未校验客户端载荷塞进 checkpoint，也允许相同命令重放、冲突命令拒绝。

## 4) 系统执行流

### 4.1 每轮 Conversation 输入

1. 业务 router 的会话端点依赖 ConversationService，Interaction 端点直接使用 InteractionCoordinator；两者共用 Runtime SSE response。
2. ConversationService 通过内部 ConversationTurnCoordinator 创建新 Run、组装 ConversationInput 并发出 run.started。
3. ConversationTurnCoordinator 以 Run ID 调用 CheckpointedWorkflowExecutor.stream；Workflow.init_state 每个 Run 生成完整领域 State，Workflow.build_graph 返回业务 Graph。
4. GraphExecutor 以 Run ID 作为 checkpoint thread：业务 Graph 完成得到 GraphOutcome.completed，Interaction interrupt 得到 GraphOutcome.waiting。
5. ConversationRunWriter 先原子提交 Run、消息与 Tool Result delivery，再发 suspended/completed/failed 事件。
6. 下一条用户消息创建新的 Run 和新的业务 Graph checkpoint；多轮语义由 Conversation 持久化、MemoryService 与 ContextAssembler 提供，不依赖 Graph 循环。
7. 前端 parseRuntimeSse 只解析统一 envelope，Experience/JD 再把 payload.kind 投影为各自 UI 状态。

证据：ai_chat/services/conversation_service.py；ai_chat/services/conversation_execution.py；workflow_runtime/graph/runner.py；workflow_runtime/graph/driver.py；ai_chat/streaming/sse.py；frontend/lib/api/runtime-events.ts。

### 4.2 Interaction 中断与恢复

1. 业务 Graph 先把等待内容持久化为 Tool Call/Interaction，再产生 InteractionRequest。
2. GraphExecutor 将业务 Graph 的结构化 interrupt 规范化为 GraphOutcome.waiting。
3. Lifecycle 将 Run 置为 suspended；随后发送 run.suspended 与 interaction.requested。
4. 前端用 run_id、interaction_id、client_resolution_id 调用统一形状的 resolve 路径，router 直接进入 Runtime InteractionCoordinator。
5. InteractionCoordinator 通过宿主 InteractionStore 定位 Workflow、Run checkpoint 与 Run；Workflow 校验领域 payload，Tool approval/input 服务以 CAS 方式持久化并保证幂等。
6. InteractionCoordinator 调用 GraphExecutor recover checkpoint，以 RunStateMachine 把 Run 切回 running，再以 identity-only command 恢复业务 Graph。
7. 业务 Graph 完成后，当前 Run 收敛为 completed。
8. 已提交过的同一命令返回 command.replayed；不同 payload/id 冲突。

证据：experience/graph/builder.py；jd_import/graph/builder.py；workflow_runtime/interactions.py；workflow_runtime/tools/approval_service.py；workflow_runtime/tools/input_service.py；ai_chat/persistence/interaction_store.py。

当前限制：一个 checkpoint 只能有一个 active Interaction；多个 interrupt 会被 GraphExecutor 拒绝。JD 因此把最多 12 个问题封装为一个 question_batch。它还不是支持并行子 Agent、多审批 join 的 Harness。

## 5) 状态统一的准确口径

“统一到一个地方”不是把所有数据塞进一张表，而是每类状态只有一个事实源和一个写入者：

| 状态类别 | 事实源 | 写入者 | checkpoint 是否权威 |
|----------|--------|--------|---------------------|
| Run 生命周期 | ai_chat_runs 或领域 RunStore | InteractionCoordinator / RunStateMachine / 宿主 RunStore | 否 |
| 等待请求、决定、输入、工具结果 | ToolCallStore；Conversation 当前使用 SQLAlchemy 实现 | workflow_runtime 的 Tool approval/input/execution 服务 | 否 |
| Graph 执行位置、节点临时态 | LangGraph checkpoint DB；每个 Run 使用独立 thread | LangGraphExecutor | 是，仅对此类别 |
| Experience/JD 等领域对象 | 各领域数据库/repository | 领域服务 | 否 |
| Resume preview/confirmed | Resume artifact_status | Resume Generation service | 否 |
| 前端视图状态 | React/TanStack Query 投影 | 领域 hooks/components | 否 |

RunStateMachine 上游化的是状态词汇与合法转换；具体业务仍通过 RunStore 写自己的表，因此 int/string Run ID 和不同 ORM 模型可以共存。

## 6) Context 统一

ContextAssembler 固定普通对话组装顺序：

~~~text
system
-> domain sections
-> memory
-> short-term messages
-> pending tool results
-> current user
-> budget/truncation
~~~

- Domain/history 被明确包在 untrusted data boundary 内。
- pending Tool Result 组装成 assistant tool-call + tool result 配对。
- Experience 使用完整 conversation assembly。
- JD plan 使用完整 assembly；抽取/结构化修复使用 assemble_structured。
- Resume Generation 使用 assemble_structured。
- 原 Experience 私有 context.py 已删除，调用迁移到统一 assembler。

证据：workflow_runtime/context.py；experience/workflow.py；jd_import/graph/builder.py；jd_import/agent/model.py；resume_generation/model.py。

边界仍不是“仓库所有 LLM 调用都已迁移”：旧 resumes/improver/refiner 等非本轮 Agent 路径仍直接使用 app/llm.py。统一范围应由 [ASK USER] 决定。

## 7) 事件统一

后端统一 envelope：

~~~text
RuntimeEvent {
  type: string,
  run_id?: int | string,
  sequence?: positive integer,
  payload: object
}
~~~

核心事件：run.started、output.delta、interaction.requested、interaction.resolved、result.available、run.suspended、run.completed、run.failed、run.cancelled、command.replayed。

改造效果：

- Experience 与 JD Graph 不再发 proposal.*、jd.* 等业务事件名。
- 业务差异下沉为 payload.kind，例如 approval、question_batch、tool_result、jd_import。
- 两个 router 共用 runtime_sse_response。
- 前端只剩 parseRuntimeSse 一套 parser；两个 API 客户端删除各自的 TextDecoder/getReader 解析器。

证据：workflow_runtime/events.py；ai_chat/streaming/sse.py；frontend/lib/api/runtime-events.ts；frontend/lib/api/experience-ai-chat.ts；frontend/lib/api/jd-imports.ts。

这只是 envelope/transport 统一，不是强类型事件 schema 或可重放事件日志：sequence 当前按 SSE 连接临时生成，前端只校验、不去重、不重连，也没有 Last-Event-ID。

## 8) Run 与 Graph 上游化

| 上游能力 | 抽象 | 业务保留 |
|----------|------|----------|
| Graph 执行 | GraphExecutor.stream/resume/recover | 节点、边、State schema |
| Graph 收口 | GraphOutcome.completed/waiting | 领域 result payload |
| Graph 接入 | Workflow + CheckpointedWorkflowExecutor | ConversationWorkflow 只增加 Conversation 绑定校验 |
| Conversation Run | 每个 Run 调用 init_state/build_graph，checkpoint thread=Run ID | ConversationService 管理跨 Run 会话与消息 |
| Interaction 恢复 | InteractionCoordinator + InteractionStore 端口 | Conversation/一次性任务各自实现绑定和 Run 持久化 |
| Tool 风险 | ToolOperation.risk + RegisteredTool | 审批展示数据与领域执行逻辑 |
| Tool 生命周期 | ToolLifecycleService + ToolCallStore 端口 | SQL/远端等存储实现；Runtime 自带 InMemory 实现 |
| Run 规则 | RunStateMachine + RunStore | ORM 表、ID 类型、产物 |
| Conversation 新 Run 原子收口 | ConversationRunWriter | assistant message、Tool Result delivery |

具体拓扑仍独立：

- Experience：LLM -> validator -> risk -> approval interrupt -> executor。
- JD Import：Workflow.init_state -> URL/source -> extract -> assess -> plan -> question interrupt -> merge -> persist。
- Resume Generation：plan/retrieve/generate -> result.available；当前无 checkpointer。

证据：experience/graph/builder.py；jd_import/graph/builder.py；resume_generation/graph.py。

## 9) 三个业务的接入成熟度

| 能力 | Experience | JD Import | Resume Generation |
|------|------------|-----------|-------------------|
| ConversationWorkflow / resolver | 是 | 是 | 否 |
| CheckpointedWorkflowExecutor / GraphExecutor | 是 / 是 | 是 / 是 | 否 / 是 |
| RuntimeEvent / GraphOutcome | 是 | 是 | 仅 result/outcome |
| 统一 SSE / 前端 parser | 是 | 是 | 否，仍为同步 REST |
| Interaction interrupt/resolve | approval | question_batch | 无 |
| checkpoint recover | 是 | 是 | 否 |
| ContextAssembler | 完整 | 完整 + structured | structured |
| 通用 Run 状态 | 是 | 是 | 是 |
| 独立 artifact 状态 | 领域 revision/tool result | 最终 JD | previewed/confirmed |

核心结论：Experience/JD 已完成主控制面接入；Resume Generation 只完成 GraphExecutor、Context、Run 状态和结果事件格式复用，不能称为完整 Runtime 客户。

## 10) 从工作树可见的实施清单

本轮工作树相对 HEAD 3e0de4c 仍有大量未提交改动；截至 2026-08-20，`git status --short` 显示 71 个 tracked 状态项和 25 个 untracked 项（后者包含本目录七份代码库文档）。精确数量会随开发继续变化，架构判断应以文件职责与测试证据为准。主要工作为：

1. 新建 workflow_runtime/protocol.py：Interaction、Resolve command、identity-only resume 与 GraphOutcome。
2. 新建 workflow_runtime/graph，并把 LangGraph executor 和 checkpoint workflow executor 抽成可组合能力。
3. 新建 workflow_runtime/runs.py 与 interactions.py；Runtime 负责恢复算法，ConversationInteractionStore 负责宿主绑定与 Run 持久化，ConversationRunWriter 收口当前 Run 的消息与结果。
4. 新建 workflow_runtime/context.py，删除 Experience 私有 context.py 并迁移三业务模型调用。
5. 重写 RuntimeEvent 与 SSE encoder；前端新建 runtime-events.ts。
6. Experience/JD 改为每个 Run 独立初始化和执行的业务 Graph；Conversation 多轮由持久化 Run、消息与 Memory 串联。
7. Resume Generation 拆分 Run status 与 artifact_status，并通过 GraphExecutor/StateMachine 运行。
8. 新增交互载荷和 Resume Run 生命周期迁移。
9. 新增协议、边界、GraphExecutor、Context、Run 状态、迁移与恢复回归测试。
10. 更新旧设计文档，新增统一设计与实施计划。

证据：git status --short；git diff --stat；docs/superpowers/plans/2026-08-17-agent-runtime-unification.md。

## 11) Intent 与 Reality 偏差

| 设计意图 | 当前现实 | 判断 |
|----------|----------|------|
| Runtime 是业务无关能力包 | 通用代码已物理迁到 app.workflow_runtime，且无 Conversation/领域反向依赖 | 已完成 |
| 统一外部 Interaction command | 内部 dataclass 已统一；Experience/JD HTTP body 仍是领域 schema，且 command 没有设计稿中的 type 字段 | 合理保留领域 payload，但 wire contract 未完全统一 |
| failed/cancelled 为终态 | RunStateMachine 允许恢复到 running/completed | 策略冲突，需产品决策 |
| 所有 Agent 共享 Runtime | Resume 只部分复用，无 checkpoint/SSE/Interaction | 未完全完成 |
| 前端统一事件消费 | parser/envelope 已统一；payload 仍弱类型，sequence/replay 未实现 | transport 完成，恢复语义未完成 |
| 中断恢复统一 | interaction resolution 失败后可重试/自愈；没有非交互崩溃的公开 ResumeRun API | 只覆盖一类恢复 |
| Conversation 不需要外层 Graph | ConversationService 每次请求创建 Run；Experience/JD 以 Run ID 独立执行，Memory 注入历史 | 已完成 |
| Interaction 是通用概念 | 持久化仍复用 Tool Call 表 | 对审批/问答可用，对未来任意 human/webhook interaction 有耦合 |

## 12) 已知架构风险

- 事件不持久化，SSE sequence 每次连接重置；浏览器刷新/断线无法按 cursor 重放。
- 前端没有 pending Interaction 查询与 hydration；等待 UI 主要存在组件内存。
- GraphExecutor 只支持单 active Interaction，不支持并行等待与 join。
- Resume Generation 没有 checkpoint，也没有取消/中断/崩溃恢复入口。
- ConversationRunWriter 仍绑定 message/tool delivery，这是有意保留的会话事务边界；通用恢复由 InteractionCoordinator/RunStateMachine 负责。
- structured repair 在已预算请求后追加 validation errors，最终重试请求没有再次统一预算。

## 13) Evidence

- docs/superpowers/specs/2026-08-17-agent-runtime-unification-design.zh-CN.md
- docs/superpowers/plans/2026-08-17-agent-runtime-unification.md
- apps/backend/app/workflow_runtime/protocol.py
- apps/backend/app/workflow_runtime/graph/driver.py
- apps/backend/app/workflow_runtime/graph/runner.py
- apps/backend/app/workflow_runtime/runs.py
- apps/backend/app/workflow_runtime/interactions.py
- apps/backend/app/ai_chat/services/conversation_service.py
- apps/backend/app/ai_chat/services/conversation_execution.py
- apps/backend/app/ai_chat/services/conversation_run_writer.py
- apps/backend/app/ai_chat/persistence/interaction_store.py
- apps/backend/app/workflow_runtime/context.py
- apps/backend/app/workflow_runtime/events.py
- apps/backend/app/ai_chat/streaming/sse.py
- apps/backend/app/experience/graph/builder.py
- apps/backend/app/jd_import/graph/builder.py
- apps/backend/app/resume_generation/service.py
- apps/frontend/lib/api/runtime-events.ts
- apps/backend/tests/unit/test_agent_runtime_boundaries.py
