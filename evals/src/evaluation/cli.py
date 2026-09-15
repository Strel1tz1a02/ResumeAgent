from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from pathlib import Path

from dotenv import load_dotenv

from .flow import run_cases


def main() -> int:
    parser = argparse.ArgumentParser(description="数据 -> Agent -> DeepEval")
    parser.add_argument("data", type=Path, help="JSONL 案例文件")
    parser.add_argument("--suite", required=True, help="提供 run_agent 和 create_metrics 的 Python 模块")
    parser.add_argument("--output", type=Path, default=Path("results.json"))
    parser.add_argument("--env", type=Path, default=Path(".env"), help="评估模型环境变量文件")
    parser.add_argument("--langfuse", action="store_true", help="将案例和评分写入已配置的 Langfuse")
    args = parser.parse_args()
    load_dotenv(args.env, override=False)
    cases = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    suite = importlib.import_module(args.suite)
    client = None
    if args.langfuse:
        from langfuse import Langfuse
        client = Langfuse()
    results = asyncio.run(run_cases(cases, suite.run_agent, suite.create_metrics, client=client))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if client is not None:
        client.flush()
    failed = sum(row["status"] != "passed" for row in results)
    print(f"案例 {len(results)}，未通过 {failed}；结果：{args.output}")
    return int(bool(failed) or not results)
