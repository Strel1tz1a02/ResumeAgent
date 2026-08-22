# Agent Runtime Unification Plan

1. 建立统一 Runtime 协议类型与契约测试。
2. 将 GraphRunner 改为 `RuntimeEvent | GraphOutcome` 流。
3. 统一 Experience 与 JD Import 的 Interaction interrupt/resume。
4. 提取 RunLifecycleService，删除 AiChatService 的领域分支。
5. 统一后端 SSE 和前端消费协议。
6. 提取 ContextAssembler 并迁移模型请求。
7. 将 Resume Generation 接入 Graph Driver 与通用 Run 状态。
8. 执行迁移、回归测试和架构边界审计。
