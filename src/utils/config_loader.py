"""应用配置加载：.env + config.yaml，并解析 ${VAR_NAME} 环境变量占位符。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

_ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_cached_config: dict[str, Any] | None = None


class ConfigError(Exception):
    """配置或环境变量解析错误。"""


def load_dotenv_file(env_path: str | Path | None = None) -> Path | None:
    """
    加载项目根目录下的 .env 文件到环境变量。

    Returns:
        实际加载的 .env 路径；若文件不存在则返回 None（不报错，便于 CI 仅用系统环境变量）。
    """
    path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    try:
        if path.exists():
            load_dotenv(dotenv_path=path, override=False)
            return path.resolve()
        load_dotenv(override=False)
        return None
    except Exception as exc:
        raise ConfigError(f"加载 .env 失败 [{path}]: {exc}") from exc


def resolve_env_placeholders(value: Any, config_path: str = "") -> Any:
    """
    递归解析配置中的 ${VAR_NAME}，替换为 os.environ 中的值。

    Args:
        value: YAML 解析后的任意节点。
        config_path: 当前节点路径，用于错误提示。

    Raises:
        ConfigError: 引用的环境变量未设置或为空。
    """
    try:
        if isinstance(value, str):

            def _replace(match: re.Match[str]) -> str:
                var_name = match.group(1)
                env_value = os.getenv(var_name)
                if env_value is None or not str(env_value).strip():
                    hint = (
                        f"配置项 `{config_path}` 引用了环境变量 `${{{var_name}}}`，"
                        f"但未在 .env 或系统环境中找到有效值。\n"
                        f"请在项目根目录创建 `.env` 并添加：{var_name}=你的密钥"
                    )
                    raise ConfigError(hint)
                return env_value.strip()

            return _ENV_PLACEHOLDER_PATTERN.sub(_replace, value)

        if isinstance(value, dict):
            return {
                k: resolve_env_placeholders(v, f"{config_path}.{k}" if config_path else k)
                for k, v in value.items()
            }

        if isinstance(value, list):
            return [
                resolve_env_placeholders(item, f"{config_path}[{i}]")
                for i, item in enumerate(value)
            ]

        return value
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"解析环境变量占位符失败 [{config_path}]: {exc}") from exc


def load_app_config(
    config_path: str | Path | None = None,
    env_path: str | Path | None = None,
    *,
    reload: bool = False,
) -> dict[str, Any]:
    """
    加载 .env + YAML 配置，并解析所有 ${VAR_NAME} 占位符。

    Returns:
        已解析的完整配置字典（带缓存）。
    """
    global _cached_config

    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    use_cache = not reload and config_path is None and _cached_config is not None

    if use_cache:
        return _cached_config

    try:
        load_dotenv_file(env_path)

        if not cfg_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {cfg_path}")

        with cfg_path.open(encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        if not isinstance(raw_config, dict):
            raise ValueError("配置文件根节点必须为字典")

        resolved = resolve_env_placeholders(raw_config)

        if config_path is None:
            _cached_config = resolved

        return resolved
    except ConfigError:
        raise
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 解析失败 [{cfg_path}]: {exc}") from exc
    except Exception as exc:
        raise ConfigError(f"加载应用配置失败: {exc}") from exc


def get_openai_api_key(config: dict[str, Any] | None = None) -> str:
    """获取 DeepSeek / OpenAI 兼容 Chat 的 API Key。"""
    cfg = config or load_app_config()
    api_key = ((cfg.get("openai") or {}).get("api_key") or "").strip()
    if not api_key:
        raise ConfigError(
            "未找到 openai.api_key。请在 config/config.yaml 中配置 ${OPENAI_API_KEY}，"
            "并在 .env 中设置 OPENAI_API_KEY。"
        )
    return api_key


def get_zhipu_api_key(config: dict[str, Any] | None = None) -> str:
    """获取智谱 Embedding 的 API Key。"""
    cfg = config or load_app_config()
    api_key = ((cfg.get("zhipu") or {}).get("api_key") or "").strip()
    if not api_key:
        raise ConfigError(
            "未找到 zhipu.api_key。请在 config/config.yaml 中配置 ${ZHIPU_API_KEY}，"
            "并在 .env 中设置 ZHIPU_API_KEY。"
        )
    return api_key


def validate_api_keys(config: dict[str, Any] | None = None) -> dict[str, str]:
    """校验并返回流水线所需的全部 API Key。"""
    cfg = config or load_app_config()
    return {
        "openai_api_key": get_openai_api_key(cfg),
        "zhipu_api_key": get_zhipu_api_key(cfg),
    }
