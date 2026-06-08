"""定位报告中旧日期来源。

运行：
    python demo.py

功能：
1. 搜索项目关键数据/输出文件中是否出现 2025-07-28、2026-05-16 等日期；
2. 检查 Analyst preprocess 的 core_data_pack / data_cards 是否含旧日期；
3. 打印命中文件与上下文片段。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.agents.analyst.agent import AnalystAgent
from src.utils.config_loader import load_dotenv_file

PROJECT_ROOT = Path(__file__).resolve().parent
TARGETS = [
    "2025-07-28",
    "2025年7月28日",
    "2025 年 7 月 28 日",
    "2026-05-16",
    "2026年5月16日",
    "2026 年 5 月 16 日",
]
SEARCH_DIRS = [
    PROJECT_ROOT / "data",
    PROJECT_ROOT / "config",
    PROJECT_ROOT / "src",
]
INCLUDE_SUFFIXES = {".json", ".md", ".txt", ".py", ".yaml", ".yml"}


def snippet(text: str, needle: str, width: int = 260) -> str:
    idx = text.find(needle)
    if idx < 0:
        return ""
    start = max(0, idx - width)
    end = min(len(text), idx + len(needle) + width)
    return text[start:end].replace("\r", "")


def search_files() -> None:
    print("=" * 90)
    print("1) 文件全文搜索旧日期")
    print("=" * 90)
    hits = 0
    for root in SEARCH_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in INCLUDE_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for target in TARGETS:
                if target in text:
                    hits += 1
                    rel = path.relative_to(PROJECT_ROOT)
                    print(f"\n[命中] {rel} -> {target}")
                    print(snippet(text, target))
    if hits == 0:
        print("未在 data/config/src 文本文件中找到目标旧日期。")


def search_preprocess() -> None:
    print("\n" + "=" * 90)
    print("2) Analyst preprocess 注入内容检查")
    print("=" * 90)
    load_dotenv_file()
    agent = AnalystAgent()
    pre = agent.preprocess()
    print(f"observation_start: {pre.observation_start}")
    print(f"observation_end  : {pre.observation_end}")

    blocks = {
        "core_data_pack": pre.core_data_pack,
        "data_cards": pre.data_cards,
        "sheet_summaries": "\n\n".join(pre.sheet_summaries.values()),
        "table_snapshots": "\n\n".join(pre.table_snapshots.values()),
    }
    for name, text in blocks.items():
        print(f"\n-- {name} --")
        found = False
        for target in TARGETS:
            if target in text:
                found = True
                print(f"[命中] {target}")
                print(snippet(text, target))
        if not found:
            dates = sorted(set(re.findall(r"20\d{2}-\d{2}-\d{2}", text)))
            print(f"未命中目标旧日期；日期最新 10 个: {dates[-10:]}")

    debug_path = PROJECT_ROOT / "data" / "outputs" / "date_source_debug_preprocess.txt"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(
        "# core_data_pack\n\n" + pre.core_data_pack +
        "\n\n# data_cards\n\n" + pre.data_cards +
        "\n\n# sheet_summaries\n\n" + blocks["sheet_summaries"] +
        "\n\n# table_snapshots\n\n" + blocks["table_snapshots"],
        encoding="utf-8",
    )
    print(f"\n预处理调试内容已写入: {debug_path}")


def main() -> None:
    search_files()
    search_preprocess()


if __name__ == "__main__":
    main()
