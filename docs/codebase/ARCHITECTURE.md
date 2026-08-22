# 架构

## 1) 架构风格

- 主风格：模块化单体 + 领域插件式 Agent Graph + 事件驱动的 Runtime 控制面。
- 一句话边界：上游统一的是 Driver、Outcome、Run、Interaction、Event、Context；下游保留每个业务自己的节点、边、领域状态与结果模型。
- 约束一：Runtime 不得导入 Experience、JD Import、Resume Generation。
- 约束二：持久化事实必须先提交，事件随后发出；checkpoint 不是业务事实源。
- 约束三：模型输入必须经 ContextAssembler，恢复必须经持久化 Interaction 身份。

证据：docs/superpowers/specs/2026-08-17-agent-runtime-unification-design.zh-CN.md；apps/backend/tests/unit/test_agent_runtime_boundaries.py。

## 2) 本轮统一改造的总图

~~~text
                         ┌── Experience Adapter ── Experience Graph
HTTP / domain schema ────┼── JD Adapter ────────── JD Graph
                         └── Resume service ─────── Resume Graph（部分接入）
                                      │
                       ┌──────────────▼───────────────┐
                       │ Runtime control plane        │
                       │ Command / Interaction        │
                       │ GraphDriver / GraphOutcome   │
                       │ RunStateMachine / Lifecycle  │
                       │ ContextAssembler             │
                       │ RuntimeEvent / SSE           │
                       └──────────────┬───────────────┘
                                      │
                   Run DB + ToolCall/Interaction + checkpoint + domain DB
                                      │
                    frontend parseRuntimeSse -> domain projections
~~~

所以“Graph 放上游”不等于把三张业务图合成一张；上游只持有可执行 Graph 的端口，具体图由 Adapter 注入。

## 3) 输入为什么不是一套对象

已有 AdapterInput，但它只解决首次 start；恢复协议解决的是另一个信任边界。当前分四层：

| 层 | 解决的问题 | 是否包含业务载荷 | 证据 |
|----|------------|------------------|------|
| HTTP domain schema | 前端提交的业务请求是否合法 | 是 | experience/routers/ai_chat.py；jd_import/schemas/agent.py |
| AdapterInput | 首次启动 Graph 的完整内部快照 | messages、tools、subject 等启动数据 | ai_chat/types/adapter_input.py:8 |
| ResolveInteractionCommand | 解决哪个 Run 的哪个 Interaction，如何幂等 | 是；含 client_resolution_id 与领域 payload | ai_chat/protocol.py:65 |
| GraphResumeCommand | 唤醒哪个 checkpoint 等待点 | 否；只含 run_id、interaction_id | ai_chat/protocol.py:106 |

不能用 AdapterInput 直接恢复，原因是它没有 interaction 身份、幂等键、领域 resolution 与 replay 语义。当前正确路径是：

~~~text
client payload
  -> domain Adapter validates
  -> CAS persists decision/result + client_resolution_id
  -> Graph receives identity-only resume
  -> resumed node reloads durable Tool Call
~~~

这避免把未校验客户端载荷塞进 checkpoint，也允许相同命令重放、冲突命令拒绝。

## 4) 系统执行流

### 4.1 首次运行

1. 业务 router 将请求交给 AiChatService；Experience/JD 共用 Runtime SSE response。
2. AiChatService 创建 Run 并发出 run.started，再从数据库组装 AdapterInput。
3. GraphRunner 从 AdapterRegistry 取得业务 Adapter，构建并缓存该业务自己的 Graph。
4. GraphDriver 只接收 graph 与 graph_input，产出 RuntimeEvent 或 GraphOutcome。
5. AiChatService/RunLifecycleService 先原子提交 Run、消息与 Tool Result delivery，再发 suspended/completed/failed 事件。
6. 前端 parseRuntimeSse 只解析统一 envelope，Experience/JD 再把 payload.kind 投影为各自 UI 状态。

证据：ai_chat/services/ai_chat_service.py；graph/runner.py；graph/driver.py；streaming/sse.py；frontend/lib/api/runtime-events.ts。

### 4.2 Interaction 中断与恢复

1. 业务 Graph 先把等待内容持久化为 Tool Call/Interaction，再产生 InteractionRequest。
2. Driver 将一个结构化 interrupt 规范化为 GraphOutcome.waiting。
3. Lifecycle 将 Run 置为 suspended；随后发送 run.suspended 与 interaction.requested。
4. 前端用 run_id、interaction_id、client_resolution_id 调用统一形状的 resolve 路径。
5. Adapter 校验领域 payload；Tool approval/input 服务以 CAS 方式持久化并保证幂等。
6. Driver recover checkpoint，Run 回到 running，再以 identity-only command 恢复。
7. 已提交过的同一命令返回 command.replayed；不同 payload/id 冲突。

证据：experience/graph/builder.py:178；jd_import/graph/builder.py:224；ai_chat/tools/approval/service.py；ai_chat/tools/input.py；ai_chat/services/ai_chat_service.py:331。

当前限制：一个 checkpoint 只能有一个 active Interaction；多个 interrupt 会被 Driver 拒绝。JD 因此把最多 12 个问题封装为一个 question_batch。它还不是支持并行子 Agent、多审批 join 的 Harness。

## 5) 状态统一的准确口径

“统一到一个地方”不是把所有数据塞进一张表，而是每类状态只有一个事实源和一个写入者：

| 状态类别 | 事实源 | 写入者 | checkpoint 是否权威 |
|----------|--------|--------|---------------------|
| Run 生命周期 | ai_chat_runs 或领域 RunStore | RunLifecycleService / RunStateMachine | 否 |
| 等待请求、决定、输入、工具结果 | 当前为 Tool Call 表 | Tool approval/input/execution 服务 | 否 |
| Graph 执行位置、节点临时态 | LangGraph checkpoint DB | LangGraph Driver | 是，仅对此类别 |
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

证据：ai_chat/context/assembler.py；experience/adapters/adapter.py；jd_import/graph/builder.py；jd_import/agent/model.py；resume_generation/model.py。

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

证据：ai_chat/streaming/events.py、streaming/sse.py；frontend/lib/api/runtime-events.ts；frontend/lib/api/experience-ai-chat.ts；frontend/lib/api/jd-imports.ts。

这只是 envelope/transport 统一，不是强类型事件 schema 或可重放事件日志：sequence 当前按 SSE 连接临时生成，前端只校验、不去重、不重连，也没有 Last-Event-ID。

## 8) Run 与 Graph 上游化

| 上游能力 | 抽象 | 业务保留 |
|----------|------|----------|
| Graph 执行 | GraphDriver.stream/resume/recover | 节点、边、State schema |
| Graph 收口 | GraphOutcome.completed/waiting | 领域 result payload |
| Graph 接入 | BaseAdapter + GraphRunner | build_graph、parse_input、resolve_interaction、tools/policy |
| Run 规则 | RunStateMachine + RunStore | ORM 表、ID 类型、产物 |
| 对话原子收口 | RunLifecycleService | assistant message、Tool Result delivery |

具体拓扑仍独立：

- Experience：LLM -> validator -> risk -> approval interrupt -> executor。
- JD Import：parse -> URL/source -> extract -> assess -> plan -> question interrupt -> merge -> persist。
- Resume Generation：plan/retrieve/generate -> result.available；当前无 checkpointer。

证据：experience/graph/builder.py；jd_import/graph/builder.py；resume_generation/graph.py。

## 9) 三个业务的接入成熟度

| 能力 | Experience | JD Import | Resume Generation |
|------|------------|-----------|-------------------|
| BaseAdapter / registry | 是 | 是 | 否 |
| GraphRunner / GraphDriver | 是 / 是 | 是 / 是 | 否 / 是 |
| RuntimeEvent / GraphOutcome | 是 | 是 | 仅 result/outcome |
| 统一 SSE / 前端 parser | 是 | 是 | 否，仍为同步 REST |
| Interaction interrupt/resolve | approval | question_batch | 无 |
| checkpoint recover | 是 | 是 | 否 |
| ContextAssembler | 完整 | 完整 + structured | structured |
| 通用 Run 状态 | 是 | 是 | 是 |
| 独立 artifact 状态 | 领域 revision/tool result | 最终 JD | previewed/confirmed |

核心结论：Experience/JD 已完成主控制面接入；Resume Generation 只完成 Driver、Context、Run 状态和结果事件格式复用，不能称为完整 Runtime 客户。

## 10) 从工作树可见的实施清单

本轮工作树相对 HEAD 3e0de4c 仍有大量未提交改动；截至 2026-08-20，`git status --short` 显示 71 个 tracked 状态项和 25 个 untracked 项（后者包含本目录七份代码库文档）。精确数量会随开发继续变化，架构判断应以文件职责与测试证据为准。主要工作为：

1. 新建 protocol.py：Interaction、Resolve command、identity-only resume、GraphOutcome。
2. 新建 graph/driver.py，并把 runner.py 从 LangGraph/业务细节改为通用委托。
3. 新建 run_state.py、services/run_lifecycle.py，拆出状态机和原子收口。
4. 新建 context/assembler.py，删除 Experience 私有 context.py 并迁移三业务模型调用。
5. 重写 RuntimeEvent 与 SSE encoder；前端新建 runtime-events.ts。
6. Experience/JD 改为 Adapter 自有 Graph + 结构化 Interaction + 通用 result.available。
7. Resume Generation 拆分 Run status 与 artifact_status，并通过 Driver/StateMachine 运行。
8. 新增交互载荷和 Resume Run 生命周期迁移。
9. 新增协议、边界、Driver、Context、Run 状态、迁移与恢复回归测试。
10. 更新旧设计文档，新增统一设计与实施计划。

证据：git status --short；git diff --stat；docs/superpowers/plans/2026-08-17-agent-runtime-unification.md。

## 11) Intent 与 Reality 偏差

| 设计意图 | 当前现实 | 判断 |
|----------|----------|------|
| Runtime 是业务无关包 | 依赖方向已无反向导入，但通用代码仍放在 app.ai_chat | 逻辑上完成，物理包未上移 |
| 统一外部 Interaction command | 内部 dataclass 已统一；Experience/JD HTTP body 仍是领域 schema，且 command 没有设计稿中的 type 字段 | 合理保留领域 payload，但 wire contract 未完全统一 |
| failed/cancelled 为终态 | RunStateMachine 允许恢复到 running/completed | 策略冲突，需产品决策 |
| 所有 Agent 共享 Runtime | Resume 只部分复用，无 checkpoint/SSE/Interaction | 未完全完成 |
| 前端统一事件消费 | parser/envelope 已统一；payload 仍弱类型，sequence/replay 未实现 | transport 完成，恢复语义未完成 |
| 中断恢复统一 | interaction resolution 失败后可重试/自愈；没有非交互崩溃的公开 ResumeRun API | 只覆盖一类恢复 |
| Interaction 是通用概念 | 持久化仍复用 Tool Call 表 | 对审批/问答可用，对未来任意 human/webhook interaction 有耦合 |

## 12) 已知架构风险

- 事件不持久化，SSE sequence 每次连接重置；浏览器刷新/断线无法按 cursor 重放。
- 前端没有 pending Interaction 查询与 hydration；等待 UI 主要存在组件内存。
- Driver 只支持单 active Interaction，不支持并行等待与 join。
- Resume Generation 没有 checkpoint，也没有取消/中断/崩溃恢复入口。
- RunLifecycleService 仍绑定 conversation/message/tool delivery；真正跨业务的是 RunStateMachine，不应误称所有生命周期已统一。
- structured repair 在已预算请求后追加 validation errors，最终重试请求没有再次统一预算。

## 13) Evidence

- docs/superpowers/specs/2026-08-17-agent-runtime-unification-design.zh-CN.md
- docs/superpowers/plans/2026-08-17-agent-runtime-unification.md
- apps/backend/app/ai_chat/protocol.py
- apps/backend/app/ai_chat/graph/driver.py
- apps/backend/app/ai_chat/graph/runner.py
- apps/backend/app/ai_chat/run_state.py
- apps/backend/app/ai_chat/services/ai_chat_service.py
- apps/backend/app/ai_chat/services/run_lifecycle.py
- apps/backend/app/ai_chat/context/assembler.py
- apps/backend/app/ai_chat/streaming/events.py
- apps/backend/app/ai_chat/streaming/sse.py
- apps/backend/app/experience/graph/builder.py
- apps/backend/app/jd_import/graph/builder.py
- apps/backend/app/resume_generation/service.py
- apps/frontend/lib/api/runtime-events.ts
- apps/backend/tests/unit/test_agent_runtime_boundaries.py
