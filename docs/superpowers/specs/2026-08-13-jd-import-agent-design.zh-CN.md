# JD 导入与提取 Agent 设计

## 1. 目标与边界

本阶段用一个 LangGraph 完成 `JD 导入 -> JD 提取 -> 补问 -> 落库`。输入可以同时包含普通文本和 URL，用户不需要声明导入方式。一次输入可以识别多个 JD；无法判断内容归属、来源事实冲突或多个 URL 疑似指向同一 JD 时，不允许猜测，必须通过合并问题批次让用户裁定。

本阶段只提取现有字段：公司、岗位名称、类型、地点和岗位要求。学历、经验年限、技能等归入岗位要求，不扩展专用字段。Graph 到结构化 JD 落库为止，不包含后续 JD 分析、经历匹配或简历生成。

持久化与恢复边界以后续的 [JD Import Tool Call 设计](./2026-08-14-jd-import-tool-call-design.zh-CN.md) 为准：提问和每个候选 JD 的落库都固化为 Tool Call，`AiChatRun` 只记录运行生命周期。

## 2. 核心约束

- 一个 JD 最多保存一个 URL；纯文本 JD 的 URL 为空。
- 多个 URL 可以分别对应多个 JD；多个 URL 疑似对应同一 JD 时作为冲突提问，由用户选择最终 URL。
- 公司、岗位名称、至少一条有效岗位要求是关键字段。
- 类型、地点等非关键字段缺失时也要积极提问，但不阻止最终确认。
- 同一事实只提问一次；用户选择跳过或不知道后不再重复提问。
- 每批最多 12 题，最多 3 批；问题批次一次发出，前端逐题作答，整批提交后才恢复 Graph。
- 三批后不再提问。关键字段齐全且关键冲突已解决的 JD 保存为 `confirmed`，否则保存为 `incomplete`；两者都落库。
- 完整 JD 自动标记为 `confirmed`，不增加预览确认中断。
- 来源冲突不设置静默优先级，用户裁定是最终事实来源。
- 同一 URL 再次导入不查重、不更新旧记录，也不设置唯一约束。

## 3. Graph 主流程

```text
START
  -> parse_input
  -> resolve_urls
  -> extract
  -> assess
       |-- 有新问题且 question_round < 3
       |     -> plan_questions
       |     -> ask_questions -> interrupt
       |     -> merge_answers
       |     -> extract
       |
       `-- 无新问题或达到 3 轮
             -> persist
             -> END
```

节点职责：

- `parse_input`：用正则提取 URL，规范化并去重；其余输入形成文本来源。URL 与文本不是互斥关系。
- `resolve_urls`：在一个节点内完成 URL 安全校验、Agent 访问价值判断和 Playwright MCP 访问。安全校验必须先于访问，不能由模型绕过。
- `extract`：基于全部来源拆分多个 JD、分配内容、提取字段和要求，并为每个结果绑定来源引用。用户回答完成后必须回到该节点，不能直接覆盖旧候选。
- `assess`：服务端验证引用、检查完整性并生成冲突。引用校验失败的候选值必须丢弃，不能信任模型提供的 `supported` 标志。
- `plan_questions`：把归属不明、来源冲突、必填缺失、URL 访问失败和非必填缺失整理成一个问题批次。
- `ask_questions`：发出完整问题批次事件，并执行一次 `interrupt()`。
- `merge_answers`：验证整批答案，将答案追加为 `user_answer` 来源，然后回到 `extract`。
- `persist`：每个 JD 使用独立事务落库；单个失败不阻塞其他 JD。

## 4. State

```text
JDImportState
├─ input
│  ├─ raw_input
│  └─ detected_urls[]
├─ sources[]
│  ├─ source_id
│  ├─ type: text | url | user_answer
│  ├─ content
│  ├─ source_url
│  └─ url_status: skipped | fetched | blocked | failed
├─ candidates[]
│  ├─ jd_key
│  ├─ source_url
│  ├─ company / job_name / type / location
│  ├─ requirements[]
│  ├─ evidence[]
│  └─ missing_fields[]
├─ conflicts[]
├─ questions
│  ├─ round: 0..3
│  ├─ asked_question_keys[]
│  └─ answers[]
├─ question_tool_call_id
└─ result
   ├─ persisted_ids[]
   └─ errors[]
```

`conflicts[]` 同时承载内容归属不明、字段值冲突和多个 URL 指向同一 JD。`jd_key` 是本次运行内稳定标识，后续提取循环必须复用，防止同一 JD 被重复拆分或落库。

原始文本、网页正文、用户回答和证据引用只保留在 Graph 运行期；落库后丢弃。数据库不提供原文审计或重新核验能力。

## 5. URL 处理与安全

- 单次输入最多识别 10 个 URL，Agent 最多选择访问 5 个，其余记录为 `skipped`。
- 只允许 `http/https`；拒绝携带凭据的 URL，以及本机、内网、链路本地和保留地址。
- 限制端口、重定向次数、响应时间和页面体积；每次重定向后重新校验目标地址。
- 安全校验决定能否访问，Agent 只决定是否值得访问。
- 访问只允许使用受限 Playwright MCP，不提供普通 HTTP 或任意浏览器工具。
- 登录、验证码、拦截或超时记为 `blocked/failed`，后续优先请求用户粘贴内容或放弃来源。
- 网页内容是不可信数据。网页中的指令不能进入 Agent 控制层，也不能改变 Graph 路由或安全策略。
- 运行期仅保留清理后的正文、页面标题、最终 URL 和访问状态。

## 6. 提取与事实校验

每个字段和每条岗位要求必须返回 `source_id`、`quote`、`value`。`assess` 对 Unicode 和空白规范化后，检查引用是否真实存在于对应来源；不存在则丢弃该值，并转为缺失或冲突。

Agent 可以发现新 JD，但不能静默合并或删除已有候选。用户回答也是正式来源，可作为事实证据。无法确认归属的岗位要求不能写入任何 JD。非关键字段 unresolved 时可以留空；关键字段 unresolved 时最终状态为 `incomplete`。

## 7. 问题协议

问题优先级固定为：

1. 内容和 URL 归属
2. 关键字段冲突
3. 关键字段缺失
4. URL 访问失败
5. 非关键字段冲突或缺失

```text
Question
├─ question_id
├─ question_key
├─ kind: ownership | missing | conflict | source_access
├─ target_jd_keys[]
├─ field
├─ prompt
├─ mode: choice | text
├─ options[]
└─ allow_custom: true
```

选择题必须保留自定义填写能力；无法合理列出选项时使用填空题。`round` 和 `asked_question_keys[]` 在中断前进入 checkpoint，避免节点重放导致重复提问或轮次失控。

恢复载荷是独立协议，不能冒充工具审批：

```json
{
  "type": "question_batch_answer",
  "batch_id": "batch-...",
  "client_resolution_id": "...",
  "answers": [
    {
      "question_id": "question-...",
      "value": "...",
      "skipped": false
    }
  ]
}
```

必须覆盖批次全部问题，回答或明确跳过后才能将结果写入对应的 `awaiting_input` Tool Call，再以 `tool_call_id` 恢复 Graph。批次不匹配、遗漏题目或回答未知题目直接拒绝；`client_resolution_id` 由 Tool Call 唯一约束提供幂等边界。

## 8. API 与事件

```text
POST /api/v1/jd-imports/conversations
POST /api/v1/jd-imports/conversations/{id}/imports
POST /api/v1/jd-imports/conversations/{id}/question-batches/{batch_id}/resolve
```

- 创建会话时绑定 `JDImportAdapter`、`subject={type: jd_import, id: new}` 和空 `scope`。
- `imports` 接收混合文本和 URL，通过 SSE 启动 Graph。
- `resolve` 整批提交答案，并通过 SSE 继续同一 checkpoint。
- `jd.questions.requested` 携带完整问题批次。
- `jd.import.completed` 携带 `persisted_ids[]` 和 `errors[]`。
- Graph 级故障发送 `jd.import.failed`。

问答不是 Tool 审批，因此不复用 `/proposals/{id}/resolve`；它使用 Tool Call 的独立外部输入状态 `awaiting_input`。现有 JD 手工 CRUD 路径继续保留。

## 9. 持久化模型

删除 `jd_origin`，不保存 `raw_text`。业务模型调整为：

```text
jd_information
├─ id
├─ source_url: nullable
├─ company
├─ job_name
├─ type
├─ location
├─ status: incomplete | confirmed
└─ revision

jd_requirements
├─ id
├─ jd_information_id
├─ priority
├─ content
├─ sort_order
└─ revision
```

`source_url` 不设置唯一约束。每个候选 JD 对应一个稳定身份 `jd-import:persist:{jd_key}` 的 `persist_jd` Tool Call；工具执行与 JD 写入共用事务，数据库唯一约束是 checkpoint 重放时的幂等兜底，不向 JD 业务表加入运行字段。

## 10. 错误处理

- 空输入、超过 URL 输入上限、非法恢复载荷属于请求级错误，Graph 不启动或不恢复。
- URL 访问问题、单个 JD 提取失败属于局部错误，不终止整批。
- LLM 结构化输出失败最多修复一次；仍失败则写入 `errors[]`。
- 能识别但无法补全的 JD 以 `incomplete` 落库。
- 单个 JD 落库失败只回滚该事务并记录错误。
- checkpoint、模型或数据库整体不可用属于 Graph 级失败，不能伪装成业务 `incomplete`。
- `persist` 必须先读取运行结果映射，保证重放幂等。

## 11. 测试策略

- 纯函数测试：URL 解析和安全、问题去重与排序、轮次限制、引用匹配。
- 节点测试：使用 Fake Model 和 Fake Playwright MCP 验证多 JD 拆分、URL 决策、冲突生成和答案回流。
- Graph 测试：使用真实 checkpoint 覆盖 `提取 -> interrupt -> 整批恢复 -> 提取 -> 再次提问或落库`。
- API 集成测试：验证 SSE 顺序、错误码、批次幂等、多 JD 独立事务和最终状态。
- 受控冒烟测试：单独验证 Playwright MCP 集成，不让常规测试依赖真实网络或真实模型。

关键回归场景包括混合输入、多 URL 多 JD、多 URL 单 JD 冲突、内容归属不明、自定义选择答案、批次不完整、三轮封顶、回答回到提取、网页提示注入、完整与不完整 JD 同批落库、checkpoint 重放以及单个事务失败。
