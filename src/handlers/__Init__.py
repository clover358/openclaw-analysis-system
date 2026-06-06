"""飞书事件与指令处理器。"""

from src.handlers.feishu_command_handler import (
    TRIGGER_PHRASES,
    handle_incoming_message,
    should_trigger_pipeline,
)

__all__ = [
    "TRIGGER_PHRASES",
    "should_trigger_pipeline",
    "handle_incoming_message",
]