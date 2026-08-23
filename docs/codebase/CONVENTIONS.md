# 编码约定

## 1) 命名规则

| 项目 | 规则 | 示例 | 证据 |
|------|------|------|------|
| Python 文件 | snake_case.py | run_lifecycle.py | apps/backend/app/ai_chat/services/run_lifecycle.py |
| Python 函数/方法 | snake_case；异步 I/O 使用 async def | resolve_interaction | apps/backend/app/ai_chat/services/ai_chat_service.py |
| Python 类型/协议 | PascalCase；数据边界常用 dataclass/Pydantic | GraphOutcome、GraphDriver | apps/backend/app/ai_chat/protocol.py；apps/backend/app/ai_chat/graph/driver.py |
| TypeScript 文件 | UI/API 多为 kebab-case | runtime-events.ts | apps/frontend/lib/api/runtime-events.ts |
| React 组件/TS 类型 | PascalCase；hooks 以 use 开头 | RuntimeEvent、useExperienceAiChat | apps/frontend/lib/api/runtime-events.ts；apps/frontend/components/experiences/ai-chat/use-experience-ai-chat.tsx |
| 常量/env | UPPER_SNAKE_CASE | REQUEST_TIMEOUT_SECONDS | config/backend.env.example |
| 事件名 | lower-case dotted vocabulary | run.started、interaction.requested | apps/backend/app/ai_chat/streaming/events.py |

## 2) 格式化与 lint

- 前端 formatter：Prettier，配置 apps/frontend/.prettierrc；命令 npm run format。
- 前端 linter：ESLint 9，组合 Next core-web-vitals、TypeScript 与 Prettier；配置 apps/frontend/eslint.config.mjs；命令 npm run lint。
- TypeScript：strict=true、noEmit=true，见 apps/frontend/tsconfig.json。
- 后端：`pyproject.toml` 未声明 Ruff/Black/Mypy 规则或脚本；当前没有仓库级正式 Python lint/format 命令。缓存目录不是规范来源。
- Git diff 本次检查：git diff --check 无 whitespace error；仅报告 Windows 工作树 LF/CRLF 转换警告。

## 3) Import 与模块约定

- 后端以 app.* 绝对导入为主；Runtime 依赖方向由 tests/unit/test_agent_runtime_boundaries.py 强制。
- 业务可以依赖 Runtime 协议，Runtime 不得依赖 experience、jd_import、resume_generation。
- 前端跨目录使用 @/ 别名；同目录可使用相对 import，见 apps/frontend/tsconfig.json 与 apps/frontend/lib/api/*.ts。
- 公共 Runtime 类型通过 `ai_chat/types/__init__.py`、`graph/__init__.py`、`streaming/__init__.py` 选择性导出；业务代码仍可从具体模块导入，仓库没有强制 barrel-only 规则。

## 4) 前端项目约束

- UI 改动必须遵守 Swiss design pack：方角、硬边/硬阴影、限定色板、serif 标题/sans 正文/mono 元信息；但这些主要是文档约束，ESLint 不会自动阻止渐变、圆角或装饰图标漂移。
- 应用页放在 `app/(default)`；Chromium 打印页保留在 `app/print` 并保持 server boundary。
- 不得创建 `app/api/`；Next 文件系统路由会先于 `next.config.ts` rewrite 命中，遮蔽 FastAPI `/api/v1`。
- 新 Runtime client 必须复用 `apiStream + parseRuntimeSse`，业务差异放在 `payload.kind` projector/hook，禁止复制 TextDecoder/getReader parser。
- QueryClient 不是全局能力；当前只在 ExperienceLibraryPage 内提供。新领域需要明确选择局部 Provider 或提升到全局。
- `messages/en.json` 是前端消息结构基准，`zh.json` 必须同形；新增文案要同时修改两份并运行 locale parity。

证据：docs/portable/swiss-design-system/README.md；apps/frontend/next.config.ts；apps/frontend/lib/api/runtime-events.ts；apps/frontend/lib/queries/experiences/provider.tsx；scripts/check_locale_parity.py。

## 5) 协议与状态约定

- 事件采用 RuntimeEvent(type, run_id, sequence, payload)；payload 为对象。
- Graph 只向 Driver 产生 RuntimeEvent 或 GraphOutcome；自定义 dict 事件被拒绝。
- 外部恢复输入先经 Adapter 校验并持久化，再把 identity-only GraphResumeCommand 送给 checkpoint。
- Run 状态转换必须经 RunStateMachine/RunStore；业务产物状态独立保存。
- checkpoint 只保存执行位置与临时 Graph state，不是 Tool Call、领域对象或最终产物的事实源。
- ContextAssembler 固定消息顺序，并标注 domain/history 为不可信数据边界。

证据：apps/backend/app/ai_chat/protocol.py、graph/driver.py、run_state.py、context/assembler.py。

## 6) 错误与日志

- HTTP 边界将稳定领域错误映射为 HTTP 状态；SSE 边界将未处理异常转为带稳定 code 的 run.failed，见 apps/backend/app/ai_chat/errors.py 与 streaming/sse.py。
- 持久化状态先提交，再发送 suspended/completed/interaction 事件，避免前端领先于事实源，见 services/ai_chat_service.py 与 services/run_lifecycle.py。
- 外部调用使用模块级 `logging.getLogger(__name__)`；日志文件格式统一为时间、级别、logger 名和消息，但尚无统一结构化字段 schema。
- API key 不写入普通 config.json，使用 Fernet 加密后存入 SQLite；异常日志不得打印明文，见 apps/backend/app/config.py、crypto.py。
- LLM/provider 日志级别由 `LOG_LLM` 控制；API key 错误有专项脱敏测试，但用户履历/JD 的集中脱敏策略尚未形成。

## 7) 测试约定

- 后端测试在 apps/backend/tests/unit 与 integration，文件名 test_*.py；pytest asyncio_mode=auto、strict markers。
- LLM/eval 测试默认排除，必须显式 opt-in，见 apps/backend/pyproject.toml。
- 前端测试集中在 apps/frontend/tests，使用 *.test.ts(x)，全局 setup 位于 vitest.setup.ts。
- 真实恢复测试优先使用 SQLite checkpoint + 真 Graph；服务编排可用 fake driver/runner 隔离。
- manifest 与 CI 均未配置 coverage 工具或阈值；不能从测试数量推断覆盖率。

## 8) 已知约定偏差

- 设计文档描述 ResolveInteractionCommand 带 type=resolve_interaction；当前 Python dataclass 没有 type 字段，靠类身份区分。
- Run 设计图把 failed/cancelled 画成终态；当前 RunStateMachine 允许它们回到 running/completed。
- 前端 RuntimeEvent payload 仍是 Record<string, unknown>，业务端自行 cast，尚非按 type 区分的联合类型。
- 根布局 `<html lang>` 仍固定为 `en-US`，但当前默认 locale 是 `zh`；可访问性/SEO 语言声明与运行时语言不一致。
- Swiss 规则尚未自动化：源码仍存在 CSS grid 渐变、圆角和装饰性图标，不能把现有任意组件都当作规范样板。

## 9) Evidence

- apps/backend/pyproject.toml
- apps/frontend/eslint.config.mjs
- apps/frontend/.prettierrc
- apps/frontend/tsconfig.json
- apps/frontend/next.config.ts
- apps/frontend/i18n/config.ts
- scripts/check_locale_parity.py
- apps/backend/app/ai_chat/protocol.py
- apps/backend/app/ai_chat/types/__init__.py
- apps/backend/app/ai_chat/streaming/sse.py
- apps/backend/tests/unit/test_agent_runtime_boundaries.py
- .githooks/pre-push
