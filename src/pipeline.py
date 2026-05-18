"""阶段 6：Collector → Analyst → Generator → Reviewer 全链路自动化编排。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agents.analyst import AnalystAgent
from src.agents.generator import GeneratorAgent
from src.agents.reviewer import ReviewerAgent
from src.utils.config_loader import ConfigError, load_app_config, validate_api_keys
from src.skills.excel_parser import parse_sales_excel
from src.skills.pdf_extractor import extract_pdf_text
from src.skills.web_scraper import scrape_competitor_html

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


def _validate_input_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} 文件不存在: {path}")
    if not path.is_file():
        raise ValueError(f"{label} 路径不是有效文件: {path}")


def _step1_collect(
    excel_path: Path,
    pdf_path: Path,
    html_path: Path,
) -> tuple[list[dict], str]:
    """Collector：解析多源异构数据并生成原始摘要。"""
    _log("Collector", "📥 Step 1: 启动多源异构数据采集...")

    collectors = [
        ("sales_excel", excel_path, parse_sales_excel),
        ("industry_report", pdf_path, extract_pdf_text),
        ("competitor_web", html_path, scrape_competitor_html),
    ]

    raw_texts: list[dict] = []
    summary_parts: list[str] = [
        "=" * 56,
        "【原始异构数据摘要】Collector-Agent 多源采集结果",
        "=" * 56,
        "",
    ]

    label_map = {
        "sales_excel": ("Excel 销售数据", "📊"),
        "industry_report": ("PDF 行业报告", "📄"),
        "competitor_web": ("竞品网页 HTML", "🌐"),
    }

    for source_type, path, parser in collectors:
        try:
            _validate_input_file(path, source_type)
            _log("Collector", f"  {label_map[source_type][1]} 正在解析: {path.name}")
            text = parser(path)
            if not text or not str(text).strip():
                raise ValueError(f"{path.name} 解析结果为空")

            raw_texts.append(
                {
                    "text": text,
                    "source_type": source_type,
                    "source_file": path.name,
                }
            )

            title, emoji = label_map[source_type]
            summary_parts.extend(
                [
                    f"### {emoji} {title}",
                    f"- 文件: `{path.name}`",
                    f"- 字符数: {len(text)}",
                    "",
                    text[:3000] + ("...\n[内容已截断]" if len(text) > 3000 else ""),
                    "",
                    "-" * 40,
                    "",
                ]
            )
            _log("Collector", f"  ✅ {path.name} 解析完成 ({len(text)} 字符)")
        except Exception as exc:
            raise RuntimeError(f"Step 1 采集失败 [{source_type}]: {exc}") from exc

    raw_data_summary = "\n".join(summary_parts).strip()
    _log("Collector", f"🎉 Step 1 完成 | 数据源: {len(raw_texts)} 路 | 摘要总长: {len(raw_data_summary)} 字符")
    return raw_texts, raw_data_summary


def _step2_analyze(raw_texts: list[dict], user_requirement: str) -> tuple[str, int]:
    """Analyst：RAG 建库 + DeepSeek 深度分析。"""
    _log("Analyst", "🧠 Step 2: 启动 RAG 向量库构建与商业分析...")

    try:
        analyst = AnalystAgent()
        chunk_count = analyst.build_knowledge_base(raw_texts)
        _log("Analyst", f"  📚 FAISS 向量库已就绪 | chunks: {chunk_count}")

        _log("Analyst", "  🤖 正在调用 DeepSeek 生成多维结构化分析...")
        result = analyst.analyze_data(user_requirement)

        analysis_text = (result.analysis or "").strip()
        if not analysis_text:
            raise RuntimeError("DeepSeek 未返回有效分析文本")

        _log("Analyst", f"🎉 Step 2 完成 | 分析结论长度: {len(analysis_text)} 字符")
        return analysis_text, chunk_count
    except Exception as exc:
        raise RuntimeError(f"Step 2 分析失败: {exc}") from exc


def _step3_generate(analysis_text: str) -> str:
    """Generator：回填标准 Markdown 报告模版。"""
    _log("Generator", "📝 Step 3: 启动报告模版生成与回填...")

    try:
        generator = GeneratorAgent()
        draft_report = generator.generate_markdown_report(analysis_text)
        if not draft_report.strip():
            raise RuntimeError("报告初稿生成为空")

        _log("Generator", f"🎉 Step 3 完成 | 初稿长度: {len(draft_report)} 字符")
        return draft_report
    except Exception as exc:
        raise RuntimeError(f"Step 3 生成失败: {exc}") from exc


def _step4_review(draft_report: str, raw_data_summary: str) -> dict[str, Any]:
    """Reviewer：结构化合规审计。"""
    _log("Reviewer", "🔍 Step 4: 启动报告合规审计（Structured Output）...")

    try:
        reviewer = ReviewerAgent()
        audit_result = reviewer.audit_report(draft_report, raw_data_summary)

        is_passed = bool(audit_result.get("is_passed", False))
        status = "✅ 通过" if is_passed else "❌ 未通过"
        _log("Reviewer", f"🎉 Step 4 完成 | 审计结果: {status}")
        return audit_result
    except Exception as exc:
        raise RuntimeError(f"Step 4 审计失败: {exc}") from exc


def _step5_finalize(
    draft_report: str,
    audit_result: dict[str, Any],
    final_output_path: Path,
) -> tuple[Path, bool]:
    """智能纠错与最终落盘。"""
    _log("Pipeline", "💾 Step 5: 智能纠错与最终报告落盘...")

    try:
        generator = GeneratorAgent()
        is_passed = bool(audit_result.get("is_passed", False))
        opinions = (audit_result.get("review_opinions") or "").strip()
        revised = (audit_result.get("revised_content") or "").strip()

        if is_passed:
            final_content = draft_report
            used_revised = False
            _log("Pipeline", "  ✅ 审计通过，保存报告初稿为最终版本")
        else:
            used_revised = True
            _log("Pipeline", "  ⚠️ 审计未通过，Reviewer 找茬意见如下：")
            print("-" * 56)
            print(opinions or "（无具体意见）")
            print("-" * 56)

            if not revised:
                raise RuntimeError("审计未通过但 revised_content 为空，无法落盘修正稿")
            final_content = revised
            _log("Pipeline", "  🔧 已自动采用修正稿作为最终完美报告")

        saved_path = generator.save_report(final_content, final_output_path)
        _log("Pipeline", f"🎉 Step 5 完成 | 最终报告: {saved_path}")
        return saved_path, used_revised
    except Exception as exc:
        raise RuntimeError(f"Step 5 落盘失败: {exc}") from exc


def run_automation_pipeline(
    excel_path: str | Path,
    pdf_path: str | Path,
    html_path: str | Path,
    final_output_path: str | Path,
    *,
    user_requirement: str = DEFAULT_USER_REQUIREMENT,
) -> PipelineResult:
    """
    一键执行 Collector → Analyst → Generator → Reviewer 全链路。

    Args:
        excel_path: Excel 销售数据路径。
        pdf_path: PDF 行业报告路径。
        html_path: 竞品网页 HTML 路径。
        final_output_path: 最终报告输出路径。
        user_requirement: 传给 Analyst 的分析需求描述。

    Returns:
        PipelineResult 全链路结果摘要。
    """
    excel = Path(excel_path)
    pdf = Path(pdf_path)
    html = Path(html_path)
    output = Path(final_output_path)

    if not excel.is_absolute():
        excel = PROJECT_ROOT / excel
    if not pdf.is_absolute():
        pdf = PROJECT_ROOT / pdf
    if not html.is_absolute():
        html = PROJECT_ROOT / html
    if not output.is_absolute():
        output = PROJECT_ROOT / output

    print("\n" + "=" * 62)
    print("🚀 阶段 6 | 全链路自动化 Pipeline 启动".center(62))
    print("=" * 62)

    try:
        config = load_app_config()
        keys = validate_api_keys(config)
        _log("Pipeline", "🔐 配置已加载（.env + config.yaml，${VAR} 已解析）")
        _log(
            "Pipeline",
            f"   DeepSeek Key: {'*' * 8}{keys['openai_api_key'][-4:]} | "
            f"智谱 Key: {'*' * 8}{keys['zhipu_api_key'][-4:]}",
        )
    except ConfigError as exc:
        raise RuntimeError(f"配置加载失败: {exc}") from exc

    _log("Pipeline", f"Excel : {excel}")
    _log("Pipeline", f"PDF   : {pdf}")
    _log("Pipeline", f"HTML  : {html}")
    _log("Pipeline", f"Output: {output}")

    try:
        raw_texts, raw_data_summary = _step1_collect(excel, pdf, html)
        analysis_text, _chunk_count = _step2_analyze(raw_texts, user_requirement)
        draft_report = _step3_generate(analysis_text)
        audit_result = _step4_review(draft_report, raw_data_summary)
        saved_path, used_revised = _step5_finalize(draft_report, audit_result, output)

        print("\n" + "=" * 62)
        print("🏁 全链路执行成功".center(62))
        print("=" * 62)
        print(f"  📌 最终报告路径 : {saved_path}")
        print(f"  📌 是否采用修正稿: {'是' if used_revised else '否'}")
        print(f"  📌 审计是否通过 : {'是' if audit_result.get('is_passed') else '否'}")
        print("=" * 62 + "\n")

        return PipelineResult(
            raw_data_summary=raw_data_summary,
            analysis_text=analysis_text,
            draft_report=draft_report,
            audit_result=audit_result,
            final_report_path=saved_path,
            used_revised=used_revised,
        )
    except Exception as exc:
        print("\n" + "=" * 62)
        print("💥 全链路执行失败".center(62))
        print("=" * 62)
        print(f"  错误类型: {type(exc).__name__}")
        print(f"  错误信息: {exc}")
        print("=" * 62 + "\n")
        raise
