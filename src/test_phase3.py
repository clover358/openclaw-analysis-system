"""阶段 3：Analyst-Agent RAG 链路测试。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.analyst import AnalystAgent
from src.skills.excel_parser import parse_sales_excel
from src.skills.pdf_extractor import extract_pdf_text
from src.skills.web_scraper import scrape_competitor_html

RAW_DIR = PROJECT_ROOT / "data" / "raw"
RAW_FILES = {
    "sales_excel": RAW_DIR / "sales_data.xlsx",
    "industry_report": RAW_DIR / "industry_report.pdf",
    "competitor_web": RAW_DIR / "competitor_site.html",
}
USER_REQUIREMENT = "分析2025年某产品的销售表现与市场竞争态势"


def _collect_raw_texts() -> list[dict]:
    parsers = {
        "sales_excel": parse_sales_excel,
        "industry_report": extract_pdf_text,
        "competitor_web": scrape_competitor_html,
    }
    raw_texts: list[dict] = []

    for source_type, path in RAW_FILES.items():
        if not path.exists():
            raise FileNotFoundError(f"缺少 raw 文件: {path}")

        parser = parsers[source_type]
        text = parser(path)
        raw_texts.append(
            {
                "text": text,
                "source_type": source_type,
                "source_file": path.name,
            }
        )
        print(f"[采集] {source_type} ({path.name}) -> {len(text)} 字符")

    return raw_texts


def _print_prompt_skeleton(result) -> None:
    separator = "=" * 60
    print(f"\n{separator}")
    print("最终 Prompt 骨架")
    print(separator)

    print("\n>>> System Prompt")
    print("-" * 40)
    print(result.system_prompt)

    print("\n>>> User Prompt")
    print("-" * 40)
    print(result.user_prompt)

    print(f"\n{separator}")
    print("分维度检索摘要")
    print(separator)
    for key, snippets in result.retrieved_contexts.items():
        print(f"\n[{key}] 命中 {len(snippets)} 条")
        for i, snippet in enumerate(snippets, start=1):
            preview = snippet[:200] + ("..." if len(snippet) > 200 else "")
            print(f"  片段 {i}: {preview}")

    if result.analysis:
        print(f"\n{separator}")
        print("LLM 分析结论（节选前 800 字）")
        print(separator)
        text = result.analysis
        print(text[:800] + ("..." if len(text) > 800 else ""))


def main() -> None:
    print("=" * 60)
    print("阶段 3 Analyst-Agent RAG 测试")
    print("=" * 60)

    try:
        raw_texts = _collect_raw_texts()
    except FileNotFoundError as exc:
        print(f"\n[中止] {exc}")
        print("请先将阶段 2 所需的三个文件放入 data/raw/ 后重试。")
        return
    except Exception as exc:
        print(f"\n[中止] 数据采集失败: {exc}")
        return

    try:
        agent = AnalystAgent()
        chunk_count = agent.build_knowledge_base(raw_texts)
        print(f"\n[RAG] 知识库构建完成，共 {chunk_count} 个 chunk")
    except Exception as exc:
        print(f"\n[失败] 知识库构建: {exc}")
        return

    print(f"\n[分析] 需求: {USER_REQUIREMENT}")
    print("[执行] 正在调用 DeepSeek 生成深度商业分析...")
    try:
        result = agent.analyze_data(USER_REQUIREMENT)
    except Exception as exc:
        print(f"\n[失败] analyze_data: {exc}")
        return

    _print_prompt_skeleton(result)
    print("\n" + "=" * 60)
    print("阶段 3 测试结束")
    print("=" * 60)


if __name__ == "__main__":
    main()
