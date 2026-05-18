"""大作业系统总入口：一键启动全链路自动化 Pipeline。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline import run_automation_pipeline
from src.utils.config_loader import ConfigError, load_app_config, validate_api_keys

EXCEL_PATH = "data/raw/sales_data.xlsx"
PDF_PATH = "data/raw/industry_report.pdf"
HTML_PATH = "data/raw/competitor_site.html"
FINAL_OUTPUT_PATH = "data/processed/2025_product_sales_and_market_analysis_report.md"


def _bootstrap_config() -> None:
    """启动前加载 .env / config.yaml 并校验 API Key。"""
    config = load_app_config()
    keys = validate_api_keys(config)
    print("[Config] 已加载 .env 与 config/config.yaml")
    print(f"[Config] DeepSeek (openai): {'*' * 8}{keys['openai_api_key'][-4:]}")
    print(f"[Config] 智谱 (zhipu)    : {'*' * 8}{keys['zhipu_api_key'][-4:]}")


def main() -> None:
    try:
        _bootstrap_config()
    except ConfigError as exc:
        print(f"\n[配置错误] {exc}")
        sys.exit(1)

    try:
        run_automation_pipeline(
            excel_path=EXCEL_PATH,
            pdf_path=PDF_PATH,
            html_path=HTML_PATH,
            final_output_path=FINAL_OUTPUT_PATH,
        )
    except ConfigError as exc:
        print(f"\n[配置错误] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[中断] 用户取消执行")
        sys.exit(130)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
