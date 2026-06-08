"""飞书自定义机器人 Webhook Skill。

用于将 Pipeline 生成的 Markdown 报告推送至飞书群，适合企事业办公场景演示。

配置方式（项目根目录 `.env`）::

    # 飞书自定义机器人 Webhook（群设置 → 群机器人 → 自定义机器人 → 复制地址）
    FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/09f0a627-cd78-451c-b083-df510174e5be

    # 自定义关键词：须与飞书后台「安全设置 → 自定义关键词」完全一致
    # 未包含关键词时飞书返回 code=19024 "Key Words Not Found"
    FEISHU_WEBHOOK_KEYWORD=竞品报告

    # 可选：开启「签名校验」时填写
    FEISHU_WEBHOOK_SECRET=

官方文档：
    https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
"""

from __future__ import annotations

import sys
import base64
import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests

# ---------------------------------------------------------------------------
# 默认配置（可被环境变量或函数参数覆盖）
# ---------------------------------------------------------------------------
# WEBHOOK_URL：飞书自定义机器人完整 Webhook 地址
DEFAULT_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "").strip()

# WEBHOOK_KEYWORD：飞书后台配置的自定义关键词，每条消息正文/标题必须包含
DEFAULT_WEBHOOK_KEYWORD = os.getenv("FEISHU_WEBHOOK_KEYWORD", "竞品报告").strip()

# WEBHOOK_SECRET：签名校验密钥（未开启则留空）
DEFAULT_WEBHOOK_SECRET = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip()

DEFAULT_TIMEOUT_SEC = 15
DEFAULT_MAX_LARK_MD_CHARS = 2800
DEFAULT_MAX_TEXT_CHARS = 2000  # 纯文本分片上限（飞书单条 text 消息）

_FEISHU_API_BASE = "https://open.feishu.cn/open-apis/bot/v2/hook"
_FEISHU_KEYWORD_ERROR_CODE = 19024


class FeishuMessageType(str, Enum):
    TEXT = "text"
    POST = "post"
    INTERACTIVE = "interactive"


class FeishuBotError(Exception):
    """飞书机器人 API 调用失败。"""


@dataclass
class FeishuSendResult:
    ok: bool
    status_code: int
    feishu_code: int | None = None
    feishu_msg: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        if self.ok:
            return "发送成功"
        parts = [self.feishu_msg or "飞书返回异常"]
        if self.feishu_code is not None:
            parts.append(f"code={self.feishu_code}")
        return "；".join(parts)


def _resolve_webhook_url(webhook_url: str | None) -> str:
    url = (webhook_url or os.getenv("FEISHU_WEBHOOK_URL", DEFAULT_WEBHOOK_URL)).strip()
    if not url:
        raise FeishuBotError(
            "未配置飞书 Webhook URL。\n"
            "请在 .env 中设置 FEISHU_WEBHOOK_URL。\n"
            "示例：FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/你的Token"
        )
    if not url.startswith(f"{_FEISHU_API_BASE}/"):
        raise FeishuBotError(
            f"Webhook URL 格式不正确，应以 {_FEISHU_API_BASE}/ 开头"
        )
    return url


def _resolve_keyword(keyword: str | None) -> str:
    if keyword is not None:
        return keyword.strip()
    return os.getenv("FEISHU_WEBHOOK_KEYWORD", DEFAULT_WEBHOOK_KEYWORD).strip()


def _ensure_keyword_in_text(text: str, keyword: str) -> str:
    """确保文本包含自定义关键词，满足飞书 Webhook 安全校验。"""
    content = (text or "").strip()
    if not keyword:
        return content
    if keyword in content:
        return content
    return f"【{keyword}】\n\n{content}" if content else f"【{keyword}】"


def _ensure_keyword_in_title(title: str, keyword: str) -> str:
    base = (title or "").strip() or "通知"
    if not keyword or keyword in base:
        return base
    return f"【{keyword}】{base}"


def _build_signature(secret: str, timestamp: int) -> str:
    """飞书签名校验：Base64(HmacSHA256(timestamp + \\n + secret))。"""
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _explain_feishu_error(code: int | None, msg: str, keyword: str) -> str:
    if code == _FEISHU_KEYWORD_ERROR_CODE:
        return (
            f"飞书拒绝发送：消息未包含自定义关键词「{keyword or '（未配置）'}」。"
            f"请在 .env 设置 FEISHU_WEBHOOK_KEYWORD，并确保与飞书后台一致。"
        )
    return msg or "飞书返回异常"


def _post_payload(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    secret: str | None = None,
    keyword: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> FeishuSendResult:
    body = dict(payload)
    sign_secret = (
        secret if secret is not None else os.getenv("FEISHU_WEBHOOK_SECRET", DEFAULT_WEBHOOK_SECRET)
    ).strip()
    resolved_keyword = _resolve_keyword(keyword)

    if sign_secret:
        timestamp = int(time.time())
        body["timestamp"] = str(timestamp)
        body["sign"] = _build_signature(sign_secret, timestamp)

    try:
        resp = requests.post(webhook_url, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise FeishuBotError(f"HTTP 请求失败: {exc}") from exc
    except ValueError as exc:
        raise FeishuBotError(f"飞书响应非 JSON: {exc}") from exc

    code = data.get("code")
    msg = str(data.get("msg") or data.get("StatusMessage") or "")
    ok = resp.status_code == 200 and code in (0, None)
    if code not in (0, None):
        ok = False
        msg = _explain_feishu_error(
            code if isinstance(code, int) else None,
            msg,
            resolved_keyword,
        )

    return FeishuSendResult(
        ok=ok,
        status_code=resp.status_code,
        feishu_code=code if isinstance(code, int) else None,
        feishu_msg=msg,
        raw=data,
    )


def send_text_message(
    text: str,
    *,
    webhook_url: str | None = None,
    secret: str | None = None,
    keyword: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> FeishuSendResult:
    """发送纯文本消息（自动注入自定义关键词）。"""
    resolved_keyword = _resolve_keyword(keyword)
    content = _ensure_keyword_in_text((text or "").strip(), resolved_keyword)
    if not content:
        raise FeishuBotError("text 不能为空")

    url = _resolve_webhook_url(webhook_url)
    payload = {
        "msg_type": FeishuMessageType.TEXT.value,
        "content": {"text": content},
    }
    result = _post_payload(
        url, payload, secret=secret, keyword=resolved_keyword, timeout=timeout
    )
    if not result.ok:
        raise FeishuBotError(result.message)
    return result


def send_post_message(
    title: str,
    paragraphs: list[list[dict[str, Any]]],
    *,
    lang: str = "zh_cn",
    webhook_url: str | None = None,
    secret: str | None = None,
    keyword: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> FeishuSendResult:
    """发送飞书富文本 Post 消息（标题与首段自动注入关键词）。"""
    resolved_keyword = _resolve_keyword(keyword)
    safe_title = _ensure_keyword_in_title(title, resolved_keyword)

    safe_paragraphs = [list(row) for row in paragraphs]
    if resolved_keyword and safe_paragraphs:
        first_row = safe_paragraphs[0]
        first_text = next(
            (el.get("text", "") for el in first_row if el.get("tag") == "text"),
            "",
        )
        if resolved_keyword not in first_text and resolved_keyword not in safe_title:
            first_row.insert(0, {"tag": "text", "text": f"【{resolved_keyword}】\n"})

    url = _resolve_webhook_url(webhook_url)
    payload = {
        "msg_type": FeishuMessageType.POST.value,
        "content": {
            "post": {
                lang: {
                    "title": safe_title,
                    "content": safe_paragraphs,
                }
            }
        },
    }
    result = _post_payload(
        url, payload, secret=secret, keyword=resolved_keyword, timeout=timeout
    )
    if not result.ok:
        raise FeishuBotError(result.message)
    return result


def send_report_delivery_card(
    *,
    summary_md: str,
    pdf_local_path: str | Path,
    audit_status: str = "",
    md_local_path: str | Path | None = None,
    header_template: str = "orange",
    webhook_url: str | None = None,
    secret: str | None = None,
    keyword: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> FeishuSendResult:
    """
    发送「报告交付」精美消息卡片（单条，适合 Pipeline 收尾）。

    - 标题：📊 OpenClaw 自动化分析报告已生成（橙色/醒目 header）
    - 正文：执行摘要或报告前几段
    - 底部：PDF 本地路径提示（Webhook 无法直接传文件实体）
    """
    resolved_keyword = _resolve_keyword(keyword)
    pdf_path = Path(pdf_local_path).resolve()
    pdf_hint = pdf_path.as_posix() if len(str(pdf_path)) < 120 else str(pdf_path)

    body_parts: list[str] = []
    summary = markdown_to_lark_md((summary_md or "").strip())
    if summary:
        body_parts.append(summary)
    if audit_status.strip():
        body_parts.append(f"**审计状态**：{audit_status.strip()}")
    if md_local_path:
        body_parts.append(f"**Markdown 源文件**：`{Path(md_local_path).name}`")
    if not body_parts:
        body_parts.append("（暂无摘要，请打开本地 PDF 查看完整内容。）")

    lark_body = "\n\n".join(body_parts)
    footer_note = (
        f"📄 PDF 本地备份：{pdf_hint}\n"
        f"📨 完整报告正文将紧随其后以文本消息发送，无需打开 PDF。"
    )

    return send_interactive_card(
        title="📊 OpenClaw 自动化分析报告已生成",
        lark_md_content=lark_body,
        header_template=header_template,
        note=footer_note,
        webhook_url=webhook_url,
        secret=secret,
        keyword=resolved_keyword,
        timeout=timeout,
    )


def send_interactive_card(
    *,
    title: str,
    lark_md_content: str,
    header_template: str = "blue",
    note: str = "",
    webhook_url: str | None = None,
    secret: str | None = None,
    keyword: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> FeishuSendResult:
    """发送飞书消息卡片（标题与正文自动注入关键词，正文支持 lark_md）。"""
    resolved_keyword = _resolve_keyword(keyword)
    safe_title = _ensure_keyword_in_title(title, resolved_keyword)
    md = _ensure_keyword_in_text((lark_md_content or "").strip(), resolved_keyword)
    if not md:
        raise FeishuBotError("lark_md_content 不能为空")

    note_text = (note or "").strip()
    if note_text and resolved_keyword not in note_text:
        note_text = f"{note_text} · {resolved_keyword}"

    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": md}}
    ]
    if note_text:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": note_text}],
                },
            ]
        )

    url = _resolve_webhook_url(webhook_url)
    payload = {
        "msg_type": FeishuMessageType.INTERACTIVE.value,
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": safe_title},
                "template": header_template,
            },
            "elements": elements,
        },
    }
    result = _post_payload(
        url, payload, secret=secret, keyword=resolved_keyword, timeout=timeout
    )
    if not result.ok:
        raise FeishuBotError(result.message)
    return result


def markdown_to_lark_md(markdown_text: str) -> str:
    """将常见 Markdown 转为飞书 lark_md 兼容格式。"""
    text = (markdown_text or "").replace("\r\n", "\n").strip()
    if not text:
        return ""

    lines: list[str] = []
    in_table = False

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()

        if re.match(r"^#{1,6}\s+", line):
            in_table = False
            heading = re.sub(r"^#{1,6}\s+", "", line).strip()
            lines.append(f"**{heading}**")
            continue

        if re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", line):
            in_table = False
            match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", line)
            alt = match.group(1) if match else "图片"
            path = match.group(2) if match else ""
            lines.append(f"📎 {alt or '图表'}：`{Path(path).name}`")
            continue

        if re.fullmatch(r"-{3,}", line.strip()):
            in_table = False
            lines.append("")
            continue

        if "|" in line and re.search(r"\|.+\|", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", c or "---") for c in cells):
                continue
            if not in_table:
                in_table = True
            row = " · ".join(c for c in cells if c)
            if row:
                lines.append(f"- {row}")
            continue

        in_table = False
        lines.append(line)

    result = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def markdown_to_plain_text(markdown_text: str) -> str:
    """将 Markdown 转为飞书纯文本消息可读格式（去除大部分标记符号）。"""
    text = markdown_to_lark_md(markdown_text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_text_chunks(content: str, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> list[str]:
    """按段落切分纯文本，用于多条飞书 text 消息。"""
    return split_lark_md(content, max_chars=max_chars)


def split_lark_md(content: str, max_chars: int = DEFAULT_MAX_LARK_MD_CHARS) -> list[str]:
    """按段落边界切分长文本，避免超出飞书单条消息限制。"""
    text = (content or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in re.split(r"\n{2,}", text):
        block = paragraph.strip()
        if not block:
            continue
        extra = len(block) + (2 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append("\n\n".join(current))
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len += extra

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _extract_report_title(markdown_text: str, fallback: str = "分析报告") -> str:
    for line in markdown_text.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return fallback


def send_markdown_report(
    report_path: str | Path,
    *,
    webhook_url: str | None = None,
    secret: str | None = None,
    keyword: str | None = None,
    title: str | None = None,
    mode: str = "card",
    max_chars: int = DEFAULT_MAX_LARK_MD_CHARS,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> list[FeishuSendResult]:
    """读取本地 Markdown 报告并推送到飞书（自动分片 + 注入关键词）。"""
    path = Path(report_path)
    if not path.is_file():
        raise FeishuBotError(f"报告文件不存在: {path}")

    raw_md = path.read_text(encoding="utf-8").strip()
    if not raw_md:
        raise FeishuBotError(f"报告文件为空: {path}")

    resolved_keyword = _resolve_keyword(keyword)
    report_title = title or _extract_report_title(raw_md, fallback=path.stem)
    lark_md = markdown_to_lark_md(raw_md)
    chunks = split_lark_md(lark_md, max_chars=max_chars)
    if not chunks:
        raise FeishuBotError("报告内容转换后为空")

    results: list[FeishuSendResult] = []
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        chunk_title = report_title if total == 1 else f"{report_title}（{index}/{total}）"
        note = f"来源文件：{path.name}" if index == 1 else ""

        if mode == "post":
            result = send_post_message(
                chunk_title,
                [[{"tag": "text", "text": chunk}]],
                webhook_url=webhook_url,
                secret=secret,
                keyword=resolved_keyword,
                timeout=timeout,
            )
        else:
            result = send_interactive_card(
                title=chunk_title,
                lark_md_content=chunk,
                note=note,
                webhook_url=webhook_url,
                secret=secret,
                keyword=resolved_keyword,
                timeout=timeout,
            )
        results.append(result)

    return results


def send_report_fulltext(
    report_path: str | Path,
    *,
    webhook_url: str | None = None,
    secret: str | None = None,
    keyword: str | None = None,
    max_chars: int = DEFAULT_MAX_TEXT_CHARS,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> list[FeishuSendResult]:
    """
    将完整报告以纯文本形式分片推送到飞书群（无需打开 PDF）。

    先发一条提示，再按段落发送「报告正文 (i/n)」文本消息。
    """
    path = Path(report_path)
    if not path.is_file():
        raise FeishuBotError(f"报告文件不存在: {path}")

    raw_md = path.read_text(encoding="utf-8").strip()
    if not raw_md:
        raise FeishuBotError(f"报告文件为空: {path}")

    plain = markdown_to_plain_text(raw_md)
    chunks = split_text_chunks(plain, max_chars=max_chars)
    if not chunks:
        raise FeishuBotError("报告正文转换后为空")

    results: list[FeishuSendResult] = []
    total = len(chunks)

    intro = send_text_message(
        f"📨 竞品报告完整正文如下（共 {total} 条消息），可直接在群内阅读，无需打开 PDF。",
        webhook_url=webhook_url,
        secret=secret,
        keyword=keyword,
        timeout=timeout,
    )
    results.append(intro)

    for index, chunk in enumerate(chunks, start=1):
        body = f"——— 报告正文 ({index}/{total}) ———\n\n{chunk}"
        results.append(
            send_text_message(
                body,
                webhook_url=webhook_url,
                secret=secret,
                keyword=keyword,
                timeout=timeout,
            )
        )

    return results


class FeishuBotSkill:
    """飞书机器人 Skill 封装，供 Pipeline / 脚本直接调用。"""

    def __init__(
        self,
        webhook_url: str | None = None,
        secret: str | None = None,
        keyword: str | None = None,
        *,
        default_mode: str = "card",
        max_chars: int = DEFAULT_MAX_LARK_MD_CHARS,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.webhook_url = (webhook_url or os.getenv("FEISHU_WEBHOOK_URL", DEFAULT_WEBHOOK_URL)).strip()
        self.secret = (
            secret if secret is not None else os.getenv("FEISHU_WEBHOOK_SECRET", DEFAULT_WEBHOOK_SECRET)
        ).strip()
        self.keyword = _resolve_keyword(keyword)
        self.default_mode = default_mode
        self.max_chars = max_chars
        self.timeout = timeout

    def send_text(self, text: str) -> FeishuSendResult:
        return send_text_message(
            text,
            webhook_url=self.webhook_url or None,
            secret=self.secret or None,
            keyword=self.keyword,
            timeout=self.timeout,
        )

    def notify_report(
        self,
        report_path: str | Path,
        *,
        title: str | None = None,
        mode: str | None = None,
    ) -> list[FeishuSendResult]:
        return send_markdown_report(
            report_path,
            webhook_url=self.webhook_url or None,
            secret=self.secret or None,
            keyword=self.keyword,
            title=title,
            mode=mode or self.default_mode,
            max_chars=self.max_chars,
            timeout=self.timeout,
        )

    def notify_report_delivery(
        self,
        md_path: str | Path,
        pdf_path: str | Path,
        *,
        summary_md: str,
        audit_status: str = "",
    ) -> FeishuSendResult:
        """推送单条报告交付卡片（摘要 + PDF 本地路径）。"""
        return send_report_delivery_card(
            summary_md=summary_md,
            pdf_local_path=pdf_path,
            audit_status=audit_status,
            md_local_path=md_path,
            webhook_url=self.webhook_url or None,
            secret=self.secret or None,
            keyword=self.keyword,
            timeout=self.timeout,
        )

    def notify_report_fulltext(
        self,
        report_path: str | Path,
        *,
        max_chars: int = DEFAULT_MAX_TEXT_CHARS,
    ) -> list[FeishuSendResult]:
        """将完整报告正文以纯文本分片发送到飞书群。"""
        return send_report_fulltext(
            report_path,
            webhook_url=self.webhook_url or None,
            secret=self.secret or None,
            keyword=self.keyword,
            max_chars=max_chars,
            timeout=self.timeout,
        )


def send_test_message(
    webhook_url: str | None = None,
    keyword: str | None = None,
) -> FeishuSendResult:
    """发送一条测试消息，用于验证 Webhook 与关键词配置是否正确。"""
    return send_text_message(
        "OpenClaw Pipeline 飞书推送测试：配置正常，可接收竞品分析报告。",
        webhook_url=webhook_url,
        keyword=keyword,
    )


if __name__ == "__main__":
    from src.utils.config_loader import load_dotenv_file

    load_dotenv_file()
    print("[Feishu] 发送测试消息...")
    result = send_test_message()
    print(f"[Feishu] {result.message} (HTTP {result.status_code})")
