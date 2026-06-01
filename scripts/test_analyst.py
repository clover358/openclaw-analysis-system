"""单独测试 AnalystAgent：跳过 Collector，直接读已落盘的 merged_rankings.json
和（可选）行业 PDF 文本，跑 preprocess + RAG + 商业分析，把中间产物全部打印/落盘。

用法：
    python scripts/test_analyst.py
    python scripts/test_analyst.py --skip-llm        # 只跑 preprocess，不调 LLM
    python scripts/test_analyst.py --rankings X.json # 指定其他 merged_rankings 文件

输出：
- data/processed/openrouter_rankings.xlsx    多 Sheet 表格
- data/processed/analyst_data_card.md         数据资产说明
- data/processed/analyst_sheet_summaries.md   每个 Sheet 的自然语言摘要
- data/processed/analyst_analysis.md          DeepSeek 输出的最终分析文本（除非 --skip-llm）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.analyst import AnalystAgent  # noqa: E402
from src.utils.config_loader import load_app_config  # noqa: E402

PDF_PATH = PROJECT_ROOT / "data" / "ai_index_report_2026_chapter_4_economy.pdf"
OUT_DIR = PROJECT_ROOT / "data" / "processed"


def _load_industry_text() -> str:
    """如果本地 AI Index PDF 已下载就读出文本，否则返回空串。"""
    if not PDF_PATH.exists():
        print(f"[test] AI Index PDF 不存在，行业维度跳过: {PDF_PATH}")
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("[test] 未安装 pypdf，行业维度跳过")
        return ""
    reader = PdfReader(str(PDF_PATH))
    pages = []
    for idx, page in enumerate(reader.pages[:30], 1):
        text = (page.extract_text() or "").strip()
        pages.append(f"--- Page {idx} ---\n{text or '[本页无文本]'}")
    return "\n\n".join(pages).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="单独测试 AnalystAgent")
    parser.add_argument("--rankings", default=None, help="merged_rankings.json 路径")
    parser.add_argument("--xlsx", default=None, help="多 Sheet xlsx 输出路径")
    parser.add_argument("--skip-llm", action="store_true", help="只跑 preprocess，不调 LLM")
    parser.add_argument(
        "--requirement",
        default=(
            "基于 OpenRouter API 中转站的真实调用数据，分析 OpenAI 主体在该平台的"
            "表现、主要竞品（Anthropic / Google / Meta / DeepSeek / xAI / Qwen 等）"
            "的格局，以及应用层与行业宏观趋势。"
        ),
        help="分析需求文本",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 校验配置（不在这里崩，让 AnalystAgent 自己抛错以便定位）
    load_app_config()

    raw_texts: list[dict] = []
    industry_text = _load_industry_text()
    if industry_text:
        raw_texts.append({
            "source_type": "industry_report_pdf",
            "source_file": PDF_PATH.name,
            "text": industry_text,
        })

    print("[test] 初始化 AnalystAgent ...")
    agent = AnalystAgent()

    print("[test] Step 1: preprocess ...")
    pre = agent.preprocess(
        raw_texts=raw_texts,
        rankings_json=args.rankings,
        xlsx_output=args.xlsx,
    )
    print(f"[test] 多 Sheet xlsx -> {pre.xlsx_path}")
    (OUT_DIR / "analyst_data_card.md").write_text(pre.data_card, encoding="utf-8")
    print(f"[test] 数据资产说明 -> {OUT_DIR / 'analyst_data_card.md'}")

    summary_md = "\n\n".join(
        f"---\n\n{summary}" for summary in pre.sheet_summaries.values()
    )
    (OUT_DIR / "analyst_sheet_summaries.md").write_text(
        f"# Sheet 摘要 ({len(pre.sheet_summaries)} 个)\n\n{summary_md}",
        encoding="utf-8",
    )
    print(f"[test] Sheet 摘要 -> {OUT_DIR / 'analyst_sheet_summaries.md'}")

    if args.skip_llm:
        print("[test] --skip-llm 模式，已跳过 RAG 与 LLM 分析")
        return

    print("[test] Step 2: build_knowledge_base ...")
    n_chunks = agent.build_knowledge_base(preprocess_result=pre)
    print(f"[test] FAISS 切片数: {n_chunks}")

    print("[test] Step 3: analyze_data ...")
    result = agent.analyze_data(args.requirement)
    analysis = result.analysis or ""
    out = OUT_DIR / "analyst_analysis.md"
    out.write_text(analysis, encoding="utf-8")
    print(f"[test] 分析文本 -> {out}")
    print("\n========== 分析结果预览（前 1500 字）==========\n")
    print(analysis[:1500])


if __name__ == "__main__":
    main()
