# 编码约定

## 1) 命名规则

| 项目 | 规则 | 示例 | 证据 |
|------|------|------|------|
| Python 文件 | snake_case.py | run_lifecycle.py | apps/backend/app/ai_chat/services/run_lifecycle.py |
| Python 函数/方法 | snake_case；异步 I/O 使用 async def | resolve_interaction | apps/backend/app/ai_chat/services/ai_chat_service.py |
| Python 类型/协议 | PascalCase；数据边界常用 dataclass/Pydantic | GraphOutcome、GraphDriver | apps/backend/app/ai_chat/protocol.py；apps/backend/app/ai_chat/graph/driver.py |
| TypeScript 文件 | UI/API 多为 kebab-case | runtime-events.ts | apps/frontend/lib/api/runtime-events.ts |
| React 组件/TS 类型 | PascalCase；hooks 以 use 开头 | RuntimeEvent、useExperienceAiChat | apps/frontend/lib/api/runtime-events.ts；apps/frontend/components/experiences/ai-chat/use-experience-ai-chat.tsx |
| 常量/env | UPPER_SNAKE_CASE | REQUEST_TIMEOUT_SECONDS | apps/backend/.env.example |
| 事件名 | lower-case dotted vocabulary | run.started、interaction.requested | apps/backend/app/ai_chat/streaming/events.py |

## 2) 格式化与 lint

- 前端 formatter：Prettier，配置 apps/frontend/.prettierrc；命令 npm run format。
- 前端 linter：ESLint 9，组合 Next core-web-vitals、TypeScript 与 Prettier；配置 apps/frontend/eslint.config.mjs；命令 npm run lint。
- TypeScript：strict=true、noEmit=true，见 apps/frontend/tsconfig.json。
- 后端：存在 Ruff 开发缓存，但 apps/backend/pyproject.toml 未声明明确 Ruff 规则/脚本；正式 lint 命令为 [TODO]。
- Git diff 本次检查：git diff --check 无 whitespace error；仅报告 Windows 工作树 LF/CRLF 转换警告。

## 3) Import 与模块约定

- 后端以 app.* 绝对导入为主；Runtime 依赖方向由 tests/unit/test_agent_runtime_boundaries.py 强制。
- 业务可以依赖 Runtime 协议，Runtime 不得依赖 experience、jd_import、resume_generation。
- 前端跨目录使用 @/ 别名；同目录可使用相对 import，见 apps/frontend/tsconfig.json 与 apps/frontend/lib/api/*.ts。
- 公共 Runtime 类型从 ai_chat/graph/__init__.py、streaming/__init__.py 暴露；完整 barrel/export 稳定性没有单独规范，[TODO]。

## 4) 协议与状态约定

- 事件采用 RuntimeEvent(type, run_id, sequence, payload)；payload 为对象。
- Graph 只向 Driver 产生 RuntimeEvent 或 GraphOutcome；自定义 dict 事件被拒绝。
- 外部恢复输入先经 Adapter 校验并持久化，再把 identity-only GraphResumeCommand 送给 checkpoint。
- Run 状态转换必须经 RunStateMachine/RunStore；业务产物状态独立保存。
- checkpoint 只保存执行位置与临时 Graph state，不是 Tool Call、领域对象或最终产物的事实源。
- ContextAssembler 固定消息顺序，并标注 domain/history 为不可信数据边界。

证据：apps/backend/app/ai_chat/protocol.py、graph/driver.py、run_state.py、context/assembler.py。

## 5) 错误与日志

- HTTP 边界将稳定领域错误映射为 HTTP 状态；SSE 边界将未处理异常转为带稳定 code 的 run.failed，见 apps/backend/app/ai_chat/errors.py 与 streaming/sse.py。
- 持久化状态先提交，再发送 suspended/completed/interaction 事件，避免前端领先于事实源，见 services/ai_chat_service.py 与 services/run_lifecycle.py。
- 外部调用使用模块 logger；日志上下文的一致字段约束为 [TODO]。
- API key 不写入普通 config.json，使用 Fernet 加密后存入 SQLite；异常日志不得打印明文，见 apps/backend/app/config.py、crypto.py。
- LLM 内容可由 LOG_LLM 控制；生产环境的敏感内容脱敏策略未形成集中规范，[TODO]。

## 6) 测试约定

- 后端测试在 apps/backend/tests/unit 与 integration，文件名 test_*.py；pytest asyncio_mode=auto、strict markers。
- LLM/eval 测试默认排除，必须显式 opt-in，见 apps/backend/pyproject.toml。
- 前端测试集中在 apps/frontend/tests，使用 *.test.ts(x)，全局 setup 位于 vitest.setup.ts。
- 真实恢复测试优先使用 SQLite checkpoint + 真 Graph；服务编排可用 fake driver/runner 隔离。
- coverage 阈值与当前覆盖率均为 [TODO]。

## 7) 已知约定偏差

- 设计文档描述 ResolveInteractionCommand 带 type=resolve_interaction；当前 Python dataclass 没有 type 字段，靠类身份区分。
- Run 设计图把 failed/cancelled 画成终态；当前 RunStateMachine 允许它们回到 running/completed。
- 前端 RuntimeEvent payload 仍是 Record<string, unknown>，业务端自行 cast，尚非按 type 区分的联合类型。

## 8) Evidence

- apps/backend/pyproject.toml
- apps/frontend/eslint.config.mjs
- apps/frontend/.prettierrc
- apps/frontend/tsconfig.json
- apps/backend/app/ai_chat/protocol.py
- apps/backend/app/ai_chat/streaming/sse.py
- apps/backend/tests/unit/test_agent_runtime_boundaries.py
- .githooks/pre-push
