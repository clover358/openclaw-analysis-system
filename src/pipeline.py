"""阶段 6：Collector -> Analyst -> Generator -> Reviewer 全链路编排骨架。

本文件只负责 *串联*：把四个 Agent 的输出按既定顺序传给下一个 Agent，
并处理路径规范化、配置加载、最终落盘等通用工作。

四个 Agent 的内部实现请到 `src/agents/<agent_name>/` 下补全；
当某个 Agent 尚未实现时，本编排会抛出明确的 NotImplementedError，
不会影响其他模块的开发。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agents.analyst import AnalystAgent
from src.agents.collector import CollectorAgent
from src.agents.generator import GeneratorAgent
from src.agents.reviewer import ReviewerAgent
from src.utils.config_loader import ConfigError, load_app_config, validate_api_keys

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_USER_REQUIREMENT = "分析2025年某产品的销售表现与市场竞争态势"


@dataclass
class PipelineResult:
    """全链路执行结果摘要。"""

    raw_data_summary: str
    analysis_text: str
    draft_report: str
    audit_result: dict[str, Any]
    final_report_path: Path
    used_revised: bool


def _log(step: str, message: str) -> None:
    print(f"\n[{step}] {message}")


def _resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def run_automation_pipeline(
    excel_path: str | Path,
    pdf_path: str | Path,
    html_path: str | Path,
    final_output_path: str | Path,
    *,
    user_requirement: str = DEFAULT_USER_REQUIREMENT,
) -> PipelineResult:
    """一键执行 Collector -> Analyst -> Generator -> Reviewer 全链路。"""
    excel = _resolve_path(excel_path)
    pdf = _resolve_path(pdf_path)
    html = _resolve_path(html_path)
    output = _resolve_path(final_output_path)

    print("\n" + "=" * 62)
    print("阶段 6 | 全链路自动化 Pipeline 启动".center(62))
    print("=" * 62)

    try:
        config = load_app_config()
        keys = validate_api_keys(config)
        _log("Pipeline", "配置已加载（.env + config.yaml，${VAR} 已解析）")
        _log(
            "Pipeline",
            f"  DeepSeek Key: {'*' * 8}{keys['openai_api_key'][-4:]} | "
            f"智谱 Key: {'*' * 8}{keys['zhipu_api_key'][-4:]}",
        )
    except ConfigError as exc:
        raise RuntimeError(f"配置加载失败: {exc}") from exc

    _log("Pipeline", f"Excel : {excel}")
    _log("Pipeline", f"PDF   : {pdf}")
    _log("Pipeline", f"HTML  : {html}")
    _log("Pipeline", f"Output: {output}")

    # Step 1: Collector
    _log("Collector", "Step 1: 启动多源异构数据采集...")
    collector = CollectorAgent()
    raw_texts, raw_data_summary = collector.run(
        excel_path=excel,
        pdf_path=pdf,
        html_path=html,
    )

    # Step 2: Analyst
    _log("Analyst", "Step 2: 启动 RAG 向量库构建与商业分析...")
    analyst = AnalystAgent()
    analysis_text = analyst.run(
        raw_texts=raw_texts,
        user_requirement=user_requirement,
    )

    # Step 3: Generator
    _log("Generator", "Step 3: 启动报告模版生成与回填...")
    generator = GeneratorAgent()
    draft_report = generator.run(analysis_text=analysis_text)

    # Step 4: Reviewer
    _log("Reviewer", "Step 4: 启动报告合规审计...")
    reviewer = ReviewerAgent()
    audit_result = reviewer.run(
        report_content=draft_report,
        raw_data_summary=raw_data_summary,
    )

    # Step 5: Finalize
    _log("Pipeline", "Step 5: 智能纠错与最终报告落盘...")
    is_passed = bool(audit_result.get("is_passed", False))
    revised = (audit_result.get("revised_content") or "").strip()

    if is_passed:
        final_content = draft_report
        used_revised = False
        _log("Pipeline", "审计通过，保存初稿为最终版本")
    elif revised:
        final_content = revised
        used_revised = True
        _log("Pipeline", "审计未通过，已采用 Reviewer 修正稿")
    else:
        raise RuntimeError("审计未通过且 revised_content 为空，无法落盘")

    saved_path = generator.save(final_content, output)

    print("\n" + "=" * 62)
    print("全链路执行成功".center(62))
    print("=" * 62)
    print(f"  最终报告路径 : {saved_path}")
    print(f"  是否采用修正稿: {'是' if used_revised else '否'}")
    print(f"  审计是否通过 : {'是' if is_passed else '否'}")
    print("=" * 62 + "\n")

    return PipelineResult(
        raw_data_summary=raw_data_summary,
        analysis_text=analysis_text,
        draft_report=draft_report,
        audit_result=audit_result,
        final_report_path=saved_path,
        used_revised=used_revised,
    )
