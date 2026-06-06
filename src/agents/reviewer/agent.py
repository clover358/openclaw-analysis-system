"""Reviewer-Agent：报告闭环核查与自动修正。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from src.agents.analyst.agent import DEFAULT_CONFIG_PATH, PROJECT_ROOT, resolve_openai_settings
from src.utils.config_loader import ConfigError, load_app_config

_ = PROJECT_ROOT


class AuditResult(BaseModel):
    """审计结果（Prompt 强约束 + json_object 解析）。"""

    is_passed: bool = Field(
        description="报告是否合规通过。无幻觉、无年份矛盾、无数据冲突填 true，否则填 false"
    )
    hallucinations: list[str] = Field(
        default_factory=list,
        description="发现的幻觉、数据矛盾或年份错位列表",
    )
    suggestions: str = Field(description="具体的修改意见和修正指导")
    revised_content: str = Field(
        default="",
        description="未通过时输出修正后的完整 Markdown 报告；通过时可留空",
    )

    @property
    def review_opinions(self) -> str:
        """兼容旧字段：合并幻觉列表与修改意见。"""
        parts: list[str] = []
        if self.hallucinations:
            parts.append("【发现的问题】")
            parts.extend(f"- {item}" for item in self.hallucinations)
        if self.suggestions.strip():
            parts.append("【修改意见】")
            parts.append(self.suggestions.strip())
        return "\n".join(parts).strip()


ReviewAuditSchema = AuditResult

_JSON_FENCE_PATTERN = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_JSON_BLOB_PATTERN = re.compile(r"(\{.*\})", re.DOTALL)

_FALLBACK_AUDIT = AuditResult(
    is_passed=True,
    hallucinations=[],
    suggestions="接口异常激活防御放行",
    revised_content="",
)


class ReviewerAgent:
    """极度挑剔的合规审计师：比对报告初稿与原始数据，输出结构化审计结论。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self.reviewer_cfg = self.config.get("reviewer", {})
        self.openai_settings = resolve_openai_settings(self.config, "reviewer")
        self.llm = self._init_llm()

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
        configured_temp = reviewer_llm.get("temperature")
        temperature = float(configured_temp if configured_temp is not None else 0.1)
        model = (
            (reviewer_llm.get("model") or "").strip()
            or self.openai_settings["chat_model"]
        )

        try:
            return ChatOpenAI(
                model=model,
                temperature=temperature,
                model_kwargs={"response_format": {"type": "json_object"}},
                **openai_kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"初始化 Reviewer ChatOpenAI 失败: {exc}") from exc

    @staticmethod
    def _build_audit_messages(report_content: str, raw_data_summary: str) -> list:
        system_prompt = (
            "你是一名极度挑剔的合规审计师，负责审核基于 OpenRouter 真实调用数据"
            "撰写的 LLM 厂商商业分析报告。\n"
            "你必须将【报告初稿】与【原始数据摘要】进行逐条比对，严厉核查：\n"
            "1. 是否存在捏造数字、虚构事实（幻觉）；\n"
            "2. 前后表述是否矛盾、年份是否错位；\n"
            "3. 错别字、语病及专业术语误用；\n"
            "4. 结论是否脱离原始数据支撑；\n"
            "5. 主体定义是否正确（主产品 = OpenAI 旗下所有模型；其余厂商 = 竞品）。\n\n"
            "【年份一致性强约束】\n"
            "报告中出现的所有四位年份（如 2024、2025、2026）必须能在【原始数据摘要】"
            "里找到来源。若报告写了「2025 年度」但原始数据全是 2024 年的日期点，"
            "必须判定 is_passed=false 并在 hallucinations 中明确指出年份错位。\n\n"
            "【图片引用保留强约束】\n"
            "报告初稿中可能包含若干 Markdown 图片引用，形如：\n"
            "    > **图 N：标题**\n"
            "    ![标题](report_xxxx_chartNN.png)\n"
            "若需要输出 revised_content（即 is_passed=false 时），你必须把所有图片引用"
            "（包括前一行的 `> **图 N：...**` 题注与紧邻的 `![](xxx.png)` 一行）"
            "原样、逐字、连同周围空行保留在原所属章节内，严禁删除、改写、合并、替换为占位符，"
            "也不得调整图片文件名。这一条违反将直接判定本次修订作废。\n\n"
            "【输出硬性要求】\n"
            "你必须严格按照要求的 JSON 格式输出，用 ```json 和 ``` 代码段包裹。\n"
            "只允许输出一个 JSON 对象，禁止输出 Markdown 报告正文或其他解释性文字。\n"
            "JSON 字段必须为：\n"
            "{\n"
            '  "is_passed": true 或 false,\n'
            '  "hallucinations": ["字符串数组，列举幻觉/矛盾/年份错位"],\n'
            '  "suggestions": "具体修改意见和修正指导",\n'
            '  "revised_content": "若 is_passed 为 false，输出修正后的完整 Markdown 报告；若 true 则填空字符串 ""\n'
            "}"
        )

        user_prompt = (
            f"## 报告初稿\n{report_content.strip()}\n\n"
            f"## 原始数据摘要（唯一可信事实来源）\n{raw_data_summary.strip()}\n\n"
            "## 任务\n"
            "请完成审计，并严格按上述 JSON 结构返回结果。"
        )

        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """多级容错 JSON 解析：整段解析 → ```json 块 → 首尾大括号盲捞。"""
        if not text or not str(text).strip():
            raise ValueError("模型返回内容为空，无法解析 JSON")

        raw = str(text).strip()
        errors: list[str] = []

        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
            errors.append("顶层 JSON 不是对象")
        except json.JSONDecodeError as exc:
            errors.append(f"整段解析失败: {exc}")

        fence_match = _JSON_FENCE_PATTERN.search(raw)
        if fence_match:
            try:
                payload = json.loads(fence_match.group(1))
                if isinstance(payload, dict):
                    return payload
                errors.append("json 代码块顶层不是对象")
            except json.JSONDecodeError as exc:
                errors.append(f"json 代码块解析失败: {exc}")

        blob_match = _JSON_BLOB_PATTERN.search(raw)
        if blob_match:
            try:
                payload = json.loads(blob_match.group(1))
                if isinstance(payload, dict):
                    return payload
                errors.append("大括号片段顶层不是对象")
            except json.JSONDecodeError as exc:
                errors.append(f"大括号片段解析失败: {exc}")

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(raw[start : end + 1])
                if isinstance(payload, dict):
                    return payload
                errors.append("首尾大括号片段顶层不是对象")
            except json.JSONDecodeError as exc:
                errors.append(f"首尾大括号片段解析失败: {exc}")

        raise ValueError(
            "无法从模型响应中解析有效 JSON。\n"
            + "\n".join(f"- {item}" for item in errors)
        )

    @staticmethod
    def _normalize_audit_payload(data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(data)

        if "suggestions" not in normalized and "review_opinions" in normalized:
            normalized["suggestions"] = normalized.pop("review_opinions")

        if "hallucinations" not in normalized:
            normalized["hallucinations"] = []
        elif not isinstance(normalized["hallucinations"], list):
            normalized["hallucinations"] = [str(normalized["hallucinations"])]

        if "revised_content" not in normalized:
            normalized["revised_content"] = ""

        return normalized

    def _parse_audit_response(self, raw_text: str) -> AuditResult:
        data = self._extract_json(raw_text)
        normalized = self._normalize_audit_payload(data)
        try:
            return AuditResult.model_validate(normalized)
        except ValidationError as exc:
            raise ValueError(f"JSON 字段校验失败: {exc}") from exc

    def audit_report(self, report_content: str, raw_data_summary: str) -> AuditResult:
        """深度比对报告与原始数据，返回 AuditResult 审计结论。"""
        try:
            report = (report_content or "").strip()
            raw_summary = (raw_data_summary or "").strip()

            if not report:
                raise ValueError("report_content 不能为空")
            if not raw_summary:
                raise ValueError("raw_data_summary 不能为空")

            messages = self._build_audit_messages(report, raw_summary)
            response = self.llm.invoke(messages)
            raw_text = getattr(response, "content", None) or str(response)

            return self._parse_audit_response(raw_text)

        except ValueError:
            raise
        except Exception as exc:
            print(f"[Reviewer] WARNING: 审计接口异常，激活防御放行 -> {type(exc).__name__}: {exc}")
            return _FALLBACK_AUDIT.model_copy()

    def run(self, report_content: str, raw_data_summary: str) -> dict[str, Any]:
        audit = self.audit_report(report_content, raw_data_summary)
        return {
            "is_passed": audit.is_passed,
            "review_opinions": audit.review_opinions,
            "revised_content": audit.revised_content,
            "hallucinations": audit.hallucinations,
            "suggestions": audit.suggestions,
        }
