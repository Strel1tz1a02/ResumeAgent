from __future__ import annotations

import inspect
from contextlib import nullcontext
from time import perf_counter


async def run_cases(cases, run_agent, create_metrics, *, client=None):
    """顺序执行案例。Agent 只收到 input，参考答案只进入评分环节。"""
    from deepeval.test_case import LLMTestCase, ToolCall

    results = []
    for index, case in enumerate(cases):
        row = {"id": case.get("id", str(index)), "input": case["input"],
               "expected_output": case.get("expected_output"),
               "expected_tools": case.get("expected_tools"), "metrics": [], "status": "error"}
        observation = (client.start_as_current_observation(name=str(row["id"]), input=case["input"])
                       if client is not None else nullcontext(None))
        with observation as span:
            started = perf_counter()
            try:
                output = run_agent(case["input"])
                if inspect.isawaitable(output):
                    output = await output
                if isinstance(output, str):
                    output = {"actual_output": output}
                row["output"] = output
                if span is not None:
                    span.update(output=output)
                    row["trace_id"] = span.trace_id
                test_case = LLMTestCase(
                    input=case["input"], actual_output=output["actual_output"],
                    expected_output=case.get("expected_output"), context=case.get("context"),
                    retrieval_context=output.get("retrieval_context"),
                    tools_called=([ToolCall(**tool) for tool in output["tools_called"]]
                                  if "tools_called" in output else None),
                    expected_tools=([ToolCall(**tool) for tool in case["expected_tools"]]
                                    if "expected_tools" in case else None),
                )
            except Exception as exc:
                row["error"] = {"stage": "execution", "message": str(exc)}
                results.append(row)
                continue
            finally:
                row["duration_seconds"] = perf_counter() - started
            try:
                metrics = list(create_metrics())
                if not metrics:
                    raise ValueError("create_metrics() must return at least one metric")
                for metric in metrics:
                    try:
                        metric.measure(test_case)
                        result = {"name": metric.__name__, "score": metric.score,
                                  "reason": metric.reason, "passed": metric.is_successful()}
                        row["metrics"].append(result)
                    except Exception as exc:
                        row["metrics"].append({"name": metric.__name__, "error": str(exc)})
                        continue
                    if span is not None:
                        span.score(name=result["name"], value=result["score"], comment=result["reason"])
                row["status"] = ("error" if any("error" in m for m in row["metrics"]) else
                                 "passed" if all(m["passed"] for m in row["metrics"]) else "failed")
            except Exception as exc:
                row["error"] = {"stage": "evaluation", "message": str(exc)}
            results.append(row)
    return results
