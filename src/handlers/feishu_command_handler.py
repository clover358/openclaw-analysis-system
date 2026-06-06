"""飞书指令路由：解析用户文本 → 触发 Pipeline → 异步回复。"""

from __future__ import annotations

import re
import threading
import traceback
from pathlib import Path
from typing import Any

from src.pipeline import run_automation_pipeline
from src.skills.feishu_bot_skill import markdown_to_lark_md, split_text_chunks
from src.skills.feishu_openapi_skill import (
    FeishuIncomingMessage,
    FeishuOpenApiError,
    send_file_to_chat,
    send_interactive_card_to_chat,
    send_text_to_chat,
    upload_file,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_DEFAULT_TRIGGER_PHRASES: tuple[str, ...] = (
    "给我发送竞品报告",
    "给我生成竞品报告",
    "生成竞品报告",
    "生成竞品分析报告",
    "竞品报告",
)


def _load_trigger_phrases() -> tuple[str, ...]:
    """从 config.yaml 读取触发词，失败时使用内置默认值。"""
    try:
        from src.utils.config_loader import load_app_config

        cfg = load_app_config()
        raw = (cfg.get("feishu_agent") or {}).get("trigger_phrases") or []
        phrases = tuple(str(p).strip() for p in raw if str(p).strip())
        return phrases or _DEFAULT_TRIGGER_PHRASES
    except Exception:
        return _DEFAULT_TRIGGER_PHRASES

DEFAULT_OUTPUT_MD = "data/processed/openrouter_llm_market_report_feishu.md"

DEFAULT_USER_REQUIREMENT = (
    "基于 OpenRouter API 中转站的真实调用数据，分析 OpenAI 主体在该平台的"
    "表现、主要竞品（Anthropic / Google / Meta / DeepSeek / xAI / Qwen 等）的格局，"
    "以及应用层与行业宏观趋势。"
)

_processed_message_ids: set[str] = set()
_lock = threading.Lock()


def should_trigger_pipeline(text: str) -> bool:
    """判断用户消息是否应触发报告生成 Pipeline。"""
    normalized = (text or "").strip()
    if not normalized:
        return False
    for phrase in _load_trigger_phrases():
        if phrase in normalized:
            return True
    return False


def _extract_executive_summary(markdown_text: str, max_chars: int = 500) -> str:
    text = (markdown_text or "").strip()
    if not text:
        return "（暂无摘要）"
    match = re.search(
        r"^#{1,3}\s*[^\n]*执行摘要[^\n]*\n+(.*?)(?=^#{1,3}\s|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    body = match.group(1).strip() if match else text
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "…"
    return body


def _dedupe_message(message_id: str) -> bool:
    """返回 True 表示首次处理；False 表示重复事件应跳过。"""
    with _lock:
        if message_id in _processed_message_ids:
            return False
        _processed_message_ids.add(message_id)
        if len(_processed_message_ids) > 2000:
            _processed_message_ids.clear()
        return True


def _run_pipeline_and_reply(chat_id: str) -> None:
    """后台线程：执行 Pipeline 并通过 Open API 回复用户。"""
    try:
        send_text_to_chat(
            chat_id,
            "🦞 已收到指令，OpenClaw 全链路 Pipeline 启动中（Collector → Analyst → Generator → Reviewer），请稍候…",
        )

        result = run_automation_pipeline(
            final_output_path=DEFAULT_OUTPUT_MD,
            user_requirement=DEFAULT_USER_REQUIREMENT,
        )

        md_path = result.final_report_path
        pdf_path = md_path.with_suffix(".pdf")
        report_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
        summary = markdown_to_lark_md(_extract_executive_summary(report_text))

        audit = "审计通过" if result.audit_result.get("is_passed") else "已采用 Reviewer 修正稿"
        pdf_hint = str(pdf_path) if pdf_path else str(md_path)

        send_interactive_card_to_chat(
            chat_id,
            title="📊 OpenClaw 自动化分析报告已生成",
            lark_md_content=(
                f"**大模型 API 竞品报告** 已生成完成。\n\n"
                f"{summary}\n\n"
                f"---\n\n"
                f"- **审计状态**：{audit}\n"
                f"- **Markdown**：`{md_path.name}`"
            ),
            header_template="orange",
            note=f"📄 完整版 PDF 报告已自动输出至本地：{pdf_hint}",
        )

        if pdf_path and pdf_path.is_file():
            try:
                file_key = upload_file(pdf_path, file_type="pdf")
                send_file_to_chat(chat_id, file_key)
            except FeishuOpenApiError as exc:
                send_text_to_chat(chat_id, f"PDF 已生成但上传飞书失败：{exc}。请从本地打开：{pdf_path}")

        if report_text.strip():
            chunks = split_text_chunks(report_text)
            total = len(chunks)
            if total:
                send_text_to_chat(chat_id, f"📄 完整报告正文共 {total} 条，即将逐条发送…")
                for index, chunk in enumerate(chunks, start=1):
                    send_text_to_chat(
                        chat_id,
                        f"——— 报告正文 ({index}/{total}) ———\n\n{chunk}",
                    )

    except Exception as exc:
        err = traceback.format_exc()
        print(f"[FeishuHandler] Pipeline 失败: {err}")
        try:
            send_text_to_chat(chat_id, f"❌ 报告生成失败：{type(exc).__name__}: {exc}")
        except Exception:
            pass


def handle_incoming_message(message: FeishuIncomingMessage) -> dict[str, Any]:
    """
    处理一条飞书入站消息。

    Returns:
        处理结果摘要（供日志使用）。
    """
    if not _dedupe_message(message.message_id):
        return {"action": "duplicate", "message_id": message.message_id}

    text = message.text
    print(f"[FeishuHandler] 收到消息 chat={message.chat_id} text={text!r}")

    if should_trigger_pipeline(text):
        thread = threading.Thread(
            target=_run_pipeline_and_reply,
            args=(message.chat_id,),
            daemon=True,
            name=f"pipeline-{message.message_id[:8]}",
        )
        thread.start()
        return {"action": "pipeline_started", "message_id": message.message_id, "chat_id": message.chat_id}

    send_text_to_chat(
        message.chat_id,
        "你好，我是 OpenClaw 飞书 Agent。\n"
        "发送「给我发送竞品报告」即可触发全链路自动化分析与报告生成。",
    )
    return {"action": "help_sent", "message_id": message.message_id}
