"""Collector-Agent：多源异构数据采集。

负责把 Excel / PDF / 网页等异构数据源转换为统一的文本/结构化中间产物，
交给 Analyst-Agent 做后续 RAG 与分析。

实现入口约定：在本包内提供一个名为 `CollectorAgent` 的类，
其 `run(...)` 方法返回 collector 产出（具体结构由负责人自行定义，
建议为 `list[dict]` 或单一 `str`，并在 docstring 中写明）。
"""

from src.agents.collector.agent import CollectorAgent

__all__ = ["CollectorAgent"]
