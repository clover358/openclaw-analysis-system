"""Reviewer-Agent：报告闭环核查与修正。

比对 Generator 产出的报告与 Collector 的原始数据摘要，
输出结构化的审计结果（是否通过、修改意见、修正稿）。

实现入口约定：在本包内提供一个名为 `ReviewerAgent` 的类，
其 `run(...)` 方法返回审计结果（建议为 `dict`，包含
`is_passed` / `review_opinions` / `revised_content` 三个字段）。
"""

from src.agents.reviewer.agent import ReviewerAgent

__all__ = ["ReviewerAgent"]
