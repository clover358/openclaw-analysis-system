"""Reviewer-Agent 实现入口（骨架）。

负责人可在本目录下自由组织，例如：
    src/agents/reviewer/
        agent.py        # 当前文件
        schema.py       # Structured Output 的 Pydantic 模型
        prompts.py      # 审计 Prompt
        ...
"""

from __future__ import annotations

from typing import Any

from src.agents.base import BaseAgent


class ReviewerAgent(BaseAgent):
    """报告闭环核查与修正 Agent。"""

    name = "reviewer"

    def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """审计入口。

        建议签名：
            run(report_content: str, raw_data_summary: str) -> dict
        返回结构建议：
            {
                "is_passed": bool,
                "review_opinions": str,
                "revised_content": str,
            }
        """
        raise NotImplementedError("ReviewerAgent.run 待实现")
