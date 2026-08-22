# LLM Integration Guide

> **基于 LangChain 的多供应商模型、工具调用、结构化输出与 JSON 兼容处理。**

## Multi-Provider Support

后端通过 LangChain 的统一 ChatModel 接口支持以下供应商：

| Provider | LangChain integration | Notes |
|----------|-----------------------|-------|
| **Ollama** | `langchain-ollama` | 本地模型 |
| **OpenAI** | `langchain-openai` | OpenAI 模型 |
| **OpenAI Compatible** | `langchain-openai` | llama.cpp、vLLM、LM Studio 等 |
| **Anthropic** | `langchain-anthropic` | Claude 模型 |
| **Google Gemini** | `langchain-google-genai` | Gemini 模型 |
| **OpenRouter** | `langchain-openrouter` | 聚合模型服务 |
| **DeepSeek** | `langchain-deepseek` | DeepSeek 模型 |
| **Groq** | `langchain-groq` | Groq 模型 |

`app/llm.py::get_chat_model()` 使用 `init_chat_model()` 创建并缓存供应商模型。普通结构化任务使用 LangChain 的 `ainvoke()` / `with_structured_output()`；Agent Graph 只能经 `AiChatRuntime.stream_model()` 和 `ContextAssembler` 组装请求，不直接调用供应商 SDK。

## API Key Handling

API Key 通过模型构造参数直接传入，不写入 `os.environ`，避免异步请求之间出现环境变量竞争。`openai_compatible` 和 `ollama` 不读取云端通用 Key；本地 OpenAI 兼容服务未配置 Key 时会使用无敏感信息的占位值满足客户端参数校验。

## Messages, Tools, and Structured Output

- 普通补全：`ChatPromptTemplate` 构建消息，ChatModel `ainvoke()` 执行。
- 流式工具调用：ChatModel `bind_tools()` 后使用 `astream()`；`AIMessageChunk` 直接聚合跨 chunk 工具参数，完整 `ToolCallChunk` 直接交给工具服务。
- 工具定义：模型只接收 LangChain `StructuredTool`，由其 `args_schema` 完成基础参数校验；工具本身不保存风险或审批状态。
- 工具生命周期：`ToolApprovalService` 根据完整调用决定审批路由，`ToolCallStore` 负责幂等固化，`ToolService` 编排准备、审批、执行和结果保存。
- Agent 控制面：业务拥有 Graph 拓扑，统一 Driver 只接收 `RuntimeEvent` / `GraphOutcome`；外部输入统一为 `InteractionRequest` / `ResolveInteractionCommand`。
- Agent 上下文：业务只声明指令、领域 Section、消息与待投递 Tool Result，`ContextAssembler` 负责来源标记、Memory、顺序和 Token 预算。
- 经历文本导入：`with_structured_output(ExperienceGlobalSave, include_raw=True)` 直接返回经 Pydantic 校验的结构。
- 业务 JSON 补全：`complete_json()` 保留现有花括号平衡提取、截断识别和内容质量重试。

## JSON Mode

`complete_json()` 根据供应商绑定原生 JSON 参数：OpenAI 兼容系列使用 `response_format`，Gemini 使用 JSON MIME type，Ollama 使用 `format="json"`。兼容服务拒绝 `response_format` 时，会回退到提示词约束的 JSON 输出；这只改变传输参数，不改变业务结果契约。

## Retry Logic

供应商传输重试由 LangChain 模型的 `max_retries` 处理。`complete_json()` 额外保留应用层内容质量重试，用于以下情况：

- 返回内容不是合法 JSON；
- JSON 疑似被输出长度截断；
- 兼容服务不支持原生 JSON 参数。

## Error Handling Pattern

模型函数在服务端记录详细错误，向客户端返回不包含密钥的通用消息：

```python
except Exception as exc:
    logger.error("LLM completion failed: %s", exc)
    raise ValueError(
        "LLM completion failed. Please check your API configuration."
    ) from exc
```

## Provider Configuration

用户可通过设置页 `/settings` 或 `/api/v1/config/*` 接口配置供应商、模型、地址、推理强度及加密存储的 API Key。旧配置中带供应商前缀的模型名会在读取时兼容处理。

## Health Checks and Timeouts

`POST /api/v1/config/llm-test` 会实际调用当前 ChatModel；`GET /api/v1/health` 仅做服务存活检查。基础超时如下，并会结合输出 token 数量和供应商系数调整：

| Operation | Base timeout |
|-----------|--------------|
| Health checks | 30s |
| Completions | 120s |
| JSON operations | 180s |

## Key Files

| File | Purpose |
|------|---------|
| `apps/backend/app/llm.py` | LangChain 模型工厂、补全、JSON 与健康检查 |
| `apps/backend/app/ai_chat/streaming/model.py` | LangChain 流式工具调用适配 |
| `apps/backend/app/ai_chat/streaming/sse.py` | Runtime Event 的唯一 SSE 编码与错误收敛 |
| `apps/backend/app/ai_chat/context/assembler.py` | Agent 模型上下文、Memory 与 Token 预算边界 |
| `apps/backend/app/ai_chat/protocol.py` | Interaction、恢复命令与 Graph Outcome 协议 |
| `apps/backend/app/ai_chat/graph/driver.py` | 唯一 LangGraph 执行、暂停和恢复边界 |
| `apps/backend/app/ai_chat/tools/operation.py` | LangChain Tool 注册与业务 Operation |
| `apps/backend/app/ai_chat/tools/approval/` | 风险判断与审批生命周期 |
| `apps/backend/app/ai_chat/tools/store.py` | Tool Call 幂等与固化边界 |
| `apps/backend/app/ai_chat/services/tool_service.py` | Tool 生命周期编排 |
| `apps/backend/app/ai_chat/services/run_lifecycle.py` | Run 状态、消息收尾和结果投递的唯一写边界 |
| `apps/backend/app/experience/services/experience_text_extractor.py` | Pydantic 结构化输出 |
| `apps/backend/app/prompts/templates.py` | Prompt 模板 |
| `apps/backend/app/config.py` | 供应商配置 |
