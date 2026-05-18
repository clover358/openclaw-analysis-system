"""阶段 2：Collector-Agent 多源数据采集 Skills 本地测试。"""

from __future__ import annotations

import sys
from pathlib import Path

# 将项目根目录加入 sys.path，便于从任意工作目录运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.skills.excel_parser import parse_sales_excel
from src.skills.pdf_extractor import extract_pdf_text
from src.skills.web_scraper import scrape_competitor_html

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PREVIEW_LEN = 300

FILES = {
    "excel": RAW_DIR / "sales_data.xlsx",
    "pdf": RAW_DIR / "industry_report.pdf",
    "html": RAW_DIR / "competitor_site.html",
}

PARSERS = {
    "excel": ("Excel 销售数据", parse_sales_excel),
    "pdf": ("PDF 行业报告", extract_pdf_text),
    "html": ("竞品网页 HTML", scrape_competitor_html),
}


def _preview(text: str, length: int = PREVIEW_LEN) -> str:
    if len(text) <= length:
        return text
    return text[:length] + "..."


def main() -> None:
    print("=" * 60)
    print("阶段 2 Collector-Agent Skills 测试")
    print(f"数据目录: {RAW_DIR}")
    print("=" * 60)

    all_exist = all(path.exists() for path in FILES.values())

    if not all_exist:
        missing = [name for name, path in FILES.items() if not path.exists()]
        print("\n以下 raw 文件缺失，跳过解析（可将样例放入 data/raw/ 后重试）:")
        for key in missing:
            print(f"  - [{key}] {FILES[key]}")
        print("\n提示: 可参考 data/mock/mock_collector.json 中的快照结构准备测试数据。")
        return

    for key, path in FILES.items():
        label, parser = PARSERS[key]
        print(f"\n--- {label} ({path.name}) ---")
        try:
            result = parser(path)
            print(_preview(result))
            print(f"\n[成功] 总长度: {len(result)} 字符")
        except Exception as exc:
            print(f"[失败] {type(exc).__name__}: {exc}")

    print("\n" + "=" * 60)
    print("测试结束")
    print("=" * 60)


if __name__ == "__main__":
    main()
