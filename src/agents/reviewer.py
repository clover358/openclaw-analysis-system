"""Reviewer-Agent：报告闭环核查与自动修正。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.agents.analyst import DEFAULT_CONFIG_PATH, PROJECT_ROOT, resolve_openai_settings
from src.utils.config_loader import ConfigError, load_app_config


class ReviewAuditSchema(BaseModel):
    """审计结果结构化输出（LangChain Structured Output）。"""

    is_passed: bool = Field(description="报告是否通过合规审计：true 通过，false 不通过")
    review_opinions: str = Field(
        description="具体的修改意见；若通过则说明赞同理由与亮点"
    )
    revised_content: str = Field(
        description="若未通过则为修正后的完整报告 Markdown；若通过可为原文或微调版"
    )


class ReviewerAgent:
    """极度挑剔的合规审计师：比对报告初稿与原始数据，输出结构化审计结论。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self.reviewer_cfg = self.config.get("reviewer", {})
        self.openai_settings = resolve_openai_settings(self.config, "reviewer")
        self.llm = self._init_llm()
        self.structured_llm = self.llm.with_structured_output(ReviewAuditSchema)

    def _load_config(self) -> dict[str, Any]:
        try:
            if self.config_path.resolve() == DEFAULT_CONFIG_PATH.resolve():
                return load_app_config()
            return load_app_config(config_path=self.config_path)
        except ConfigError:
            raise
        except Exception as exc:
            raise ValueError(f"加载配置文件失败: {exc}") from exc

    def _init_llm(self) -> ChatOpenAI:
        openai_kwargs = self.openai_settings["openai_kwargs"]
        if not openai_kwargs.get("api_key"):
            raise RuntimeError(
                "未配置 API Key。请在 config/config.yaml 的 openai.api_key 中填写。"
            )

        reviewer_llm = self.reviewer_cfg.get("llm", {})
        temperature = float(
            reviewer_llm.get("temperature", self.openai_settings["temperature"])
        )
        model = (
            (reviewer_llm.get("model") or "").strip()
            or self.openai_settings["chat_model"]
        )

        try:
            return ChatOpenAI(
                model=model,
                temperature=temperature,
                **openai_kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"初始化 Reviewer ChatOpenAI 失败: {exc}") from exc

    @staticmethod
    def _build_audit_messages(report_content: str, raw_data_summary: str) -> list:
        system_prompt = (
            "你是一名极度挑剔的合规审计师，负责审核商业分析报告。\n"
            "你必须将【报告初稿】与【原始数据摘要】进行逐条比对，严厉核查：\n"
            "1. 是否存在捏造数字、虚构事实（幻觉）；\n"
            "2. 前后表述是否矛盾；\n"
            "3. 错别字、语病及专业术语误用；\n"
            "4. 结论是否脱离原始数据支撑。\n"
            "审计标准：只有证据充分、数据可追溯、逻辑自洽的报告才能判定 is_passed=true。\n"
            "若 is_passed=false，必须在 revised_content 中给出修正后的完整 Markdown 报告全文。\n"
            "若 is_passed=true，review_opinions 说明通过理由，revised_content 可输出优化后的完整报告。"
        )

        user_prompt = (
            f"## 报告初稿\n{report_content.strip()}\n\n"
            f"## 原始数据摘要（唯一可信事实来源）\n{raw_data_summary.strip()}\n\n"
            "## 任务\n"
            "请完成审计，并严格按 JSON 结构返回 is_passed、review_opinions、revised_content。"
        )

        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

    @staticmethod
    def _parse_fallback_json(text: str) -> dict[str, Any]:
        """Structured Output 失败时，尝试从模型文本中提取 JSON。"""
        try:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise ValueError("未找到 JSON 块")
            data = json.loads(match.group())
            return ReviewAuditSchema.model_validate(data).model_dump()
        except Exception as exc:
            raise ValueError(f"无法解析审计 JSON: {exc}") from exc

    def audit_report(self, report_content: str, raw_data_summary: str) -> dict:
        """
        深度比对报告与原始数据，返回结构化审计结果字典。

        Returns:
            {
              "is_passed": bool,
              "review_opinions": str,
              "revised_content": str,
            }
        """
        try:
            report = (report_content or "").strip()
            raw_summary = (raw_data_summary or "").strip()

            if not report:
                raise ValueError("report_content 不能为空")
            if not raw_summary:
                raise ValueError("raw_data_summary 不能为空")

            messages = self._build_audit_messages(report, raw_summary)

            try:
                audit: ReviewAuditSchema = self.structured_llm.invoke(messages)
                return audit.model_dump()
            except Exception as structured_exc:
                try:
                    raw_response = self.llm.invoke(messages)
                    raw_text = getattr(raw_response, "content", str(raw_response))
                    return self._parse_fallback_json(raw_text)
                except Exception as fallback_exc:
                    raise RuntimeError(
                        f"结构化审计失败: {structured_exc}; 回退解析亦失败: {fallback_exc}"
                    ) from fallback_exc

        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"报告审计失败: {exc}\n"
                "请检查 DeepSeek API 配置与网络状态。"
            ) from exc
