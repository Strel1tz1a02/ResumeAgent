# Agent 评估：数据 → 执行 → 评分

只保留一个流程入口。DeepEval 负责评分，Langfuse 可选地展示案例、执行输出和分数。
没有数据生成器、脱敏、健康检查、预算系统、运行清单或适配器类层级。

## 使用

使用独立 Conda 环境安装评测依赖，避免 DeepEval 与后端依赖冲突：

```powershell
conda create -n resume-matcher-evals --override-channels -c conda-forge python=3.13 pip -y
conda activate resume-matcher-evals
python -m pip install -e .
```

复制评估模型配置：

```powershell
Copy-Item .env.example .env
```

- Agent 模型读取后端进程的 `apps/backend/.env`；评测进程只通过 `AGENT_BASE_URL` 调用它。
- 评估模型读取 `evals/.env` 中的 `EVAL_MODEL`、`EVAL_API_KEY`、可选 `EVAL_API_BASE`。
- 两个文件里的占位 Key 必须换成真实值；不要提交 `.env`。

简历生成已提供 `evaluation.suites.resume_generation`，其中 `run_agent()` 调用正式接口
`POST /api/v1/resume-generations/preview`。模块名为
`evaluation.suites.resume_generation`，案例 input 使用 JSON 字符串：

```json
{"id":"resume-1","input":"{\"jd_information_id\":1,\"mode\":\"llm\"}"}
```

它使用项目数据库中对应的 JD 和所有 ready 经历，因此运行前必须启动后端且这些数据必须存在。
`create_metrics()` 按当前任务边界保持未定义，后续填入评估方法后执行：

```powershell
python -m evaluation cases.jsonl `
  --suite evaluation.suites.resume_generation --output results.json
```

其他业务也可提供自己的 suite 模块，只需要两个函数：

```python
def run_agent(input: str):
    # 调用项目真实 Agent；也支持 async def。
    # 返回字符串，或以下字典：
    return {
        "actual_output": ...,       # 实际最终回答，字符串
        "tools_called": ...,        # 实际工具调用列表
        "retrieval_context": ...,   # 实际检索内容，字符串列表
    }

def create_metrics():
    # 每个案例创建新的 DeepEval 指标对象；具体维度由业务选择。
    return [...]
```

以上是接口说明，占位符必须换成真实调用和指标；仓库未提供虚假的业务执行实现。
tools_called 每项沿用 DeepEval ToolCall 字段：name、input_parameters、output。
没有采集工具过程时省略该字段，不要把未知过程填成空列表。

JSONL 每行一个案例，字段：
- input：传给 Agent 的字符串。
- expected_output：可选参考答案。
- context：可选参考资料。
- expected_tools：可选预期工具列表，与 ToolCall 字段一致。
- id：可选案例标识。

参考答案和预期工具只进入评分，不传给 Agent。本流程处理单次任务；完整多轮会话需后续采用 DeepEval 的会话测试对象。

## 结果

results.json 逐条包含输入、参考、实际输出、工具证据、耗时、指标分数、评分理由和通过状态。
执行或评分失败会保留错误；有未通过案例、错误或空数据时命令返回非零退出码。

需要 Langfuse 展示时，配置 LANGFUSE_PUBLIC_KEY、LANGFUSE_SECRET_KEY、LANGFUSE_BASE_URL，
在命令中加 --langfuse。默认只写本地结果。平台部署单独使用官方方案：
https://langfuse.com/self-hosting

SDK 使用依据：
- https://deepeval.com/docs/evaluation-test-cases
- https://langfuse.com/docs/observability/sdk/instrumentation

## 本次验证边界

本地测试使用替代 SDK 对象验证串联、参考隔离、错误保留和分数投递参数。
没有调用真实 Agent、评分模型或 Langfuse 服务。简历生成业务函数已经接入，指标仍由后续任务定义。
