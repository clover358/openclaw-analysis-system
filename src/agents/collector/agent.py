"""Collector-Agent 实现入口（骨架）。

负责人可在本目录下自由扩展，例如：
    src/agents/collector/
        agent.py            # 当前文件，统一入口
        skills/
            excel_parser.py
            pdf_extractor.py
            web_scraper.py
        prompts.py
        ...

只要确保 `CollectorAgent` 类与 `run()` 方法可被 Pipeline 调用即可。
"""

from __future__ import annotations

from typing import Any

from src.agents.base import BaseAgent


class CollectorAgent(BaseAgent):
    """多源异构数据采集 Agent。"""

    name = "collector"

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """采集入口。

        建议签名：
            run(excel_path, pdf_path, html_path) -> tuple[list[dict], str]
        其中：
            - 第一项为结构化的中间产物（每条含 text / source_type / source_file），
              供 Analyst 构建向量库使用；
            - 第二项为人类可读的原始数据摘要，供 Reviewer 做事实比对。

        具体实现由 Collector 负责人补全。
        """
        raise NotImplementedError("CollectorAgent.run 待实现")
