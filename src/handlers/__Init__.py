"""飞书事件与指令处理器包。

避免在包初始化阶段导入具体 handler，防止脚本入口触发循环导入。
请在使用处直接导入：
    from src.handlers.feishu_command_handler import handle_incoming_message
"""

__all__: list[str] = []
