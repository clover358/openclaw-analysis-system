"""阶段 5：Reviewer-Agent 闭环核查与修正测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.reviewer import ReviewerAgent

REPORT_PATH = PROJECT_ROOT / "data" / "processed" / "sales_and_market_report_2025.md"
MOCK_COLLECTOR_PATH = PROJECT_ROOT / "data" / "mock" / "mock_collector.json"
REVISED_OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "sales_and_market_report_2025_reviewed.md"
PREVIEW_LEN = 500


def _load_report() -> str:
    if not REPORT_PATH.exists():
        raise FileNotFoundError(
            f"报告初稿不存在: {REPORT_PATH}\n请先运行 python src/test_phase4.py 生成报告。"
        )
    try:
        return REPORT_PATH.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise RuntimeError(f"读取报告失败: {exc}") from exc


def _build_raw_data_summary() -> str:
    if not MOCK_COLLECTOR_PATH.exists():
        raise FileNotFoundError(f"原始数据摘要不存在: {MOCK_COLLECTOR_PATH}")

    try:
        data = json.loads(MOCK_COLLECTOR_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"mock_collector.json 解析失败: {exc}") from exc

    sources = data.get("sources") or {}
    parts: list[str] = [
        f"采集快照 ID: {data.get('snapshot_id', 'N/A')}",
        f"采集时间: {data.get('collected_at', 'N/A')}",
        "",
    ]

    label_map = {
        "excel": "【Excel 销售数据】",
        "pdf": "【PDF 行业报告】",
        "web": "【竞品网页】",
    }

    for key, label in label_map.items():
        block = sources.get(key) or {}
        parts.append(label)
        parts.append(f"文件: {block.get('file_name', 'N/A')} | 状态: {block.get('status', 'N/A')}")

        if key == "excel":
            parts.append(block.get("preview_markdown") or block.get("note", ""))
        elif key == "pdf":
            parts.append(block.get("preview_text") or block.get("note", ""))
        else:
            title = block.get("title", "")
            preview = block.get("preview_text") or ""
            parts.append(f"标题: {title}\n{preview}")

        parts.append("")

    return "\n".join(parts).strip()


def _print_banner(title: str) -> None:
    width = 62
    print("\n" + "=" * width)
    print(title.center(width))
    print("=" * width)


def _print_audit_result(result: dict) -> None:
    is_passed = result.get("is_passed", False)
    opinions = result.get("review_opinions", "")
    revised = result.get("revised_content", "")

    status_text = "通过" if is_passed else "未通过"
    status_icon = "[PASS]" if is_passed else "[FAIL]"

    _print_banner("Reviewer-Agent 审计结果")
    print(f"\n  审计状态 : {status_icon}  {status_text}  (is_passed={is_passed})")

    _print_banner("具体找茬 / 修改意见 (review_opinions)")
    print(opinions or "（无）")

    _print_banner(f"修正后内容预览 (前 {PREVIEW_LEN} 字)")
    if revised:
        preview = revised[:PREVIEW_LEN] + ("..." if len(revised) > PREVIEW_LEN else "")
        print(preview)
        print(f"\n  [信息] 修正稿全文长度: {len(revised)} 字符")
    else:
        print("（无修正内容）")


def main() -> None:
    _print_banner("阶段 5 Reviewer-Agent 闭环核查测试")

    try:
        report_content = _load_report()
        raw_data_summary = _build_raw_data_summary()
        print(f"\n[输入] 报告初稿: {REPORT_PATH.name} ({len(report_content)} 字符)")
        print(f"[输入] 原始数据摘要: {MOCK_COLLECTOR_PATH.name} ({len(raw_data_summary)} 字符)")
    except Exception as exc:
        print(f"\n[中止] {type(exc).__name__}: {exc}")
        return

    try:
        reviewer = ReviewerAgent()
        print("\n[执行] 正在调用 DeepSeek 进行合规审计（Structured Output）...")
        result = reviewer.audit_report(report_content, raw_data_summary)
    except Exception as exc:
        print(f"\n[失败] {type(exc).__name__}: {exc}")
        return

    _print_audit_result(result)

    try:
        revised = (result.get("revised_content") or "").strip()
        if revised and not result.get("is_passed", True):
            REVISED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            REVISED_OUTPUT_PATH.write_text(revised, encoding="utf-8")
            print(f"\n[保存] 修正稿已写入: {REVISED_OUTPUT_PATH}")
    except Exception as exc:
        print(f"\n[警告] 修正稿保存失败: {exc}")

    _print_banner("阶段 5 测试结束")


if __name__ == "__main__":
    main()
