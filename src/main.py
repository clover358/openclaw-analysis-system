"""系统总入口：一键启动全链路自动化 Pipeline。

主题：基于 OpenRouter API 中转站的 LLM 厂商商业格局分析。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline import run_automation_pipeline
from src.utils.config_loader import ConfigError, load_app_config, validate_api_keys


def _bootstrap_config() -> None:
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
        run_automation_pipeline()
    except NotImplementedError as exc:
        print(f"\n[未实现] {exc}")
        print("提示：四个 Agent 中存在尚未补全的实现，请到 src/agents/ 下完成。")
        sys.exit(2)
    except ConfigError as exc:
        print(f"\n[配置错误] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[中断] 用户取消执行")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[运行失败] {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
