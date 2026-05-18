"""Analyst-Agent：基于 RAG 的多源数据分析。

读取 Collector-Agent 的产出，构建向量库并完成深度商业分析。

实现入口约定：在本包内提供一个名为 `AnalystAgent` 的类，
其 `run(...)` 方法返回分析结论（建议为 `str` 或包含正文与上下文的对象）。
"""

from src.agents.analyst.agent import AnalystAgent

__all__ = ["AnalystAgent"]
