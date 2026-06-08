"""飞书 AI Agent 长连接事件入口。

用于飞书开放平台「事件与回调 → 使用长连接接收事件」模式：

- 不需要公网域名 / ngrok / HTTP 回调地址；
- 需要在 .env 配置 FEISHU_APP_ID、FEISHU_APP_SECRET；
- 需要在飞书后台订阅「接收消息 v2.0 / im.message.receive_v1」事件；
- 收到消息后复用 handlers.feishu_command_handler 中的指令路由，触发 Pipeline 并回复飞书。

运行：
    python src/feishu_long_connection_server.py
"""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
except ImportError as exc:  # pragma: no cover - 仅在缺依赖时触发
    raise SystemExit(
        "缺少飞书长连接 SDK：lark-oapi。请先运行：\n"
        "  pip install lark-oapi\n"
        "或：\n"
        "  pip install -r requirements.txt"
    ) from exc

from src.handlers.feishu_command_handler import handle_incoming_message
from src.skills.feishu_openapi_skill import FeishuIncomingMessage, parse_im_message_receive_v1
from src.utils.config_loader import ConfigError, load_dotenv_file


def _get_env(name: str) -> str:
    """读取必需环境变量。"""
    import os

    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"未配置 {name}，请在项目根目录 .env 中填写")
    return value


def _sdk_event_to_dict(data: Any) -> dict[str, Any]:
    """将飞书 SDK 事件对象转为普通 dict，兼容 parse_im_message_receive_v1。"""
    marshalled = lark.JSON.marshal(data)
    if isinstance(marshalled, str):
        return json.loads(marshalled)
    if isinstance(marshalled, dict):
        return marshalled
    return json.loads(str(marshalled))


def _fallback_parse_message(body: dict[str, Any]) -> FeishuIncomingMessage | None:
    """SDK 结构差异兜底解析。"""
    try:
        event = body.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        content_raw = message.get("content") or "{}"
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
        text = str(content.get("text") or "").strip()
        message_id = str(message.get("message_id") or "")
        chat_id = str(message.get("chat_id") or "")
        sender_id = str(sender.get("sender_id", {}).get("open_id") or "")
        if not message_id or not chat_id:
            return None
        return FeishuIncomingMessage(message_id, chat_id, sender_id, text, body)
    except Exception:
        return None


def on_im_message_receive(data: P2ImMessageReceiveV1) -> None:
    """长连接收到 im.message.receive_v1 后的回调。"""
    try:
        body = _sdk_event_to_dict(data)
        message = parse_im_message_receive_v1(body) or _fallback_parse_message(body)
        if message is None:
            print("[FeishuLongConn] 收到非文本消息或无法解析，已忽略")
            return

        result = handle_incoming_message(message)
        print(f"[FeishuLongConn] 处理结果: {result}")
    except Exception as exc:
        print(f"[FeishuLongConn] 处理异常: {type(exc).__name__}: {exc}")


def build_event_handler():
    """构建飞书长连接事件分发器。"""
    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_im_message_receive)
        .build()
    )


def main() -> None:
    """启动飞书长连接客户端。"""
    try:
        load_dotenv_file()
        app_id = _get_env("FEISHU_APP_ID")
        app_secret = _get_env("FEISHU_APP_SECRET")
    except ConfigError as exc:
        raise SystemExit(f"[Config] {exc}") from exc

    print("=" * 62)
    print("OpenClaw 飞书 Agent 长连接服务".center(62))
    print("=" * 62)
    print("  接收模式 : 飞书开放平台长连接")
    print("  订阅事件 : im.message.receive_v1 / 接收消息 v2.0")
    print("  触发示例 : @机器人 给我发送竞品报告")
    print("  退出方式 : Ctrl+C")
    print("=" * 62)

    client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=build_event_handler(),
        log_level=lark.LogLevel.INFO,
    )

    def _stop(_signum=None, _frame=None) -> None:
        print("\n[FeishuLongConn] 收到退出信号，正在停止...")
        try:
            client.stop()
        finally:
            raise SystemExit(0)

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    client.start()


if __name__ == "__main__":
    main()
