"""所有 Agent 的统一基类。

提供：
- 配置加载与缓存
- 统一日志前缀（便于在 Pipeline 串联时区分来源）
- 子类只需实现 `run(...)`，保持各 Agent 的对外接口一致
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.utils.config_loader import ConfigError, load_app_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


class BaseAgent(ABC):
    """所有 Agent 的抽象基类。

    Agent 子类放在 `src/agents/<agent_name>/agent.py` 中，
    可在同一目录下自由组织 skills / prompts / utils 等子模块。
    """

    #: Agent 名称，用于日志前缀与从 config 中读取对应配置段
    name: str = "base"

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config: dict[str, Any] = self._load_config()
        self.agent_config: dict[str, Any] = self.config.get(self.name, {}) or {}

    def _load_config(self) -> dict[str, Any]:
        try:
            if self.config_path.resolve() == DEFAULT_CONFIG_PATH.resolve():
                return load_app_config()
            return load_app_config(config_path=self.config_path)
        except ConfigError:
            raise
        except Exception as exc:
            raise RuntimeError(f"[{self.name}] 加载配置失败: {exc}") from exc

    def log(self, message: str) -> None:
        print(f"[{self.name}] {message}")

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """每个 Agent 的统一执行入口，由子类实现。"""
        raise NotImplementedError
