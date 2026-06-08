"""飞书指令路由：解析用户文本 → 触发 Pipeline → 异步回复。"""

from __future__ import annotations

import re
import threading
import traceback
from pathlib import Path
from typing import Any

from src.pipeline import run_automation_pipeline
from src.skills.feishu_bot_skill import markdown_to_lark_md
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


def _extract_report_outline(markdown_text: str) -> str:
    text = (markdown_text or "").strip()
    if not text:
        return "- 暂无可展示提纲"
    headings = re.findall(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    cleaned = []
    for heading in headings:
        title = re.sub(r"^[一二三四五六七八九十\d、.．\s-]+", "", heading).strip()
        if title and title not in cleaned:
            cleaned.append(title)
    if not cleaned:
        return "- 执行摘要\n- 主流 API 平台使用表现\n- 行业趋势与竞品分析\n- 战略建议与风险提示"
    return "\n".join(f"- {title}" for title in cleaned[:6])


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
        pdf_path = result.final_pdf_path
        report_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
        outline = markdown_to_lark_md(_extract_report_outline(report_text))

        audit = "审计通过" if result.audit_result.get("is_passed") else "已采用 Reviewer 修正稿"

        send_interactive_card_to_chat(
            chat_id,
            title="📊 OpenClaw 自动化分析报告已生成",
            lark_md_content=(
                f"**大模型 API 竞品报告** 已生成完成。\n\n"
                f"**报告提纲**\n{outline}\n\n"
                f"---\n\n"
                f"- **审计状态**：{audit}\n"
                f"- **Markdown**：`{md_path.name}`\n"
                f"- **PDF**：`{Path(pdf_path).name}`"
            ),
            header_template="orange",
            note="📄 完整版 PDF 报告将作为文件发送到群聊。",
        )

        if pdf_path and pdf_path.is_file():
            try:
                file_key = upload_file(pdf_path, file_type="pdf")
                send_file_to_chat(chat_id, file_key)
            except FeishuOpenApiError as exc:
                send_text_to_chat(chat_id, f"PDF 已生成但上传飞书失败：{exc}。请从本地打开：{pdf_path}")
        else:
            send_text_to_chat(chat_id, f"PDF 生成失败，未发送正文分片。Markdown 已保存至：{md_path}")

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
