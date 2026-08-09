# AI Chat 有界会话记忆与上下文设计

**状态：** Implemented（独立 Memory 模块，尚未接入 AI Chat）

**日期：** 2026-08-08

**核心边界：** Memory 只生产历史 `messages`；当前 Run、Graph、模型调用、SSE 与前端代码均未接入或修改。

---

## 1. 对外接口

Memory 模块只有一个公开入口：

```python
class MemoryContextService:
    async def get_context_messages(
        self,
        *,
        conversation_id: int,
        run_id: int,
        run_kind: str,
        tools_enabled: bool,
    ) -> list[JsonObject]:
        ...
```

未来接入时，外部调用方只应做两件事：

```python
messages = await memory.get_context_messages(...)
value["messages"] = messages
```

Service 不返回：

- Graph State；
- Model Request；
- Run 边界或状态；
- Token Usage；
- SSE/前端事件。

当前生产调用链尚未调用这个 Service。因此本次改造不改变 Run/User/Assistant 的创建顺序，不改变 Graph interrupt/resume，也不改变业务 Adapter 和 Tool 协议。

---

## 2. 返回内容

返回值严格按以下顺序组成：

```text
Active Conversation Memory Message
+ 短期窗口内的完整 completed Run Messages
+ 当前 running Run 中已 completed 的 Messages
```

规则：

- 当前用户消息始终位于最后；
- generating、failed、cancelled 的消息残片不进入结果；
- 裁剪单位是完整 Run，不拆半轮对话；
- Staged Snapshot 只入库，绝不进入返回值；
- 已由 Active Memory 覆盖的 Run 不再以原文重复返回；
- Transcript 不因压缩而修改或删除。

Memory Message 使用普通历史角色并标记为派生、非权威内容。它不能覆盖 Experience/Resume 等 Domain Truth，也不能作为 System 指令。

---

## 3. Token 预算

Memory Service 内部使用同一个业务 Adapter 预演最终消息 Renderer，并使用该 Adapter 的真实 Tool Handlers 计数：

```text
System Prompt
+ Tool Schema
+ 当前业务数据
+ Active Memory
+ Recent RunBundles
+ Pending Tool Result
+ 当前用户消息
+ 输出预留
+ 安全余量
<= 模型输入上限
```

具体步骤：

1. 使用真实模型与 Tool Schema 建立 Memory 内部 Token Budget；
2. 先用仅含当前 Run 消息的 Adapter 输出计算固定占用；
3. 为 Conversation Memory 预留独立上限；
4. 从最新 Run 向前选择完整 RunBundle；
5. 使用 Adapter Renderer 对最终候选再次整体计数；
6. 超限时继续移除最老的完整 Run，而不是截断 Message。

Token Budget 只属于 Memory 内部选择逻辑，不写入 Graph State，也不改变真实模型调用接口。两次 Adapter 渲染之间若业务数据变化，安全余量负责吸收小幅漂移；Memory 不能冻结外部业务状态。

---

## 4. RunBundle

RunBundle 是最小裁剪与压缩单元：

```text
文本 Run：completed Run + User + completed Assistant
工具 Run：completed Run + User + optional Assistant + resolved Tool 紧凑结果
Opening Run：completed Run + completed Assistant
```

只有 `status=completed` 且结构完整的 Run 才能成为 Bundle。当前 running Run 不参与摘要，其已完成用户消息由 Service 单独追加。

每个 Bundle 生成稳定 Hash，绑定：

- Run ID 与类型；
- 完整消息；
- 紧凑 Tool 结果；
- 首尾 Message Sequence。

摘要完成后会重新读取来源 Bundle；Hash 变化时结果作废。

---

## 5. Conversation Memory

### 5.1 Core

```json
{
  "current_goal": null,
  "constraints": [],
  "preferences": [],
  "confirmed_decisions": [],
  "open_questions": []
}
```

### 5.2 Other

`other` 保存无法归入 Core、但后续仍重要的信息：

```json
{
  "preferred_wording": "保留自然口语感"
}
```

`other` 必须是单层 `snake_case` 字段；值只能是字符串或字符串数组，并受字段数、单字段 Token 和总 Token 上限约束。

禁止写入：

- Experience/Resume 业务事实；
- Scope、Revision；
- Tool 参数、审批状态与完整结果；
- 助手猜测或预测的下一问。

---

## 6. Operations

摘要 LLM 每次看到：

```text
完整 Parent Memory
+ 一个完整 RunBundle
```

输出：

```json
{
  "operations": [
    {
      "op": "update",
      "path": "core.current_goal",
      "value": "优化项目经历描述"
    }
  ]
}
```

规则：

- `add` 只能创建不存在的 `other.<key>`；
- `update` 替换已有 Core 或 Other 字段；
- `delete core.*` 只清空内容；
- `delete other.*` 删除字段和值；
- 同批 Operations 不能重复或冲突；
- 整批先校验再应用，失败时不产生部分更新；
- 合法空 Operations 仍生成连续 Snapshot。

---

## 7. Snapshot Chain

每个 Conversation 具有：

- 一个 Active Pointer；
- 一个不可变 Snapshot Chain；
- Active 后最多两个 Staged Snapshot。

主模型只能读取 Active Pointer 指向的 Snapshot。每个非 Root Snapshot 只消费 Parent 后紧邻的一个 RunBundle，并保存 Source Run、Source Hash、水位、Operations 和完整 Memory Document。

晋升流程不再次调用 LLM：

1. 校验 Target 是当前 Active 的连续后代；
2. 重新验证 Source Bundle Hash；
3. 使用 Active ID 做 CAS；
4. 将 Target 变为新 Active；
5. 清理旧祖先并保留后方最多两个 Stage。

同一 Conversation 使用可过期租约，数据库唯一边与 CAS 共同防止前后台形成分叉。

---

## 8. 当前压缩行为与待接入能力

当前模块只实现 Getter 内部必需的同步追赶：

- 若已有 Run 必须移出短期窗口，而对应 Stage 尚不存在，Getter 连续压缩到晋升目标之后最多两个已有 Run；
- 追赶完成后只晋升真正移出短期窗口的 Snapshot；后两个仍保持 Staged；
- 压缩使用 Snapshot Chain、租约与 Source Hash 校验。

“Run 完成后立即后台预压缩”需要 AI Chat 在完成边界触发调度，并管理任务关闭生命周期。该接入本期明确不做，后续实现时不得把调度状态放入 Graph State。

---

## 9. 失败边界

Memory Getter 不接管外部 Run 生命周期。Memory 选择、摘要或 Token 预演失败时直接抛出模块错误；当前没有生产调用方，所以不会改变现有 Run 状态。

始终保证：

- Memory 异常不修改 Transcript、Domain Truth、Tool 或 Checkpoint；
- 失败 Snapshot 不晋升；
- Active Pointer 不回退；
- 不把 Staged Snapshot 或不完整 Run 暴露给主模型；
- 不新增 Memory 专用 SSE、前端状态或错误协议。

如果未来需要专门的 `context_full` UI 或压缩进度，必须单独扩展外部错误/事件协议；它不属于当前“只返回 messages”的接口。

---

## 10. 组件职责

| 组件 | 职责 |
|---|---|
| `MemoryContextService` | 唯一 Facade；选择并返回历史 Messages |
| Token Budget | 按 Adapter 最终 Messages 与 Tools 计算占用 |
| RunBundle Builder | 构建不可拆分的 completed Run |
| Memory Summarizer | 基于 Parent + 一个 Run 生成 Operations |
| Memory Repository | 管理 Active Pointer、Snapshot、租约与 CAS |
| AI Chat Service | 当前完全未修改；后续只负责获取并填入 `value["messages"]` |
| Adapter / Graph / Model | 保持原调用方式，不感知 Memory Snapshot 或预算对象 |

---

## 11. 验收标准

- Memory 模块只公开 `MemoryContextService`；
- 当前外部调用链净改动为零；
- Graph State、Runtime、Model、Adapter、SSE、前端协议与改造前一致；
- 返回类型严格为 `list[JsonObject]`；
- 返回值只包含 Active Memory、完整 Recent Runs 和当前已完成消息；
- 当前用户消息不会因 RunBundle 只读取 completed Run 而丢失；
- Staged Snapshot 不可见；
- Active Memory 与 Recent Runs 不重叠；
- 追赶同时建立最多两个 Stage 缓冲；
- Source Hash 变化时拒绝晋升；
- Token Counter 输入包含最终 Renderer Messages 与真实 Tools；
- Memory 失败时抛出模块错误，不改变外部 Run 生命周期；
- Transcript、Domain Truth、Tool 与 Checkpoint 不受 Memory 写入影响。

---

## 12. 实现索引

- `app/ai_chat/memory/service.py`：唯一 Facade、历史选择、追赶与内部预压缩；
- `app/ai_chat/memory/token_budget.py`：Memory 私有 Token 预算；
- `app/ai_chat/memory/run_bundles.py`：完整 RunBundle；
- `app/ai_chat/memory/operations.py`：Core、Other 与 Operations 校验；
- `app/ai_chat/memory/summarizer.py`：摘要 LLM；
- `app/ai_chat/memory/repository.py`：Pointer、Snapshot、租约与 CAS；
- `app/ai_chat/models/models.py`：Memory ORM 表；
- `app/ai_chat/services/service.py`：当前未接入、未修改；后续计划接入 `_build_input()`。
