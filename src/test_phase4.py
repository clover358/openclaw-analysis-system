"""阶段 4：Generator-Agent 报告生成与回填测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.generator import GeneratorAgent

MOCK_ANALYSIS_PATH = PROJECT_ROOT / "data" / "mock" / "mock_analysis.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "sales_and_market_report_2025.md"
PREVIEW_LEN = 300

# 并行测试用固化分析文本（当 mock 文件不存在时使用）
FALLBACK_MOCK_ANALYSIS = """## 执行摘要

2025年某智能手表产品在核心区域销售稳中有升，行业保持增长，但竞品促销加剧。建议聚焦差异化与健康场景。

## 自身销售表现

全年累计销量约5,000台，销售额约1,250万元；华东区域贡献最高，入门款走量、高端款贡献毛利。

## 行业趋势研判

可穿戴市场持续扩容，健康监测与长续航成为主流迭代方向，政策鼓励智能硬件与医疗场景融合。

## 竞品威胁与机会

竞品降价与电商满减带来份额压力；银发健康与运动细分人群仍存在差异化机会。

## 战略建议

短期：优化渠道陈列与对比传播。中期：布局健康订阅服务并试点高端下沉。

## 风险提示

需关注成本波动、价格战及宏观消费复苏不及预期对高端销量的影响。
"""


def _load_mock_analysis_text() -> str:
    if MOCK_ANALYSIS_PATH.exists():
        try:
            raw = MOCK_ANALYSIS_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            text = (data.get("analysis_text") or "").strip()
            if text:
                print(f"[Mock] 已加载: {MOCK_ANALYSIS_PATH.name}")
                return text
            print(f"[Mock] {MOCK_ANALYSIS_PATH.name} 中 analysis_text 为空，使用内置测试文本")
        except json.JSONDecodeError as exc:
            print(f"[Mock] JSON 解析失败: {exc}，使用内置测试文本")
        except Exception as exc:
            print(f"[Mock] 读取失败: {exc}，使用内置测试文本")
    else:
        print(f"[Mock] 未找到 {MOCK_ANALYSIS_PATH.name}，使用内置测试文本")

    return FALLBACK_MOCK_ANALYSIS.strip()


def main() -> None:
    print("=" * 60)
    print("阶段 4 Generator-Agent 报告生成测试")
    print("=" * 60)

    try:
        mock_analysis_text = _load_mock_analysis_text()
        agent = GeneratorAgent()
        report = agent.generate_markdown_report(mock_analysis_text)
        saved_path = agent.save_report(report, OUTPUT_PATH)
    except Exception as exc:
        print(f"\n[失败] {type(exc).__name__}: {exc}")
        return

    preview = report[:PREVIEW_LEN] + ("..." if len(report) > PREVIEW_LEN else "")

    print(f"\n[成功] 报告已保存至: {saved_path}")
    print(f"[成功] 报告总长度: {len(report)} 字符")
    print(f"\n--- 报告预览（前 {PREVIEW_LEN} 字）---\n")
    print(preview)
    print("\n" + "=" * 60)
    print("阶段 4 测试结束")
    print("=" * 60)


if __name__ == "__main__":
    main()
