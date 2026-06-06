"""飞书 AI Agent 事件驱动服务入口（被动响应模式，可选，暂未启用）。

与 ``main.py``（CLI 主动批跑）分离：

- ``python src/main.py``              → 本地一键跑 Pipeline，完成后 Webhook 推群
- ``python src/feishu_agent_server.py`` → 监听飞书 im.message.receive_v1（需 App 凭证 + ngrok）

飞书后台配置：
    1. 创建企业自建应用：https://open.feishu.cn/app
    2. 权限：获取与发送消息、读取用户发给机器人的单聊消息、接收群聊中 @ 机器人 消息等
    3. 事件订阅 → 请求地址：``https://你的公网域名/feishu/webhook``
    4. 订阅 ``im.message.receive_v1``
    5. 将机器人添加到目标群聊

本地调试需内网穿透（ngrok / cpolar 等）将 8000 端口暴露为 HTTPS 公网 URL。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request

from src.handlers.feishu_command_handler import handle_incoming_message
from src.skills.feishu_openapi_skill import (
    FeishuOpenApiError,
    handle_url_verification,
    parse_im_message_receive_v1,
    verify_event_token,
)
from src.utils.config_loader import ConfigError, load_dotenv_file

app = Flask(__name__)


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok", "service": "openclaw-feishu-agent"}, 200


@app.post("/feishu/webhook")
def feishu_webhook():
    """
    飞书事件订阅统一入口。

    - URL 验证：``type=url_verification`` → 返回 ``{"challenge": "..."}``
    - 消息事件：``im.message.receive_v1`` → 异步触发 Agent / Pipeline
    """
    body = request.get_json(silent=True) or {}

    # ① URL 验证（配置事件订阅时飞书会先发 challenge）
    if body.get("type") == "url_verification":
        try:
            return jsonify(handle_url_verification(body))
        except FeishuOpenApiError as exc:
            return jsonify({"error": str(exc)}), 400

    # ② 普通事件
    try:
        verify_event_token(body)
    except FeishuOpenApiError as exc:
        print(f"[FeishuServer] Token 校验失败: {exc}")
        return jsonify({"code": 403, "msg": str(exc)}), 403

    message = parse_im_message_receive_v1(body)
    if message is None:
        return jsonify({})

    # 飞书要求 3 秒内响应；Pipeline 放后台线程
    try:
        result = handle_incoming_message(message)
        print(f"[FeishuServer] 处理结果: {result}")
    except Exception as exc:
        print(f"[FeishuServer] 处理异常: {type(exc).__name__}: {exc}")

    return jsonify({})


def main() -> None:
    load_dotenv_file()
    host = "0.0.0.0"
    port = 8000

    print("=" * 62)
    print("OpenClaw 飞书 Agent 服务（事件驱动）".center(62))
    print("=" * 62)
    print(f"  Webhook 路径 : http://127.0.0.1:{port}/feishu/webhook")
    print("  健康检查     : http://127.0.0.1:{}/health".format(port))
    print("  触发指令示例 : 「给我发送竞品报告」")
    print("  说明         : 需公网 HTTPS 地址供飞书回调；本地请用 ngrok 等穿透")
    print("=" * 62)

    try:
        load_dotenv_file()
        from src.utils.config_loader import validate_api_keys, load_app_config

        validate_api_keys(load_app_config())
        print("[Config] API Key 校验通过")
    except ConfigError as exc:
        print(f"[Config] 警告: {exc}")

    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()