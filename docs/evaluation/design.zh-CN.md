# 精简评估流程

当前范围：数据 → 执行 Agent → DeepEval 评分。Langfuse 用于查看执行输出和分数。

## 实现

- evals/src/evaluation/flow.py：顺序读取案例，调用业务函数，将实际回答、工具调用和检索内容交给 DeepEval。
- evals/src/evaluation/cli.py：加载 JSONL 和业务模块，输出逐案例 JSON 结果；可选择投递到 Langfuse。
- 业务模块只提供 run_agent(input) 和 create_metrics() 两个函数，不建立通用适配器体系。

具体评估维度后续决定。框架不生成数据集，不预设业务评分规则。
未采集的过程证据保持缺失，不能视为 Agent 没有调用工具。

## 删除的旧设计

移除运行时封装、预算、健康检查、脱敏、运行信封、运行清单、独立事件日志和清理策略。
使用说明以 evals/README.md 为准。旧版“基础设施完成”不代表真实 Agent / DeepEval / Langfuse 已完成联调。

## 验证

使用本地替代 SDK 对象测试流程。真实模型调用和平台投递尚未验证。
