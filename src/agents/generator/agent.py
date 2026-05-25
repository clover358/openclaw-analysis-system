"""Generator-Agent：商业分析报告自动生成与回填。"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from src.utils.config_loader import ConfigError, load_app_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

REPORT_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("一、执行摘要", ("执行摘要", "摘要", "overview", "executive summary")),
    ("二、自身销售表现", ("自身销售表现", "自身销售", "销售表现", "销售业绩")),
    ("三、行业趋势研判", ("行业趋势研判", "行业趋势", "行业研判", "市场趋势")),
    ("四、竞品威胁与机会", ("竞品威胁与机会", "竞品威胁", "竞争态势", "竞品分析")),
    ("五、战略建议", ("战略建议", "策略建议", "行动建议", "建议")),
    ("六、风险提示", ("风险提示", "风险", "风险因素")),
)

DEFAULT_REPORT_TITLE = "2025年某产品销售与市场分析报告"


class GeneratorAgent:
    """将 Analyst-Agent 分析结论回填至标准 Markdown 报告模版。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self.generator_cfg = self.config.get("generator", {})
        report_cfg = self.generator_cfg.get("report", {})
        self.report_title = report_cfg.get("title", DEFAULT_REPORT_TITLE)
        self.default_output = PROJECT_ROOT / report_cfg.get(
            "default_output",
            "data/processed/sales_and_market_report_2025.md",
        )

    def _load_config(self) -> dict[str, Any]:
        try:
            if self.config_path.resolve() == DEFAULT_CONFIG_PATH.resolve():
                return load_app_config()
            return load_app_config(config_path=self.config_path)
        except ConfigError:
            raise
        except Exception as exc:
            raise RuntimeError(f"读取配置失败: {exc}") from exc

    @staticmethod
    def _normalize_heading(text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r"^[#\s\d、.．\-]+", "", text)
        text = re.sub(r"\s+", "", text)
        return text

    def _match_section_key(self, heading: str) -> str | None:
        normalized = self._normalize_heading(heading)
        for section_title, aliases in REPORT_SECTIONS:
            for alias in aliases:
                alias_norm = self._normalize_heading(alias)
                if alias_norm in normalized or normalized in alias_norm:
                    return section_title
        return None

    def _parse_analysis_sections(self, analysis_text: str) -> dict[str, str]:
        """从 Analyst 输出中按 Markdown 标题（##）解析各章节正文。"""
        parsed: dict[str, str] = {}
        text = analysis_text.strip()
        if not text:
            return parsed

        try:
            pattern = re.compile(r"^#{1,3}\s*(.+?)\s*$", re.MULTILINE)
            matches = list(pattern.finditer(text))

            if not matches:
                return parsed

            for idx, match in enumerate(matches):
                heading = match.group(1).strip()
                start = match.end()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
                body = text[start:end].strip()
                section_key = self._match_section_key(heading)
                if section_key and body and section_key not in parsed:
                    parsed[section_key] = body

        except Exception:
            return parsed

        return parsed

    def _build_section_body(self, section_title: str, parsed: dict[str, str]) -> str:
        if section_title in parsed and parsed[section_title].strip():
            return parsed[section_title].strip()
        return "（暂无数据，待 Analyst-Agent 分析回填。）"

    def generate_markdown_report(self, analysis_text: str) -> str:
        """
        将深度商业分析文本套入标准 Markdown 报告模版。

        Args:
            analysis_text: Analyst-Agent 产出的分析正文。

        Returns:
            完整 Markdown 报告字符串。
        """
        try:
            if not isinstance(analysis_text, str):
                raise TypeError("analysis_text 必须为字符串")

            content = analysis_text.strip()
            if not content:
                raise ValueError("analysis_text 不能为空")

            parsed = self._parse_analysis_sections(content)
            tz_cn = timezone(timedelta(hours=8))
            generated_at = datetime.now(tz_cn).strftime("%Y-%m-%d %H:%M:%S")

            header = (
                f"# {self.report_title}\n\n"
                f"| 项目 | 内容 |\n"
                f"| --- | --- |\n"
                f"| 报告类型 | 销售与市场综合分析 |\n"
                f"| 分析周期 | 2025 年度 |\n"
                f"| 生成时间 | {generated_at} |\n"
                f"| 生成引擎 | Generator-Agent（基于 Analyst-Agent 结论回填） |\n\n"
                f"---\n\n"
            )

            section_blocks: list[str] = []
            for section_title, _aliases in REPORT_SECTIONS:
                body = self._build_section_body(section_title, parsed)
                section_blocks.append(f"## {section_title}\n\n{body}\n")

            appendix = ""
            if not parsed:
                appendix = (
                    "\n---\n\n"
                    "## 附录：Analyst-Agent 原始分析全文\n\n"
                    f"{content}\n"
                )

            footer = (
                "\n---\n\n"
                "*本报告由 Generator-Agent 自动生成，内容来源于多源数据采集与 RAG 分析链路，"
                "仅供课程大作业与商业研讨参考。*\n"
            )

            return header + "\n".join(section_blocks) + appendix + footer

        except (TypeError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError(f"生成 Markdown 报告失败: {exc}") from exc

    def save_report(self, report_content: str, output_path: str | Path) -> Path:
        """
        将报告安全写入指定路径，目录不存在时自动创建。

        Returns:
            写入文件的绝对路径。
        """
        try:
            if not isinstance(report_content, str):
                raise TypeError("report_content 必须为字符串")

            content = report_content.strip()
            if not content:
                raise ValueError("report_content 不能为空")

            path = Path(output_path)
            if not path.is_absolute():
                path = PROJECT_ROOT / path

            path.parent.mkdir(parents=True, exist_ok=True)

            temp_path = path.with_suffix(path.suffix + ".tmp")
            try:
                temp_path.write_text(content, encoding="utf-8")
                temp_path.replace(path)
            except Exception:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                raise

            return path.resolve()

        except (TypeError, ValueError):
            raise
        except PermissionError as exc:
            raise PermissionError(f"无权限写入报告文件: {output_path}") from exc
        except OSError as exc:
            raise RuntimeError(f"保存报告失败 [{output_path}]: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"保存报告时发生未知错误: {exc}") from exc

    def run(self, analysis_text: str) -> str:
        return self.generate_markdown_report(analysis_text)

    def save(self, report_content: str, output_path: str | Path) -> Path:
        return self.save_report(report_content, output_path)
