"""Generator-Agent 实现入口（骨架）。

负责人可在本目录下自由组织，例如：
    src/agents/generator/
        agent.py        # 当前文件
        templates.py    # Markdown 报告模版
        parsers.py      # 章节切分 / 回填逻辑
        ...
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agents.base import BaseAgent


class GeneratorAgent(BaseAgent):
    """报告自动生成与回填 Agent。"""

    name = "generator"

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """生成报告入口。

        建议签名：
            run(analysis_text: str) -> str
        返回完整的 Markdown 报告字符串。
        """
        raise NotImplementedError("GeneratorAgent.run 待实现")

    def save(self, report_content: str, output_path: str | Path) -> Path:
        """落盘入口，由实现者补全（建议原子写入并自动建目录）。"""
        raise NotImplementedError("GeneratorAgent.save 待实现")
