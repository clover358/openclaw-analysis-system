"""飞书开放平台 Open API Skill（事件订阅 + 主动回复）。

与 ``feishu_bot_skill.py``（群自定义机器人 Webhook 单向推送）不同，本模块用于：

- 接收 ``im.message.receive_v1`` 事件（需配置事件订阅 URL）
- URL 验证 Challenge 响应
- 通过 ``tenant_access_token`` 向用户/群聊回复消息、发送卡片、上传 PDF

.env 配置::

    FEISHU_APP_ID=cli_xxxxxxxx
    FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
    FEISHU_VERIFICATION_TOKEN=                      # 事件订阅「Verification Token」
    FEISHU_ENCRYPT_KEY=                             # 若开启「加密」则填写

文档：https://open.feishu.cn/document/server-docs/event-subscription-guide/overview
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_FEISHU_API = "https://open.feishu.cn/open-apis"
_TOKEN_CACHE: dict[str, Any] = {"token": "", "expire_at": 0.0}


class FeishuOpenApiError(Exception):
    """飞书 Open API 调用失败。"""


@dataclass
class FeishuIncomingMessage:
    """解析后的用户消息。"""

    message_id: str
    chat_id: str
    sender_id: str
    text: str
    raw_event: dict[str, Any]


def _app_id() -> str:
    value = os.getenv("FEISHU_APP_ID", "").strip()
    if not value:
        raise FeishuOpenApiError("未配置 FEISHU_APP_ID")
    return value


def _app_secret() -> str:
    value = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not value:
        raise FeishuOpenApiError("未配置 FEISHU_APP_SECRET")
    return value


def get_tenant_access_token(*, force_refresh: bool = False) -> str:
    """获取并缓存 tenant_access_token（有效期约 2 小时）。"""
    now = time.time()
    if not force_refresh and _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expire_at"]:
        return str(_TOKEN_CACHE["token"])

    url = f"{_FEISHU_API}/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url,
        json={"app_id": _app_id(), "app_secret": _app_secret()},
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuOpenApiError(f"获取 tenant_access_token 失败: {data}")

    token = str(data["tenant_access_token"])
    expire = int(data.get("expire", 7200))
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expire_at"] = now + max(expire - 120, 60)
    return token


def handle_url_verification(body: dict[str, Any]) -> dict[str, str]:
    """飞书事件订阅 URL 验证：原样返回 challenge。"""
    challenge = str(body.get("challenge", "")).strip()
    if not challenge:
        raise FeishuOpenApiError("url_verification 缺少 challenge 字段")
    return {"challenge": challenge}


def verify_event_token(body: dict[str, Any]) -> None:
    """校验 Verification Token（若已配置）。"""
    expected = os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip()
    if not expected:
        return
    token = str(body.get("token") or body.get("header", {}).get("token") or "").strip()
    if token and token != expected:
        raise FeishuOpenApiError("Verification Token 不匹配")


def parse_im_message_receive_v1(body: dict[str, Any]) -> FeishuIncomingMessage | None:
    """从事件体解析 ``im.message.receive_v1`` 文本消息。"""
    header = body.get("header") or {}
    if header.get("event_type") != "im.message.receive_v1":
        return None

    event = body.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}

    if message.get("message_type") != "text":
        return None

    content_raw = message.get("content") or "{}"
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
    except json.JSONDecodeError:
        content = {}

    text = str(content.get("text") or "").strip()
    message_id = str(message.get("message_id") or "")
    chat_id = str(message.get("chat_id") or "")
    sender_id = str(sender.get("sender_id", {}).get("open_id") or sender.get("sender_id") or "")

    if not message_id or not chat_id:
        return None

    return FeishuIncomingMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_id=sender_id,
        text=text,
        raw_event=body,
    )


def send_text_to_chat(chat_id: str, text: str) -> dict[str, Any]:
    """向群聊/单聊发送文本消息。"""
    token = get_tenant_access_token()
    url = f"{_FEISHU_API}/im/v1/messages"
    params = {"receive_id_type": "chat_id"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    resp = requests.post(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuOpenApiError(f"发送文本失败: {data}")
    return data


def send_interactive_card_to_chat(
    chat_id: str,
    *,
    title: str,
    lark_md_content: str,
    header_template: str = "orange",
    note: str = "",
) -> dict[str, Any]:
    """向群聊/单聊发送 interactive 消息卡片。"""
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": lark_md_content}}
    ]
    if note.strip():
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": note.strip()}],
                },
            ]
        )

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": header_template,
        },
        "elements": elements,
    }

    token = get_tenant_access_token()
    url = f"{_FEISHU_API}/im/v1/messages"
    params = {"receive_id_type": "chat_id"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    resp = requests.post(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuOpenApiError(f"发送卡片失败: {data}")
    return data


def upload_file(file_path: str | Path, *, file_type: str = "stream") -> str:
    """上传文件到飞书，返回 file_key（可用于发送文件消息）。"""
    path = Path(file_path)
    if not path.is_file():
        raise FeishuOpenApiError(f"文件不存在: {path}")

    token = get_tenant_access_token()
    url = f"{_FEISHU_API}/im/v1/files"
    with path.open("rb") as f:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (path.name, f)},
            data={"file_type": file_type, "file_name": path.name},
            timeout=60,
        )
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuOpenApiError(f"上传文件失败: {data}")
    file_key = (data.get("data") or {}).get("file_key")
    if not file_key:
        raise FeishuOpenApiError(f"上传文件未返回 file_key: {data}")
    return str(file_key)


def send_file_to_chat(chat_id: str, file_key: str) -> dict[str, Any]:
    """向群聊发送已上传的文件。"""
    token = get_tenant_access_token()
    url = f"{_FEISHU_API}/im/v1/messages"
    params = {"receive_id_type": "chat_id"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
    }
    resp = requests.post(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuOpenApiError(f"发送文件失败: {data}")
    return data
