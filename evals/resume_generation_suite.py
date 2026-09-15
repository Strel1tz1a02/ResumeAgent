"""兼容旧模块名；正式实现位于 evaluation.suites。"""

from evaluation.suites.resume_generation import create_metrics, run_agent

__all__ = ["create_metrics", "run_agent"]
