"""阶段 6：Collector -> Analyst -> Generator -> Reviewer 全链路编排。

主题：基于 OpenRouter API 中转站的真实调用数据 + Stanford HAI AI Index 行业报告，
分析 LLM 厂商商业格局。主产品 = OpenAI；竞品 = 其余厂商。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.agents.analyst import AnalystAgent
from src.agents.collector import CollectorAgent
from src.agents.generator import GeneratorAgent
from src.agents.reviewer import ReviewerAgent
from src.utils.config_loader import ConfigError, load_app_config, validate_api_keys

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_USER_REQUIREMENT = (
    "基于 OpenRouter API 中转站的真实调用数据，分析 OpenAI 主体在该平台的"
    "表现、主要竞品（Anthropic / Google / Meta / DeepSeek / xAI / Qwen 等）"
    "的格局，以及应用层与行业宏观趋势。"
)


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


def _default_output_path() -> Path:
    """基于当前北京时间生成默认报告输出路径。"""
    tz_cn = timezone(timedelta(hours=8))
    stamp = datetime.now(tz_cn).strftime("%Y%m")
    return PROJECT_ROOT / "data" / "processed" / f"openrouter_llm_market_report_{stamp}.md"


# ── 图表块抽取与回填 ───────────────────────────────────────────────────────
# Generator 在每个章节末尾插入的图表块固定形如:
#     \n\n> **图 N：标题**\n\n![标题](report_xxx_chartNN.png)\n
# 当 Reviewer 重写 revised_content 时往往会丢掉这些行,
# 这里负责从 draft 中按章节抽出图表块,再原样塞回 revised 的同名章节末尾。

_IMG_BLOCK_PATTERN = re.compile(
    r"(?:>\s*\*\*图\s*\d+[^\n]*\*\*\s*\n+)?!\[[^\]]*\]\([^)]+\.(?:png|jpg|jpeg|gif|webp)\)",
    re.IGNORECASE,
)
_SECTION_HEAD_PATTERN = re.compile(r"(?m)^##\s+(.+?)\s*$")


def _split_sections(md: str) -> list[tuple[str, int, int]]:
    """切分 ## 章节: 返回 [(标题, body_start, body_end)] 列表。"""
    heads = list(_SECTION_HEAD_PATTERN.finditer(md))
    sections: list[tuple[str, int, int]] = []
    for idx, m in enumerate(heads):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = heads[idx + 1].start() if idx + 1 < len(heads) else len(md)
        sections.append((title, body_start, body_end))
    return sections


def _normalize_section_title(title: str) -> str:
    """归一化章节标题, 用于跨稿匹配 (容忍编号变化、空白差异)。"""
    norm = title.strip().lower()
    norm = re.sub(r"^[#\s\d、.．\-—–一二三四五六七八九十]+", "", norm)
    norm = re.sub(r"\s+", "", norm)
    return norm


def _extract_section_image_blocks(md: str) -> dict[str, list[str]]:
    """从 markdown 中按章节抽取图片块 (含上方题注行)。"""
    result: dict[str, list[str]] = {}
    for title, start, end in _split_sections(md):
        body = md[start:end]
        blocks = [m.group(0).strip() for m in _IMG_BLOCK_PATTERN.finditer(body)]
        if blocks:
            result[_normalize_section_title(title)] = blocks
    return result


def _reinject_images(draft: str, revised: str) -> tuple[str, int, int]:
    """把 draft 中各章节的图片块原样回填到 revised 的同名章节末尾。

    Returns:
        (patched_text, total_blocks_in_draft, blocks_reinjected)
    """
    draft_blocks = _extract_section_image_blocks(draft)
    total = sum(len(v) for v in draft_blocks.values())
    if total == 0:
        return revised, 0, 0

    revised_blocks = _extract_section_image_blocks(revised)
    sections = _split_sections(revised)

    pieces: list[str] = []
    cursor = 0
    reinjected = 0

    for title, body_start, body_end in sections:
        pieces.append(revised[cursor:body_start])
        body = revised[body_start:body_end]
        norm = _normalize_section_title(title)

        expected = draft_blocks.get(norm, [])
        existing = revised_blocks.get(norm, [])
        missing = [b for b in expected if b not in revised and b not in body]

        if expected and not existing and missing:
            stripped_body = body.rstrip()
            patched_body = stripped_body + "\n\n" + "\n\n".join(missing) + "\n\n"
            pieces.append(patched_body)
            reinjected += len(missing)
        else:
            pieces.append(body)

        cursor = body_end

    pieces.append(revised[cursor:])

    if reinjected == 0 and total > 0:
        all_in_revised = sum(
            1 for blocks in draft_blocks.values() for b in blocks if b in revised
        )
        if all_in_revised < total:
            tail = "\n\n## 附录：报告图表\n\n" + "\n\n".join(
                b for blocks in draft_blocks.values() for b in blocks if b not in revised
            ) + "\n"
            return revised + tail, total, total - all_in_revised

    return "".join(pieces), total, reinjected


def run_automation_pipeline(
    final_output_path: str | Path | None = None,
    *,
    user_requirement: str = DEFAULT_USER_REQUIREMENT,
) -> PipelineResult:
    """一键执行 Collector -> Analyst -> Generator -> Reviewer 全链路。"""
    output = _resolve_path(final_output_path) if final_output_path else _default_output_path()

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

    _log("Pipeline", f"最终报告输出: {output}")

    # Step 1: Collector
    _log("Collector", "Step 1: 启动多源异构数据采集 (OpenRouter rankings + AI Index PDF)...")
    collector = CollectorAgent()
    raw_texts, raw_data_summary = collector.run()

    # Step 2: Analyst
    _log("Analyst", "Step 2: 多源 JSON 预处理 + RAG 向量库构建 + 商业分析...")
    analyst = AnalystAgent()
    analysis_text = analyst.run(
        raw_texts=raw_texts,
        user_requirement=user_requirement,
    )

    # Step 3: Generator
    _log("Generator", "Step 3: 启动报告模板生成与回填 (DeepSeek 润色 + 智能图表)...")
    generator = GeneratorAgent()
    draft_report = generator.run(analysis_text=analysis_text)

    # Step 4: Reviewer
    _log("Reviewer", "Step 4: 启动报告合规审计 (含年份一致性校验)...")
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
        patched, total, reinjected = _reinject_images(draft_report, revised)
        final_content = patched
        used_revised = True
        if total == 0:
            _log("Pipeline", "审计未通过，已采用 Reviewer 修正稿（初稿无图表，无需回填）")
        elif reinjected > 0:
            _log(
                "Pipeline",
                f"审计未通过，已采用 Reviewer 修正稿；检测到 Reviewer 丢失 "
                f"{reinjected}/{total} 个图表块，已自动回填到对应章节",
            )
        else:
            _log(
                "Pipeline",
                f"审计未通过，已采用 Reviewer 修正稿（{total} 个图表块均已保留，无需回填）",
            )
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
