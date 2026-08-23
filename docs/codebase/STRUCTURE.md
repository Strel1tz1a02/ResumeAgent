# 代码库结构

## 1) 顶层地图

| 路径 | 作用 | 证据 |
|------|------|------|
| apps/backend/ | FastAPI 后端、领域模块、worker、迁移、测试 | apps/backend/pyproject.toml；apps/backend/app/main.py |
| apps/frontend/ | Next.js App Router 前端、组件、API 客户端、测试 | apps/frontend/package.json；apps/frontend/app/ |
| config/ | 仓库跟踪的前后端环境变量模板 | config/backend.env.example；config/frontend.env.example |
| docker/ | 容器启动辅助文件 | Dockerfile；docker-compose.yml |
| scripts/ | 仓库级检查与开发脚本 | scripts/check_locale_parity.py；.githooks/pre-push |
| docs/agent/ | 面向开发者的架构、工作流、LLM 文档 | docs/agent/README.md |
| docs/superpowers/specs/ | 功能设计与决策记录 | docs/superpowers/specs/2026-08-17-agent-runtime-unification-design.zh-CN.md |
| docs/superpowers/plans/ | 实施计划 | docs/superpowers/plans/2026-08-17-agent-runtime-unification.md |
| docs/codebase/ | 本次代码事实地图 | docs/codebase/.codebase-scan.txt |
| assets/ | README hero、首启截图与 GitHub Social Preview | README.md |
| .github/、.githooks/ | CI/发布、安全、贡献规范与本地质量门禁 | .github/workflows/ci.yml；.github/workflows/docker-publish.yml；.githooks/pre-push |

.worktrees/、缓存、日志和 node_modules 是工作环境/生成物，不应当作产品源码边界；扫描器把它们计入了规模统计。`.git-upstream-backup/` 是本机冻结的上游 Git 基线，不是产品源码，也不是可移植的远端依赖；其 main commit/tree 可读，但完整 `fsck` 会因 8 个无效 Codex 临时 ref 和 Windows 长路径返回失败，不能把它当作健康或唯一的上游档案。

## 2) 上游与 ResumeAgent 二开边界

`.git-upstream-backup/config` 指向 `srbhr/Resume-Matcher`，冻结在 `dd9b5c3`；当前仓库从独立 root `be8f03c` 演进，与该基线没有共同 ancestry。差异必须分两段理解：冻结上游到独立 root 只有 17 个文件（+804/-2589），说明初始化时基本复制了上游快照；独立 root 到当前 HEAD 才是主要二开，共 384 个文件（+52657/-16093）。冻结上游直接到 HEAD 的总比较为 395 个文件（A300/D30/M65、+53397/-18618），其中混入了初始化差异，不能全部算成后续二开。

在当前机器复现跨对象库比较，需要先让当前 Git 临时读取 backup objects：

~~~powershell
$env:GIT_ALTERNATE_OBJECT_DIRECTORIES=(Resolve-Path '.git-upstream-backup/objects').Path
git diff --stat dd9b5c3b7a341a62c3a86f7a84e8e30786e6153d HEAD
~~~

因此下面是架构级边界，不是可长期维护的普通 patch stack，也不能代表上游最新版本。

| 类别 | 主要路径 | 说明 |
|------|----------|------|
| 上游保留并继续维护 | app/routers/resumes.py、jobs.py、applications.py；app/services/improver.py、refiner.py；frontend builder/tailor/tracker/resume；app/pdf.py | 简历 CRUD、旧式一次性 LLM 优化、编辑器、模板、PDF、投递看板 |
| ResumeAgent 新增 Runtime | app/ai_chat/、app/background_jobs/ | 会话/Run/Interaction、LangGraph Driver、工具生命周期、Memory、SSE、Outbox |
| ResumeAgent 新增业务域 | app/experience/、app/jd_import/、app/resume_generation/ | 经历证据、JD Agent、混合检索与可追溯简历生成 |
| ResumeAgent 新增前端 | components/experiences、jd-imports、resume-generation；lib/api/runtime-events.ts；lib/queries/experiences | 三个新工作区、统一 SSE parser、Experience TanStack Query 缓存 |
| 当前工作树的发布/品牌层 | README.md、SETUP.md、package metadata、镜像名、zh/en locale、ci.yml | 相对 HEAD 仍有未提交项，不能与已提交的 Agent 核心混为一层 |

证据：`.git-upstream-backup/config`；`git log --reverse`；当前与 backup HEAD 的 tree comparison；上述源码目录。

发布层必须作为一个原子变更看待：HEAD 的后端/前端仍为 1.2.0、Next.js ^16.2.6，Compose 仍默认拉 `ghcr.io/srbhr/resume-matcher`，而 HEAD 的发布 workflow 写入 `ghcr.io/strel1tz1a02/resumeagent`；当前工作树才统一到 2.0.0、Next.js ^16.3.2 与 `ghcr.io/strel1tz1a02/resume-agent`，新 README/SETUP/CI 也仍未跟踪。只提交其中一部分会让 fresh clone、pull image 和发布产物指向不同版本。

## 3) 入口点

- 后端主入口：apps/backend/app/main.py；pyproject 脚本 app = app.main:main。
- 前端主入口：apps/frontend/app/；Next.js 脚本位于 apps/frontend/package.json。
- 后台进程：apps/backend/app/ai_chat/memory/worker.py 与 apps/backend/app/resume_generation/index_worker.py；Compose 以 ARQ worker 启动。
- 数据库初始化/迁移入口：apps/backend/app/database.py 延迟触发 apps/backend/app/db_engine.py；启动早期会执行幂等 SQL 迁移，没有 Alembic 目录。
- Agent composition root：apps/backend/app/main.py 注册 ExperienceAdapter 与 JDImportAdapter；Resume Generation 仍由独立 router/service 入口管理。

## 4) 模块边界

| 边界 | 应拥有 | 不应拥有 |
|------|--------|----------|
| app/ai_chat/protocol.py、graph/driver.py、run_state.py、streaming/、context/ | 通用命令、事件、Graph 执行端口、Run 状态规则、上下文组装 | Experience/JD/Resume 的节点、字段与业务校验 |
| app/ai_chat/services/ai_chat_service.py | 对话型 Runtime 编排、持久化后发事件、恢复与幂等协调 | 直接导入业务 graph 或识别业务 checkpoint key |
| app/ai_chat/adapters/ | 业务 Graph 插件契约与注册 | 具体业务拓扑 |
| app/experience/ | 经历修订、证据、审批 Graph 与领域工具 | Runtime 生命周期写入规则 |
| app/jd_import/ | JD 来源、抽取、澄清问题、落库 Graph | Runtime 事件协议 |
| app/resume_generation/ | 简历计划、检索、生成、产物状态 | 将 previewed/confirmed 混入通用 Run 状态 |
| app/background_jobs/ | 持久化 outbox、ARQ 发布与后台任务设置 | 领域数据规则或 HTTP 序列化 |
| app/ai_chat/repositories/、各领域 repository | 持久化与 CAS | HTTP/SSE 序列化 |
| apps/frontend/lib/api/ | transport、统一 Runtime SSE envelope 解析 | 页面展示状态 |
| apps/frontend/components/ | 领域事件投影与 UI 交互 | 重复实现 SSE envelope parser |

边界测试 apps/backend/tests/unit/test_agent_runtime_boundaries.py 禁止 Runtime 反向导入 Experience/JD/Resume，并用有限黑名单阻止已知业务事件名/checkpoint key 回流；它不是完整的静态依赖分析。当前还存在两个真实跨域耦合：JD/Resume 路由复用 experience.repositories.session，Experience 写入服务调用 resume_generation.indexing 生成索引 outbox。

## 5) Agent Runtime 深层地图

~~~text
HTTP router
  -> AiChatService
     -> AdapterRegistry -> business Adapter -> business Graph
     -> GraphRunner -> GraphDriver -> RuntimeEvent | GraphOutcome
     -> RunLifecycleService / RunStateMachine
     -> RunRepository + ToolCallStore + LangGraph checkpoint
  -> runtime_sse_response
  -> frontend parseRuntimeSse
~~~

- 首次输入：app/ai_chat/types/adapter_input.py。
- 外部解决等待：app/ai_chat/protocol.py 的 ResolveInteractionCommand。
- checkpoint 唤醒：同文件的 GraphResumeCommand，只含 run_id 与 interaction_id。
- 图执行：app/ai_chat/graph/driver.py 与 runner.py。
- 对话生命周期原子收口：app/ai_chat/services/run_lifecycle.py。
- 统一模型上下文：app/ai_chat/context/assembler.py。
- 统一事件与 SSE：app/ai_chat/streaming/events.py、sse.py。

## 6) 前端二开地图

- 当前有 13 个 `page.tsx` 路由：落地页、dashboard、experiences、jd-imports、resume-generation、resume-wizard、tailor、builder、resumes/[id]、tracker、settings，以及两个 print 页面。
- `(default)/layout.tsx` 只有 Status/Language/Preview/ErrorBoundary，没有全局导航或 QueryClient。导航入口硬编码在 Hero、Dashboard 卡片和 SwissGrid；新增页面不会自动出现在 UI。
- TanStack Query 只由 ExperienceLibraryPage 局部提供；JDImportWorkspace 与 ResumeGenerationWorkspace 使用组件本地 state。不能假设新页面天然处在 QueryClientProvider 内。
- Experience/JD 必须复用 `lib/api/runtime-events.ts` 的唯一 SSE parser；Resume Generation 当前是同步 REST。
- `app/print/**` 保持服务端打印边界；`next.config.ts` 明确禁止新建 `app/api/`，否则会遮蔽到 FastAPI 的 rewrite。
- i18n 当前只支持 `zh/en`，两个 JSON 必须同形；`scripts/check_locale_parity.py` 与前端 parity test 会验证结构。

概念上的证据驱动业务顺序是 Experience -> JD Import -> Resume Generation -> Resume Viewer/Builder/PDF，但真实导航以 `/dashboard` 为 hub：三个工作区各自返回 Dashboard；生成确认后才跳 `/resumes/[id]`，编辑使用 `/builder?id=<id>`，PDF 通过下载 API 而不是 `/builder/PDF` 路由。`/tailor` 仍是“主简历 + 原始 JD”的旧兼容链。新增需求应先判断属于新主链还是旧链，避免两边重复实现。

证据：apps/frontend/app/；apps/frontend/app/(default)/layout.tsx；apps/frontend/components/experiences/experience-library-page.tsx；apps/frontend/lib/api/runtime-events.ts；apps/frontend/next.config.ts；apps/frontend/i18n/config.ts。

## 7) 继续二开的落点速查

| 目标 | 最小改动面 | 必须验证的契约 |
|------|------------|----------------|
| 新增对话 Agent | 新建领域 state/graph/adapter/tools/router；main.py 注册 Adapter 和 router | Adapter 稳定名、状态 JSON 可序列化、start/wait/resume、SSE、幂等与 boundary test |
| 新增 Agent Tool | 领域 ToolOperation；Adapter 注册 RegisteredTool 和风险策略；必要时覆盖 resolve_interaction | prepare/execute 信任边界、审批或外部输入、重放、并发至多一次 |
| 新增普通 REST 领域 | models/schemas/repositories/services/routers；main.py；db_engine 模型注册与迁移 | HTTP 错误映射、事务、CAS/回滚、integration test |
| 新增前端领域页 | app/(default)/<route>/page.tsx；components/<domain>/；lib/api/<domain>.ts；messages/zh.json、en.json | loading/error/取消、API/SSE parser、组件测试、locale parity |
| 新增简历模板 | components/resume 组件/styles/index；template-settings 的 union/options/font；resume-component 分派；template-selector 标签/缩略图；print/resumes/[id] 的 parseTemplate allowlist；中英文案 | 编辑预览与 print/PDF 必须使用同一模板；漏改 print allowlist 会静默回退 swiss-single；另验证分页 |
| 新增后台索引/任务 | background_jobs outbox；ARQ worker；Compose service；领域事务内写 outbox | DB 与 outbox 同事务、任务幂等、失败重试、worker health |

最关键的顺序是：先确定领域不变量和持久化，再接 Adapter/Graph，最后接 SSE/UI；不要让 Tool 绕过领域 Service 直接写 Repository。

## 8) 命名与组织规则

- Python 文件、函数、变量使用 snake_case；类/协议使用 PascalCase；测试使用 test_*.py。
- TypeScript 组件文件多用 kebab-case，组件/类型用 PascalCase；测试使用 *.test.ts 或 *.test.tsx。
- 后端按领域 + 层混合组织：experience/adapters、graph、repositories、routers、services。
- 前端按 App Router 页面 + components/lib API 分层。
- TypeScript 使用 @/* -> 项目根的路径别名，见 apps/frontend/tsconfig.json。
- Python 使用 app.* 绝对导入；通用 Runtime 目前仍物理位于 app.ai_chat 下，这是命名/包边界债务。

## 9) Evidence

- docs/codebase/.codebase-scan.txt
- apps/backend/app/main.py
- apps/backend/app/ai_chat/adapters/base.py
- apps/backend/app/ai_chat/graph/runner.py
- apps/backend/app/ai_chat/graph/driver.py
- apps/backend/app/background_jobs/
- apps/backend/app/resume_generation/indexing.py
- apps/backend/tests/unit/test_agent_runtime_boundaries.py
- .git-upstream-backup/config
- apps/frontend/tsconfig.json
- apps/frontend/next.config.ts
- apps/frontend/i18n/config.ts
- docker-compose.yml
