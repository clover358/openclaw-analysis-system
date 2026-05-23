"""Analyst-Agent 实现入口（骨架）。

负责人可在本目录下自由组织，例如：
    src/agents/analyst/
        agent.py        # 当前文件
        rag.py          # 向量库构建与检索
        prompts.py      # System / User Prompt 模版
        ...
"""

from __future__ import annotations

from typing import Any

from src.agents.base import BaseAgent


class AnalystAgent(BaseAgent):
    """基于 RAG 的深度商业分析 Agent。"""

    name = "analyst"

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """分析入口。

        建议签名：
            run(raw_texts: list[dict], user_requirement: str) -> str
        返回的字符串即为按章节组织的分析结论 Markdown。
        """
        raise NotImplementedError("AnalystAgent.run 待实现")
