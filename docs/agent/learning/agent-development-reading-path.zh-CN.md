# Resume Matcher Agent 开发代码阅读路径

> 目标：把“我设计了项目，但代码主要由 AI 生成”转化为“我能解释、调试、修改并为关键设计辩护”。
>
> 适用范围：当前仓库中的个人经历库、通用 `ai_chat` 运行时、`ExperienceAdapter`、LangGraph、Tool Call、Human-in-the-loop 与前端 SSE 接入。

## 1. 学完后的验收标准

完成这条路径后，应当能够：

1. 不看代码画出普通经历修改和 AI 经历修改两条完整调用链。
2. 解释核心函数的调用者、参数来源、返回值、副作用、异常和事务边界。
3. 解释 Conversation、Run、Message、ToolCall、Graph State、Checkpoint 的区别和关系。
4. 说明为什么本项目需要 Agent，而不只是一次普通 LLM 请求。
5. 解释 Tool Call 为什么不能绕过领域 Service 直接写 Repository。
6. 解释审批、幂等、revision guard、断流恢复分别防止什么问题。
7. 能独立增加一个小型 Tool 或 Adapter，并补充有效测试。
8. 能完成 3 分钟项目介绍、10 分钟架构讲解和 30 分钟代码深挖。

## 2. 阅读规则

### 2.1 事实来源优先级

按以下顺序判断项目“现在到底怎么运行”：

1. 当前生产代码；
2. 能实际运行且会在目标代码损坏时失败的测试；
3. 设计 SPEC，用来解释设计意图和权衡；
4. 旧文档、注释和命名。

部分 SPEC 的状态仍写着“待实施”，但对应代码已经存在。因此不能把规划状态当作当前实现证据。

### 2.2 不按目录通读，按用户行为纵向追踪

错误方式：依次读完 `routers/`、`services/`、`repositories/`。

正确方式：选择一个具体行为，从 UI 一直追到数据库和测试。例如：

```text
用户要求 AI 修改经历背景
→ 前端发消息
→ SSE 请求
→ 业务 Router
→ AiChatService
→ ExperienceAdapter
→ ExperienceGraph
→ 模型产生 Tool Call
→ Tool Handler 形成提案
→ interrupt + checkpoint
→ 用户批准
→ 领域 Service 写入
→ Graph 恢复并续答
→ SSE 更新页面
```

### 2.3 先学确定性业务，再学 Agent

Agent 最终仍要调用普通业务能力。必须先理解字段保存、Evidence 所有权、revision 和事务，再理解模型如何提出修改。否则容易把 Agent 误解成“会调用 LLM 的接口”。

### 2.4 每节课必须产生可检查的输出

每节课至少留下以下一种产出：

- 调用链；
- 数据结构卡；
- 函数卡；
- 状态转换表；
- 失败场景表；
- 一段口述录音或文字复述；
- 一个自己完成的小改动或测试。

## 3. 固定学习动作

每节课建议 60～120 分钟，按同一流程进行：

1. **预测（10 分钟）**：只看文件名和函数签名，预测职责与调用关系。
2. **追踪（30～50 分钟）**：沿一个具体输入追踪代码，不展开无关分支。
3. **验证（20～30 分钟）**：阅读对应测试，必要时运行单测或下断点。
4. **整理（15 分钟）**：填写函数卡、数据结构卡或状态表。
5. **脱稿复述（10 分钟）**：不看代码回答本节核心问题。

### 3.1 函数卡模板

```text
函数：
所在层：
一句话职责：
直接调用者：
继续调用：

参数及来源：
返回值及去向：
副作用：
事务边界：
可能异常：

为什么需要这些参数：
删除某个参数会破坏什么约束：
对应测试：
```

### 3.2 数据结构卡模板

```text
名称：
创建位置：
拥有者：
生命周期：
是否持久化：
字段来源：
被谁读取：
允许的状态变化：
与相似结构的区别：
```

### 3.3 每次都要问的失败问题

- 输入不存在或不合法会怎样？
- 请求重复发送会怎样？
- 两个请求并发修改会怎样？
- 模型返回非法内容会怎样？
- 数据已经写入，但网络响应丢失会怎样？
- 流式连接中断会怎样？
- 服务重启后哪些状态还能恢复？

## 4. 总体路线

路线共 16 课。前 4 课建立业务底座，第 5～13 课进入 Agent 主链，第 14～16 课完成可靠性、对比和面试迁移。

### 阶段 A：建立地图和确定性业务底座

#### 第 1 课：系统边界与 Agent 的装配位置

按顺序阅读：

1. `docs/superpowers/specs/2026-08-01-ai-chat-functional-boundaries-design.zh-CN.md` 的第 1、2、3 节；
2. `apps/backend/app/main.py` 中 `_register_business_adapters()`、`lifespan()` 和 Router 挂载；
3. `apps/backend/app/ai_chat/adapters/base.py`；
4. `apps/backend/app/ai_chat/adapters/registry.py`；
5. `apps/backend/app/ai_chat/container.py`。

核心问题：

- 通用聊天层、业务 AI 接入层、领域层分别负责什么？
- 为什么 `ai_chat` 不能直接导入 `ExperienceService`？
- 为什么 Adapter 是长期复用且无请求状态的？
- 为什么注册表保存稳定名称，而不是到处直接实例化 Adapter？

产出：三层边界图 + `BaseAdapter` 四个抽象方法的职责表。

#### 第 2 课：Experience 聚合与 API 数据契约

按顺序阅读：

1. `apps/backend/app/models.py` 中 Experience、Evidence、ExperienceEvidence、FieldState、Revision 相关 ORM；
2. `apps/backend/app/experience/schemas/experiences.py`；
3. `apps/backend/app/experience/schemas/evidence_items.py`；
4. `apps/backend/app/experience/services/experience_fields.py`；
5. `apps/backend/app/experience/repositories/experience_revision_repository.py`。

核心问题：ORM、Pydantic Schema、普通字典分别解决什么问题？`ExperienceDetail` 为什么不直接把 ORM 返回给前端？

产出：Experience 聚合数据字典，标注主键、关系表、派生状态和三类 revision。

#### 第 3 课：普通经历修改的完整调用链

先追踪较短的“单个保存单元 PATCH”调用链：

1. `apps/frontend/components/experiences/experience-editor.tsx` 中 `payloadFor()`、`saveKeys()` 和 `saveUnit()`；
2. `apps/frontend/lib/queries/experiences/mutations.ts::usePatchExperienceMutation()`；
3. `apps/frontend/lib/api/experiences.ts::patchExperience()`；
4. `apps/backend/app/experience/routers/experiences.py::patch_experience()`；
5. `apps/backend/app/experience/services/experience_service.py::patch()`；
6. `apps/backend/app/experience/services/experience_field_service.py::claim_experience_units()`；
7. `apps/backend/app/experience/repositories/experience_revision_repository.py::claim()`；
8. `apps/backend/app/experience/repositories/experience_repository.py::update_fields()`；
9. `apps/backend/tests/unit/test_experience_repositories.py::test_revision_claim_is_database_compare_and_swap()`。

再对照当前页面“全局保存”的真实生产调用链：

1. `apps/frontend/components/experiences/experience-library-page.tsx::saveAll()`；
2. `apps/frontend/lib/queries/experiences/mutations.ts::useSaveExperienceMutation()`；
3. `apps/frontend/lib/api/experiences.ts::saveExperience()`；
4. `apps/backend/app/experience/routers/experiences.py::save_experience()`；
5. `apps/backend/app/experience/services/experience_global_save_service.py::save()`。

核心问题：一次 PATCH 在每一层的数据形态是什么？谁负责 HTTP、业务规则、事务和 SQL？为什么单字段保存适合 PATCH，而主字段与全部 Evidence 的全局保存需要单独的原子事务？

产出：普通 PATCH 的端到端时序图 + 全局保存对照图 + `patch()` 函数卡。

#### 第 4 课：字段 revision、Evidence 与领域不变量

按顺序阅读：

1. `apps/backend/app/models.py` 中 `ExperienceEvidence` 与 `ExperienceRevision`；
2. `apps/backend/app/experience/repositories/experience_revision_repository.py`；
3. `apps/backend/app/experience/services/experience_field_service.py`；
4. `apps/backend/app/experience/services/evidence_service.py`；
5. `apps/backend/app/experience/services/experience_global_save_service.py`；
6. `apps/backend/app/experience/services/experience_ai_mutation_service.py`；
7. `apps/backend/tests/unit/test_experience_repositories.py` 中关系与 CAS 测试；
8. `apps/backend/tests/unit/test_experience_ai_chat.py` 前半部分。

核心问题：展示状态与 revision 为什么分表？保存单元、单条 Evidence、Evidence 集合三种 revision 分别保护什么？生成开始时的 revision 与规范化值为什么要在审批时再次比较？

产出：至少 6 条领域不变量及其保护位置。

### 阶段 B：通用 Agent 运行时

#### 第 5 课：通用类型、Adapter 契约与依赖装配

按顺序阅读：

1. `apps/backend/app/ai_chat/types.py`；
2. `apps/backend/app/ai_chat/graph/state.py`；
3. `apps/backend/app/ai_chat/tools/results.py`；
4. `apps/backend/app/ai_chat/adapters/base.py`；
5. `apps/backend/app/ai_chat/graph/runtime.py`；
6. `apps/backend/app/ai_chat/container.py`；
7. `apps/backend/app/main.py`。

核心问题：Adapter 为什么要把统一 `AdapterInput` 转换成扩展 `BaseState` 的完整业务 State？哪些值必须 JSON 可序列化？

产出：三种输入/状态结构的对照表。

#### 第 6 课：Conversation、Run、Message、ToolCall 持久化模型

按顺序阅读：

1. `apps/backend/app/ai_chat/models/models.py`；
2. `apps/backend/app/ai_chat/repositories/conversation_repository.py`；
3. `apps/backend/app/ai_chat/repositories/run_repository.py`；
4. `apps/backend/app/ai_chat/repositories/message_repository.py`；
5. `apps/backend/app/ai_chat/repositories/tool_call_repository.py`。

核心问题：Conversation 和 Run 为什么拆开？为什么一个会话只能有一个 current run？数据库唯一约束解决了什么竞争条件？

产出：四张数据结构卡 + 状态转换表。

#### 第 7 课：AiChatService 的创建会话与普通消息轮次

不要一次通读整个文件，只追两条路径：

1. `AiChatService.create_conversation()`；
2. `AiChatService.stream_message()`；
3. `_start_run()`、`_build_input()`、`_execute()` 等被直接调用的内部函数；
4. 对应 Repository 方法。

文件：`apps/backend/app/ai_chat/services/service.py`。

核心问题：用户消息为什么先落库再调用模型？assistant 消息为什么先以 `generating` 创建？失败时保留什么？

产出：普通文本轮次的事务时间线。

#### 第 8 课：模型流、Tool 参数聚合与内部事件

按顺序阅读：

1. `apps/backend/app/ai_chat/streaming/model.py`；
2. `apps/backend/app/ai_chat/tools/buffer.py`；
3. `apps/backend/app/ai_chat/streaming/compatibility/dsml.py`；
4. `apps/backend/app/ai_chat/streaming/events.py`；
5. `apps/backend/app/ai_chat/graph/runtime.py`。

核心问题：模型流中的文本 delta 和 Tool Call delta 如何变成稳定内部事件？为什么必须等参数聚合完整后才能校验？

产出：Provider 流事件到内部事件的转换表。

### 阶段 C：ExperienceAdapter 与 LangGraph

#### 第 9 课：业务 API、SSE 与前端会话状态

按顺序阅读：

1. `apps/frontend/lib/api/experience-ai-chat.ts`；
2. `apps/frontend/components/experiences/ai-chat/use-experience-ai-chat.tsx`；
3. `apps/frontend/components/experiences/ai-chat/experience-chat-panel.tsx`；
4. `apps/backend/app/experience/schemas/ai_chat.py`；
5. `apps/backend/app/experience/routers/ai_chat.py`。

核心问题：为什么用 POST + 流式响应而不是浏览器原生 `EventSource`？业务 Router 为什么要把内部事件映射成业务事件？

产出：前端状态、HTTP 请求和 SSE 事件对照表。

#### 第 10 课：Adapter 如何加载业务上下文

按顺序阅读：

1. `apps/backend/app/experience/adapters/adapter.py::validate_binding()`；
2. `ExperienceAdapter.parse_input()`；
3. `apps/backend/app/experience/graph/context.py`；
4. `apps/backend/app/experience/graph/prompts.py`；
5. `apps/backend/app/experience/graph/state.py`。

核心问题：`subject` 和 `target` 为什么对通用层是不透明的？为什么业务上下文在运行开始前加载？Prompt、消息历史和业务快照如何组合？

产出：用一个 `background` 会话实例填写完整的字段来源表。

#### 第 11 课：Graph 的普通文本分支

阅读 `apps/backend/app/experience/graph/builder.py`，本节只追：

```text
START
→ prepare_turn
→ agent_stream
→ route_model_output
→ persist_answer
→ END
```

再阅读 `apps/backend/app/ai_chat/graph/runner.py::stream()` 和 `_normalize()`。

核心问题：Graph 节点为什么返回局部 State？条件边如何选择分支？Graph 事件和 Service 持久化为什么分属不同位置？

产出：普通文本 Graph 图 + 每个节点的输入/输出字段表。

#### 第 12 课：Tool Call、提案与领域修改

按顺序阅读：

1. `apps/backend/app/experience/tools/content_change.py`；
2. `apps/backend/app/ai_chat/tools/handler.py`；
3. `apps/backend/app/ai_chat/tools/lifecycle.py`；
4. `builder.py` 中 `validate_tool_call`、`route_tool`、`persist_proposal`；
5. `apps/backend/app/experience/services/experience_ai_mutation_service.py`。

核心问题：Handler 的 `invoke()` 和 `resolve()` 为什么分开？`proposal_payload` 与 `guard_payload` 为什么不能合并成一个前端对象？

产出：Tool Call 生命周期图 + `content_change` 参数卡。

#### 第 13 课：interrupt、checkpoint、审批与恢复

按顺序阅读：

1. `builder.py` 中 `await_approval` 和 `finalize_tool_result`；
2. `apps/backend/app/ai_chat/graph/runner.py`；
3. `apps/backend/app/ai_chat/checkpoint/factory.py`；
4. `AiChatService.resolve_proposal()`；
5. `test_real_graph_interrupt_approve_and_deferred_tool_result()`。

核心问题：`interrupt()` 后为什么普通 Python 调用栈不需要一直存在？`Command(resume=...)` 如何找到原来的状态？为什么审批收尾不需要再次调用模型？

产出：暂停前、暂停中、恢复后的数据库状态与 checkpoint 状态表。

### 阶段 D：可靠性、对比和面试迁移

#### 第 14 课：幂等、并发、断流与失败恢复

从失败场景反向查代码：

- 重复消息：`client_message_id`；
- 重复审批：`client_resolution_id`；
- 同会话并发运行：current run 唯一约束；
- 用户先断流：`CancelledError` 与 run/message 状态；
- 提案落库但尚未进入 interrupt：`ensure_interrupted()`；
- Tool 已应用但模型续答失败：pending Tool Result；
- 生成期间字段变化：revision guard。

主要文件：`service.py`、`runner.py`、四个 Repository、`experience_ai_mutation_service.py` 及相关测试。

产出：至少 10 个“故障—保护机制—剩余风险”三列表。

#### 第 15 课：测试、调试与 Agent Eval

按顺序阅读：

1. `apps/backend/tests/unit/test_experience_ai_chat.py`；
2. `apps/backend/tests/unit/test_ai_chat_model.py`；
3. `apps/frontend/tests/experience-ai-chat.test.ts`；
4. `apps/backend/tests/evals/README.md`；
5. `apps/backend/tests/evals/scorers.py`。

核心问题：哪些测试验证确定性机制？哪些测试验证 LLM 质量？Mock 到哪一层才不会变成“测试戏剧”？

产出：测试金字塔 + 为一个失败场景新增测试草案。

#### 第 16 课：对比旧 LLM 流程、独立扩展与面试演练

对比阅读：

1. `apps/backend/app/services/improver.py`；
2. `apps/backend/app/services/refiner.py`；
3. `apps/backend/app/ai_chat/`；
4. `apps/backend/app/experience/`。

回答：一次 LLM Pipeline、Workflow 和 Agent 的边界分别是什么？哪些决策由代码决定，哪些由模型决定？

最终练习：设计一个最小的新 Tool 或新 Adapter，只写设计和测试计划，确认边界后再编码。

产出：

- 3 分钟项目介绍；
- 10 分钟 Agent 架构讲解；
- 20 个面试追问及回答；
- 一个独立扩展设计。

## 5. 主调用链索引

### 5.1 创建会话

```text
createExperienceConversation
→ POST /experience-ai-chat/conversations
→ experience.routers.ai_chat.create_conversation
→ AiChatService.create_conversation
→ ExperienceAdapter.validate_binding
→ ConversationRepository.create
→ ExperienceFieldService.snapshot
```

### 5.2 发送消息并返回普通文本

```text
streamExperienceMessage
→ router.stream_message
→ AiChatService.stream_message
→ 创建 user message / run / generating assistant message
→ GraphRunner.stream
→ ExperienceAdapter.parse_input
→ ExperienceGraph.agent_stream
→ AiChatRuntime.stream_model
→ AiChatModel.stream
→ assistant.delta
→ AiChatService 持久化完整 assistant message
→ assistant.completed
```

### 5.3 Tool 提案与审批恢复

```text
模型输出 content_change
→ ToolCallBuffer 聚合参数
→ graph.validate_tool_call
→ AiChatRuntime.receive_tool_call
→ ToolLifecycle.receive
→ ContentChangeHandler.invoke
→ ExperienceAiMutationService.prepare_*
→ 持久化 awaiting_approval ToolCall
→ graph.await_approval / interrupt
→ run=suspended
→ 前端展示 proposal
→ resolveExperienceProposal
→ AiChatService.resolve_proposal
→ ContentChangeHandler.resolve
→ ExperienceAiMutationService.apply_*
→ Command(resume=approval)
→ finalize_tool_result
→ run=completed，Tool Result 保持 pending
```

## 6. 面试复述框架

回答任何设计题时，按下面五步组织：

1. **问题**：当时要解决什么真实风险或用户需求？
2. **方案**：采用了什么结构和调用链？
3. **代码**：对应哪些类、函数和数据结构？
4. **权衡**：复杂度、性能、一致性或扩展性付出了什么代价？
5. **改进**：如果有更多时间或规模变化，会如何演进？

例如回答“为什么使用 Adapter”：

```text
问题：通用会话机制不应该认识 Experience 字段语义。
方案：以 BaseAdapter 作为通用运行时与业务 AI 接入层的稳定协议。
代码：validate_binding、parse_input、build_graph、get_tool_handlers。
权衡：增加了一层抽象和注册机制，但换来业务隔离和可扩展性。
改进：新增 ResumeOptimizationAdapter 验证协议是否真正通用。
```

## 7. 学习进度记录

| 课次 | 状态 | 能否脱稿讲解 | 产出位置 | 尚未理解的问题 |
|---|---|---|---|---|
| 1 | 进行中 | 否 | 待填写 | 待填写 |
| 2 | 未开始 | 否 |  |  |
| 3 | 未开始 | 否 |  |  |
| 4 | 未开始 | 否 |  |  |
| 5 | 未开始 | 否 |  |  |
| 6 | 未开始 | 否 |  |  |
| 7 | 未开始 | 否 |  |  |
| 8 | 未开始 | 否 |  |  |
| 9 | 未开始 | 否 |  |  |
| 10 | 未开始 | 否 |  |  |
| 11 | 未开始 | 否 |  |  |
| 12 | 未开始 | 否 |  |  |
| 13 | 未开始 | 否 |  |  |
| 14 | 未开始 | 否 |  |  |
| 15 | 未开始 | 否 |  |  |
| 16 | 未开始 | 否 |  |  |

只有完成产出并通过脱稿问答，才能把一课标记为完成。
