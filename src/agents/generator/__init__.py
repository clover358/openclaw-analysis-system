"""Generator-Agent：报告自动生成与回填。

把 Analyst-Agent 的分析结论填入标准 Markdown 模版，
并负责最终落盘。

实现入口约定：在本包内提供一个名为 `GeneratorAgent` 的类，
建议至少提供 `run(...)`（生成报告字符串）和 `save(...)`（落盘）两类方法。
"""

from src.agents.generator.agent import GeneratorAgent

__all__ = ["GeneratorAgent"]
