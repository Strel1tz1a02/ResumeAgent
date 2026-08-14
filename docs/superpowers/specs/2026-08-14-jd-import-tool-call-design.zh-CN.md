# JD Import Tool Call 持久化设计

## 1. 目标

JD Import Graph 的两类持久化交互统一使用现有 `AiChatToolCall`：

- `ask_jd_questions` 保存问题批次与用户整批回答。
- `persist_jd` 保存每个候选 JD 的固化过程与 `information_id`。

完成后删除 `AiChatRun.result_json`。`AiChatRun` 只表达运行生命周期，不承载 JD 专属状态。

## 2. 总体流程

```text
extract -> assess -> plan_questions
                         | 模型不调用工具
                         +-----------------------> persist
                         |
                         | 模型调用 ask_jd_questions
                         v
                  validate_tool_call
                         v
                   awaiting_input
                         v
                      interrupt
                         v
                    用户整批回答
                         v
                 Tool Call resolved
                         v
                 merge_answers -> extract

persist -> 每个候选 JD 创建一个 persist_jd Tool Call -> 自动执行 -> resolved
```

## 3. `ask_jd_questions`

### 3.1 调用权

该工具只在专门的 `plan_questions` 节点暴露给模型。是否调用、询问哪些字段以及如何组织文案，由模型根据完整现状决定。

模型收到的现状包括：

- 当前所有候选 JD 及已填写字段；
- 缺失字段；
- 未解决冲突；
- URL 访问结果；
- 证据校验错误；
- 历史问题和回答；
- 当前问题轮次。

模型有两种合法输出：

- 调用一次 `ask_jd_questions`；
- 不调用工具，直接进入 `persist`。

### 3.2 服务端约束

服务端不强制问题覆盖全部缺失项，只校验：

- 一次最多调用一个提问工具；
- 每批最多 12 个问题；
- 总共最多 3 轮；
- 同一稳定问题键不能重复询问；
- 目标 JD、字段和选项格式合法；
- `batch_id`、`question_id` 和持久化身份由服务端生成。

模型选择不问时允许继续落库；存在必填缺失或必填冲突的 JD 最终状态必须是 `incomplete`。

### 3.3 状态与结果

`ask_jd_questions` 使用新增状态：

```text
received -> validated -> awaiting_input -> resolved
```

- `arguments` 保存服务端规范化后的完整问题批次。
- `tool_result` 保存用户整批回答。
- `client_resolution_id` 保证回答请求幂等。
- 相同 resolution ID 和相同回答重放已有结果。
- 不同 resolution ID 或不同回答视为冲突。

提问结果对模型可见：

```python
model_visible = True
deliver_result_to_model = True
```

Graph 恢复后，`merge_answers` 从已 resolved 的 Tool Call 读取回答，将其转换成 `user_answer` 来源，再回到 `extract`。下一次提取模型因此能看到用户补充内容。Tool Result 只投递一次，避免重复上下文。

## 4. `persist_jd`

`persist_jd` 是系统内部工具，不由模型生成或选择：

```python
model_visible = False
deliver_result_to_model = False
security = ToolSecurity.LOW
```

每个候选 JD 对应一个 Tool Call：

```text
received -> validated -> executing -> resolved
```

- `arguments` 保存经过证据校验的候选 JD。
- `tool_result` 保存 `{"information_id": <id>}`。
- Handler 在 `ToolCallService` 注入的同一个数据库事务中创建 `JDInformation`、`JDRequirement` 并写入 Tool Result。
- Tool Call 已 resolved 时直接重放原 `information_id`，不重复创建 JD。
- 单个候选 JD 失败只产生该 JD 的稳定错误，不阻止其他候选 JD 固化。

## 5. 稳定身份

Tool Call 由服务端分配稳定业务身份，不信任模型返回的调用索引或 provider ID：

```text
ask_jd_questions: jd-import:questions:{round}
persist_jd:       jd-import:persist:{jd_key}
```

`ToolCallService` 按 `run_id + provider_tool_call_id` 查找：

- 不存在：分配当前 Run 的下一个 `tool_call_index` 并创建；
- 已存在且工具名、参数相同：作为重放；
- 已存在但参数不同：抛出幂等冲突。

## 6. 通用 Tool Call 扩展

`ToolHandler` 增加能力声明：

```python
model_visible: bool = True
deliver_result_to_model: bool = True
```

含义分别是：

- 是否向模型暴露工具定义；
- resolved 结果是否进入模型上下文。

通用状态机增加 `awaiting_input`。新增原子操作：

- 将 `validated` Tool Call 转为 `awaiting_input`；
- 使用 `client_resolution_id` 领取并写入外部输入；
- 从 `awaiting_input` 转为 `resolved`；
- 查询某个 Run 当前唯一的 `awaiting_input` Tool Call。

现有审批状态 `awaiting_approval` 保持原语义，不用于用户填写问题。

## 7. Graph 与 API 恢复

Graph 中断前先将提问 Tool Call 持久化为 `awaiting_input`，再发出 `jd.questions.requested` 并调用 `interrupt()`。中断节点在 `interrupt()` 前不得执行其他非幂等副作用。

现有 API 保持不变：

```text
POST /api/v1/jd-imports/conversations/{id}/question-batches/{batch_id}/resolve
```

恢复步骤：

1. 查找当前 suspended Run 的唯一 `awaiting_input` Tool Call；
2. 校验 `batch_id` 和整批回答；
3. 原子写入回答并将 Tool Call 标为 `resolved`；
4. 使用 `tool_call_id` 恢复 Graph；
5. Graph 从 Tool Result 读取回答并回到 `extract`。

服务在步骤 3 后崩溃时，重复请求会重放已保存回答，再次恢复同一 checkpoint。

## 8. 清理

替换完成后删除：

- `AiChatRun.result_json`；
- `RunRepository.patch_result()`；
- `RunRepository.claim_question_resolution()`；
- `migrate_ai_chat_run_result.py`。

新增迁移删除旧 `ai_chat_runs.result` 列。该列当前仅用于尚未发布的 JD Agent，不迁移其中内容。

## 9. 验证范围

- 模型在 `plan_questions` 调用或不调用工具的两条路径；
- 问题数量、轮次、重复键和目标字段校验；
- `awaiting_input` 的首次回答、重放、冲突和并发领取；
- 回答恢复后重新进入 `extract`，且提问结果对模型可见；
- 每个候选 JD 一个 `persist_jd`，部分失败互不影响；
- Graph 重放不会重复创建 JD；
- `AiChatRun` 不再含业务 JSON；
- Experience AI Chat 的自动工具、审批工具、结果投递和恢复流程保持不变。
