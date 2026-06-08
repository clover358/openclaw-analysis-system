"""Analyst-Agent：多源 API 竞品结构化预处理 + RAG 知识库 + 商业分析（双模型混用）。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.utils.config_loader import ConfigError, load_app_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# 三维检索维度：主体 OpenAI / 竞品 / 行业趋势（大模型 API 调用语境）
RETRIEVAL_KEYS = ("openai", "competitor", "industry")
RETRIEVAL_LABELS = {
    "openai": "【主体·OpenAI API 使用】",
    "competitor": "【竞品·Google / Anthropic / DeepSeek 等 API】",
    "industry": "【行业·大模型 API 整体趋势】",
}

# 与 Generator DEFAULT_REPORT_SECTIONS 严格对齐的章节标题
REPORT_SECTION_HEADINGS = (
    "一、执行摘要",
    "二、主流 API 平台使用表现",
    "三、大模型 API 行业趋势研判",
    "四、竞品 API 威胁与机会",
    "五、战略建议",
    "六、风险提示",
)

DEFAULT_DATA_PATHS = {
    "merged_rankings": "data/merged_rankings.json",
    "market_share": "data/Market Share.json",
    "top_model": "data/Top Model.json",
    "day_leaderboard": "data/day_leaderboard.json",
    "week_leaderboard": "data/week_leaderboard.json",
    "month_leaderboard": "data/month_leaderboard.json",
    "excel_output": "data/processed/openrouter_rankings.xlsx",
    "ai_index_pdf": "data/raw/AI_Index_Report.pdf",
}

LEADERBOARD_TOP_N = 50
TIMESERIES_TOP_N = 15
ZHIPU_EMBEDDING_CHUNK_SIZE = 32

_PROVIDER_ALIASES = {
    "openai": "OpenAI",
    "google": "Google",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "meta-llama": "Meta",
    "mistralai": "Mistral",
    "qwen": "Qwen",
    "x-ai": "xAI",
    "moonshotai": "Moonshot",
    "z-ai": "Z.ai",
    "openrouter": "OpenRouter",
}


def resolve_openai_settings(
    config: dict[str, Any],
    section_name: str = "analyst",
) -> dict[str, Any]:
    """解析 DeepSeek（openai 段）Chat 配置，供分析与审计使用。"""
    global_cfg = config.get("openai") or {}
    section_cfg = (config.get(section_name) or {}).get("openai") or {}

    api_key = (section_cfg.get("api_key") or global_cfg.get("api_key") or "").strip()
    if not api_key:
        env_key = section_cfg.get("api_key_env") or global_cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.getenv(env_key, "").strip()

    base_url = (section_cfg.get("base_url") or global_cfg.get("base_url") or "").strip()
    if not base_url:
        env_base = section_cfg.get("base_url_env") or global_cfg.get("base_url_env", "OPENAI_API_BASE")
        base_url = os.getenv(env_base, "").strip()

    section_llm = (config.get(section_name) or {}).get("llm") or {}
    chat_model = (
        (section_cfg.get("model") or "").strip()
        or (section_llm.get("model") or "").strip()
        or (global_cfg.get("model") or "").strip()
        or "deepseek-chat"
    )

    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    return {
        "openai_kwargs": kwargs,
        "chat_model": chat_model,
        "temperature": float(section_llm.get("temperature", 0.3)),
    }


def resolve_zhipu_embedding_settings(config: dict[str, Any]) -> dict[str, Any]:
    """严格解析智谱 zhipu 段 Embedding 配置（RAG 向量化专用）。"""
    zhipu_cfg = config.get("zhipu") or {}

    api_key = (zhipu_cfg.get("api_key") or "").strip()
    if not api_key:
        env_key = zhipu_cfg.get("api_key_env", "ZHIPU_API_KEY")
        api_key = os.getenv(env_key, "").strip()

    base_url = (zhipu_cfg.get("base_url") or "").strip()
    if not base_url:
        env_base = zhipu_cfg.get("base_url_env", "ZHIPU_API_BASE")
        base_url = os.getenv(env_base, "").strip()

    embedding_model = (zhipu_cfg.get("embedding_model") or "").strip() or "embedding-3"

    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    return {
        "openai_kwargs": kwargs,
        "embedding_model": embedding_model,
    }


def _resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _sanitize_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\[\]\:\*\?\/\\]", "_", name.strip())
    return cleaned[:31] or "Sheet"


def _format_tokens(value: Any) -> str:
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if num >= 1_000_000_000_000:
        return f"{num / 1_000_000_000_000:.2f}T"
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    return f"{int(num):,}"


def _provider_label(key: str) -> str:
    normalized = (key or "").strip().lower()
    return _PROVIDER_ALIASES.get(normalized, key)


def _extract_timeseries_blocks(payload: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    """从 OpenRouter 排行榜类 JSON 提取时间序列块。"""
    blocks: list[tuple[str, list[dict[str, Any]]]] = []
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict) and "x" in payload[0]:
            blocks.append(("series", payload))
        return blocks
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            data = payload["data"]
            if data and isinstance(data[0], dict) and "x" in data[0]:
                blocks.append(("data", data))
        for key, value in payload.items():
            if key == "data":
                continue
            if isinstance(value, list) and value and isinstance(value[0], dict) and "x" in value[0]:
                blocks.append((str(key), value))
    return blocks


def _timeseries_to_dataframes(
    series: list[dict[str, Any]],
    *,
    entity_col: str = "provider",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """将 {x, ys} 时间序列转为趋势长表与最新快照表。"""
    trend_rows: list[dict[str, Any]] = []
    for point in series:
        period = str(point.get("x", ""))
        ys: dict[str, Any] = point.get("ys") or {}
        if not period or not ys:
            continue
        for name, tokens in ys.items():
            try:
                trend_rows.append(
                    {
                        "period": period,
                        entity_col: str(name),
                        "tokens": float(tokens or 0),
                    }
                )
            except (TypeError, ValueError):
                continue

    if not trend_rows:
        return pd.DataFrame(), pd.DataFrame()

    trend_df = pd.DataFrame(trend_rows)
    latest_period = trend_df["period"].max()
    snapshot_df = (
        trend_df[trend_df["period"] == latest_period]
        .sort_values("tokens", ascending=False)
        .reset_index(drop=True)
    )
    snapshot_df["rank"] = range(1, len(snapshot_df) + 1)
    snapshot_df["share_pct"] = (
        snapshot_df["tokens"] / snapshot_df["tokens"].sum() * 100
    ).round(2)
    return trend_df, snapshot_df


def _leaderboard_to_dataframe(rows: list[dict[str, Any]], *, top_n: int = LEADERBOARD_TOP_N) -> pd.DataFrame:
    """将 day/week/month leaderboard 转为可分析表格。"""
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for col in ("total_prompt_tokens", "total_completion_tokens", "count"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    sort_col = "total_prompt_tokens" if "total_prompt_tokens" in df.columns else None
    if sort_col:
        if "date" in df.columns:
            latest_date = df["date"].max()
            df = df[df["date"] == latest_date]
        df = df.sort_values(sort_col, ascending=False).head(top_n)

    df = df.reset_index(drop=True)
    if sort_col:
        df["rank"] = range(1, len(df) + 1)
    return df


def _load_json_file(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {path}") from exc
    except OSError as exc:
        raise OSError(f"读取文件失败: {path}") from exc


def _extract_pdf_text(pdf_path: Path) -> str:
    """提取 AI Index 等行业报告 PDF 文本（文件缺失或解析失败时返回空字符串）。"""
    if not pdf_path.is_file():
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        pages: list[str] = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
        return "\n\n".join(pages).strip()
    except Exception:
        return ""


def _summarize_timeseries_sheet(sheet_name: str, snapshot_df: pd.DataFrame, trend_df: pd.DataFrame) -> str:
    if snapshot_df.empty:
        return f"【分表摘要】{sheet_name}\n（无有效时间序列数据）"

    entity_col = "provider" if "provider" in snapshot_df.columns else "model"
    latest_period = str(snapshot_df["period"].iloc[0]) if "period" in snapshot_df.columns else "最新周期"
    if "period" in trend_df.columns and not trend_df.empty:
        latest_period = str(trend_df["period"].max())

    lines = [
        f"【分表摘要】{sheet_name}",
        f"- 数据类型: OpenRouter API Token 调用量",
        f"- 最新周期: {latest_period}",
        f"- Top {min(TIMESERIES_TOP_N, len(snapshot_df))} 排名:",
    ]
    for _, row in snapshot_df.head(TIMESERIES_TOP_N).iterrows():
        entity = str(row.get(entity_col, ""))
        tokens = _format_tokens(row.get("tokens", 0))
        share = row.get("share_pct", "")
        share_text = f", 份额 {share}%" if share != "" else ""
        lines.append(f"  {int(row.get('rank', 0))}. {entity}: {tokens} tokens{share_text}")

    if "period" in trend_df.columns and trend_df["period"].nunique() >= 2:
        periods = sorted(trend_df["period"].unique())
        first_period, last_period = periods[0], periods[-1]
        lines.append(f"- 时间跨度: {first_period} → {last_period}")

        if entity_col == "provider":
            for provider in ("openai", "google", "anthropic", "deepseek"):
                sub = trend_df[trend_df[entity_col].str.lower() == provider]
                if len(sub) >= 2:
                    start_val = float(sub.iloc[0]["tokens"])
                    end_val = float(sub.iloc[-1]["tokens"])
                    if start_val > 0:
                        change = (end_val - start_val) / start_val * 100
                        lines.append(
                            f"  · {_provider_label(provider)}: "
                            f"{_format_tokens(start_val)} → {_format_tokens(end_val)} ({change:+.1f}%)"
                        )

    return "\n".join(lines)


def _summarize_leaderboard_sheet(sheet_name: str, df: pd.DataFrame) -> str:
    if df.empty:
        return f"【分表摘要】{sheet_name}\n（无有效榜单数据）"

    lines = [
        f"【分表摘要】{sheet_name}",
        f"- 数据类型: OpenRouter 模型 API 调用榜单",
        f"- 记录数: {len(df)}",
    ]
    if "date" in df.columns and not df["date"].isna().all():
        lines.append(f"- 榜单日期: {df['date'].iloc[0]}")

    model_col = "model_permaslug" if "model_permaslug" in df.columns else None
    token_col = "total_prompt_tokens" if "total_prompt_tokens" in df.columns else None
    if model_col and token_col:
        lines.append(f"- Top {min(10, len(df))} 模型:")
        for _, row in df.head(10).iterrows():
            lines.append(
                f"  {int(row.get('rank', 0))}. {row[model_col]}: "
                f"{_format_tokens(row[token_col])} prompt tokens"
            )
    return "\n".join(lines)


def _build_data_cards(sheet_summaries: dict[str, str], snapshot_tables: dict[str, pd.DataFrame]) -> str:
    """基于各表快照生成 API 竞品数据卡片。"""
    lines = [
        "【数据卡片】2025 大模型 API 竞品数据概览",
        "- 数据来源: OpenRouter 多维度 API 调用统计（Token 调用量）",
        "- 分析对象: 主流大模型 API 提供商（OpenAI、Google、Anthropic、DeepSeek 等）",
        "",
    ]

    market_df = snapshot_tables.get("Market Share")
    if market_df is not None and not market_df.empty:
        top3 = market_df.head(3)
        lines.append("## API 厂商市场份额（最新周期）")
        for _, row in top3.iterrows():
            provider = _provider_label(str(row.get("provider", "")))
            lines.append(
                f"- #{int(row['rank'])} {provider}: {_format_tokens(row['tokens'])} tokens "
                f"({row.get('share_pct', 0)}%)"
            )
        openai_row = market_df[market_df["provider"].str.lower() == "openai"]
        if not openai_row.empty:
            row = openai_row.iloc[0]
            lines.append(
                f"- OpenAI（主体）排名 #{int(row['rank'])}，份额 {row.get('share_pct', 0)}%，"
                f"调用量 {_format_tokens(row['tokens'])}"
            )
        lines.append("")

    for sheet_name, df in snapshot_tables.items():
        if sheet_name == "Market Share" or df.empty:
            continue
        if "model" in df.columns:
            lines.append(f"## {sheet_name} 热门 API 模型 Top3")
            for _, row in df.head(3).iterrows():
                lines.append(
                    f"- {row.get('model', '')}: {_format_tokens(row.get('tokens', 0))} tokens"
                )
            lines.append("")

    lines.append("## 分表索引")
    for name in sheet_summaries:
        lines.append(f"- {name}")
    return "\n".join(lines).strip()


@dataclass
class PreprocessResult:
    """结构化预处理产物。"""

    excel_path: Path | None = None
    data_cards: str = ""
    sheet_summaries: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """商业分析输出与 Prompt 骨架。"""

    system_prompt: str
    user_prompt: str
    retrieved_contexts: dict[str, list[str]] = field(default_factory=dict)
    analysis: str | None = None
    preprocess: PreprocessResult | None = None


class AnalystAgent:
    """双模型混用：智谱 Embedding（RAG）+ DeepSeek Chat（API 竞品分析）。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self.analyst_cfg = self.config.get("analyst", {}) or {}
        self.data_cfg = {**DEFAULT_DATA_PATHS, **(self.analyst_cfg.get("data") or {})}

        self.zhipu_settings = resolve_zhipu_embedding_settings(self.config)
        self.openai_settings = resolve_openai_settings(self.config, "analyst")

        self.embeddings = self._init_embeddings()
        self.llm = self._init_llm()
        self.text_splitter = self._init_text_splitter()

        self.vectorstore: FAISS | None = None
        self._top_k = int(self.analyst_cfg.get("rag", {}).get("top_k", 3))
        self._preprocess_result: PreprocessResult | None = None

    def _load_config(self) -> dict[str, Any]:
        try:
            if self.config_path.resolve() == DEFAULT_CONFIG_PATH.resolve():
                return load_app_config()
            return load_app_config(config_path=self.config_path)
        except ConfigError:
            raise
        except Exception as exc:
            raise ValueError(f"加载配置文件失败: {exc}") from exc

    def _init_embeddings(self) -> OpenAIEmbeddings:
        debug_cfg = self.analyst_cfg.get("debug", {}) or {}
        if debug_cfg.get("use_fake_embeddings", False):
            raise RuntimeError(
                "analyst.debug.use_fake_embeddings 已为 true，但当前要求使用智谱在线向量服务。"
                "请在 config/config.yaml 中将其设为 false。"
            )

        zhipu_kwargs = self.zhipu_settings["openai_kwargs"]
        if not zhipu_kwargs.get("api_key"):
            raise RuntimeError(
                "未配置智谱 API Key。请在 config/config.yaml 的 zhipu.api_key 中填写，"
                "或设置环境变量 ZHIPU_API_KEY。"
            )
        if not zhipu_kwargs.get("base_url"):
            raise RuntimeError("未配置智谱 base_url。请在 config/config.yaml 的 zhipu.base_url 中填写。")

        model = self.zhipu_settings["embedding_model"]
        zhipu_cfg = self.config.get("zhipu") or {}
        batch_size = int(zhipu_cfg.get("embedding_batch_size", ZHIPU_EMBEDDING_CHUNK_SIZE))
        if not 1 <= batch_size <= 64:
            raise ValueError(f"zhipu.embedding_batch_size 须在 1~64 之间，当前为: {batch_size}")

        try:
            return OpenAIEmbeddings(
                model=model,
                check_embedding_ctx_length=False,
                chunk_size=batch_size,
                **zhipu_kwargs,
            )
        except Exception as exc:
            raise RuntimeError(
                f"初始化智谱 Embedding 失败（model={model}, base_url={zhipu_kwargs.get('base_url')}）: {exc}\n"
                "请确认 zhipu.api_key、zhipu.base_url、zhipu.embedding_model 配置正确。"
            ) from exc

    def _init_llm(self) -> ChatOpenAI:
        openai_kwargs = self.openai_settings["openai_kwargs"]
        if not openai_kwargs.get("api_key"):
            raise RuntimeError(
                "未配置 DeepSeek API Key。请在 config/config.yaml 的 openai.api_key 中填写，"
                "或设置环境变量 OPENAI_API_KEY。"
            )

        model = self.openai_settings["chat_model"]
        try:
            return ChatOpenAI(
                model=model,
                temperature=self.openai_settings["temperature"],
                **openai_kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"初始化 DeepSeek ChatOpenAI 失败（model={model}）: {exc}") from exc

    def _init_text_splitter(self) -> RecursiveCharacterTextSplitter:
        rag_cfg = self.analyst_cfg.get("rag", {}) or {}
        return RecursiveCharacterTextSplitter(
            chunk_size=int(rag_cfg.get("chunk_size", 500)),
            chunk_overlap=int(rag_cfg.get("chunk_overlap", 50)),
        )

    def preprocess(self) -> PreprocessResult:
        """
        主动从磁盘读取多源 API JSON，生成 Excel、数据卡片与分表摘要。

        核心数据源：merged_rankings.json、Market Share.json、Top Model.json、leaderboard。
        """
        result = PreprocessResult()
        sheets: dict[str, pd.DataFrame] = {}
        snapshot_tables: dict[str, pd.DataFrame] = {}
        merged_payload: dict[str, Any] | None = None

        merged_path = _resolve_path(self.data_cfg["merged_rankings"])
        try:
            merged_raw = _load_json_file(merged_path)
            if isinstance(merged_raw, dict):
                merged_payload = merged_raw
                print(f"[Analyst] 已加载合并数据: {merged_path.name}")
            else:
                result.warnings.append(f"merged_rankings.json 根节点非对象: {merged_path}")
        except Exception as exc:
            raise RuntimeError(f"无法加载核心数据 merged_rankings.json: {exc}") from exc

        def _ingest_timeseries(name: str, payload: Any, *, entity_col: str = "provider") -> None:
            blocks = _extract_timeseries_blocks(payload)
            if not blocks:
                result.warnings.append(f"{name}: 未找到可解析的时间序列")
                return
            for block_name, series in blocks:
                sheet_key = name if block_name in {"series", "data"} else f"{name}_{block_name}"
                trend_df, snapshot_df = _timeseries_to_dataframes(series, entity_col=entity_col)
                if trend_df.empty:
                    continue
                sheets[f"{sheet_key}_Trend"] = trend_df
                sheets[f"{sheet_key}_Latest"] = snapshot_df
                snapshot_tables[sheet_key] = snapshot_df
                result.sheet_summaries[sheet_key] = _summarize_timeseries_sheet(
                    sheet_key, snapshot_df, trend_df
                )

        def _ingest_leaderboard(name: str, payload: Any) -> None:
            rows = payload if isinstance(payload, list) else []
            df = _leaderboard_to_dataframe(rows)
            if df.empty:
                result.warnings.append(f"{name}: 榜单为空或格式不符")
                return
            sheets[name] = df
            result.sheet_summaries[name] = _summarize_leaderboard_sheet(name, df)

        # 1) 优先解析 merged_rankings 内各维度
        if merged_payload:
            if "Market Share" in merged_payload:
                _ingest_timeseries("Market Share", merged_payload["Market Share"])
            if "Top Model" in merged_payload:
                _ingest_timeseries("Top Model", merged_payload["Top Model"], entity_col="model")
            for lb_key in ("day_leaderboard", "week_leaderboard", "month_leaderboard"):
                if lb_key in merged_payload:
                    _ingest_leaderboard(lb_key, merged_payload[lb_key])
            for extra_key in ("Categories", "Context Length", "Programming", "Languages", "Top Apps"):
                if extra_key in merged_payload:
                    _ingest_timeseries(extra_key, merged_payload[extra_key], entity_col="model")

        # 2) 独立 JSON 补全/覆盖（磁盘直读，不完全依赖 pipeline）
        standalone_sources = {
            "Market Share": (_resolve_path(self.data_cfg["market_share"]), "provider"),
            "Top Model": (_resolve_path(self.data_cfg["top_model"]), "model"),
        }
        for sheet_name, (file_path, entity_col) in standalone_sources.items():
            try:
                payload = _load_json_file(file_path)
                _ingest_timeseries(sheet_name, payload, entity_col=entity_col)
                print(f"[Analyst] 已加载独立数据集: {file_path.name}")
            except Exception as exc:
                result.warnings.append(f"跳过独立文件 {file_path.name}: {exc}")

        leaderboard_files = {
            "day_leaderboard": _resolve_path(self.data_cfg["day_leaderboard"]),
            "week_leaderboard": _resolve_path(self.data_cfg["week_leaderboard"]),
            "month_leaderboard": _resolve_path(self.data_cfg["month_leaderboard"]),
        }
        for lb_name, lb_path in leaderboard_files.items():
            if lb_name in sheets:
                continue
            try:
                payload = _load_json_file(lb_path)
                _ingest_leaderboard(lb_name, payload)
                print(f"[Analyst] 已加载榜单: {lb_path.name}")
            except Exception as exc:
                result.warnings.append(f"跳过榜单 {lb_path.name}: {exc}")

        if not sheets:
            raise RuntimeError("预处理失败：未从 JSON 解析出任何有效表格")

        # 3) 导出 Excel
        excel_path = _resolve_path(self.data_cfg["excel_output"])
        try:
            excel_path.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                for sheet_name, df in sheets.items():
                    safe_name = _sanitize_sheet_name(sheet_name)
                    try:
                        df.to_excel(writer, sheet_name=safe_name, index=False)
                    except Exception as exc:
                        result.warnings.append(f"Excel 写入跳过 {sheet_name}: {exc}")
            result.excel_path = excel_path
            print(f"[Analyst] Excel 已导出 → {excel_path}")
        except Exception as exc:
            result.warnings.append(f"Excel 导出失败: {exc}")

        result.data_cards = _build_data_cards(result.sheet_summaries, snapshot_tables)
        self._preprocess_result = result
        return result

    @staticmethod
    def _normalize_raw_texts(raw_texts: list | None) -> list[Document]:
        documents: list[Document] = []
        if not raw_texts:
            return documents

        for index, item in enumerate(raw_texts):
            try:
                if isinstance(item, str):
                    text = item.strip()
                    metadata: dict[str, Any] = {"source_index": index, "source_type": "raw_text"}
                elif isinstance(item, dict):
                    text = str(item.get("text", "") or item.get("content", "")).strip()
                    metadata = {k: v for k, v in item.items() if k not in {"text", "content"}}
                    metadata.setdefault("source_index", index)
                    metadata.setdefault("source_type", "collector_text")
                else:
                    continue

                if not text:
                    continue
                documents.append(Document(page_content=text, metadata=metadata))
            except Exception:
                continue
        return documents

    def _collect_knowledge_documents(
        self,
        raw_texts: list | None,
        preprocess_result: PreprocessResult | None,
    ) -> list[Document]:
        """汇总结构化摘要、数据卡片、AI Index PDF 与 Collector 文本。"""
        docs: list[Document] = []

        if preprocess_result:
            if preprocess_result.data_cards:
                docs.append(
                    Document(
                        page_content=preprocess_result.data_cards,
                        metadata={"source_type": "data_card"},
                    )
                )
            for sheet_name, summary in preprocess_result.sheet_summaries.items():
                if summary.strip():
                    docs.append(
                        Document(
                            page_content=summary,
                            metadata={"source_type": "sheet_summary", "sheet": sheet_name},
                        )
                    )

        pdf_path = _resolve_path(self.data_cfg["ai_index_pdf"])
        pdf_text = _extract_pdf_text(pdf_path)
        if pdf_text:
            docs.append(
                Document(
                    page_content=pdf_text,
                    metadata={"source_type": "ai_index_pdf", "source_file": pdf_path.name},
                )
            )
            print(f"[Analyst] 已载入 AI Index PDF 文本: {pdf_path.name}")
        elif pdf_path.parent.exists():
            print(f"[Analyst] 未找到 AI Index PDF（可放置于 {pdf_path}），跳过")

        docs.extend(self._normalize_raw_texts(raw_texts))

        if not docs:
            raise ValueError("知识库无有效文档：预处理、PDF 与 raw_texts 均为空")
        return docs

    def build_knowledge_base(
        self,
        raw_texts: list | None = None,
        *,
        preprocess_result: PreprocessResult | None = None,
    ) -> int:
        """
        构建 FAISS 向量库：结构化摘要 + 数据卡片 + AI Index PDF + Collector 文本。
        """
        try:
            pre = preprocess_result or self._preprocess_result
            source_docs = self._collect_knowledge_documents(raw_texts, pre)

            chunks: list[Document] = []
            for doc in source_docs:
                source_type = (doc.metadata or {}).get("source_type", "")
                # 结构化摘要与数据卡片保持完整，避免打碎关键指标
                if source_type in {"data_card", "sheet_summary"}:
                    chunks.append(doc)
                else:
                    chunks.extend(self.text_splitter.split_documents([doc]))

            if not chunks:
                raise ValueError("文本切片结果为空")

            self.vectorstore = FAISS.from_documents(chunks, self.embeddings)

            persist_dir = (self.analyst_cfg.get("rag") or {}).get("vector_store_dir")
            if persist_dir:
                save_path = _resolve_path(persist_dir)
                try:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    self.vectorstore.save_local(str(save_path))
                except Exception:
                    pass

            print(f"[Analyst] 知识库已构建，共 {len(chunks)} 个向量片段")
            return len(chunks)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"构建知识库失败（智谱 Embedding）: {exc}") from exc

    def retrieve_context(self, query: str, k: int | None = None) -> list[str]:
        if self.vectorstore is None:
            raise RuntimeError("向量库未构建，请先调用 build_knowledge_base()")

        top_k = k if k is not None else self._top_k
        query = (query or "").strip()
        if not query:
            raise ValueError("检索 query 不能为空")

        try:
            docs = self.vectorstore.similarity_search(query, k=top_k)
            return [doc.page_content for doc in docs]
        except Exception as exc:
            raise RuntimeError(f"检索失败 [{query}]: {exc}") from exc

    def _get_retrieval_queries(self) -> dict[str, str]:
        retrieval_cfg = self.analyst_cfg.get("retrieval", {}) or {}
        return {
            "openai": (
                retrieval_cfg.get("openai_query")
                or retrieval_cfg.get("sales_query")
                or "OpenAI API token usage market share gpt-4o gpt-5 platform ranking"
            ),
            "competitor": (
                retrieval_cfg.get("competitor_query")
                or "Google Gemini Anthropic Claude DeepSeek API competitor token usage comparison"
            ),
            "industry": (
                retrieval_cfg.get("industry_query")
                or "LLM API industry trend context length open source model adoption 2025"
            ),
        }

    def _build_prompts(
        self,
        user_requirement: str,
        contexts: dict[str, list[str]],
        preprocess_result: PreprocessResult | None = None,
    ) -> tuple[str, str]:
        def _format_block(key: str) -> str:
            label = RETRIEVAL_LABELS[key]
            snippets = contexts.get(key) or []
            if not snippets:
                return f"{label}\n（未检索到相关内容）\n"
            body = "\n\n---\n\n".join(f"片段 {i + 1}:\n{s}" for i, s in enumerate(snippets))
            return f"{label}\n{body}\n"

        context_block = "\n".join(_format_block(key) for key in RETRIEVAL_KEYS)
        section_list = "\n".join(f"## {title}" for title in REPORT_SECTION_HEADINGS)

        data_card_block = ""
        if preprocess_result and preprocess_result.data_cards:
            data_card_block = f"## 结构化数据卡片\n{preprocess_result.data_cards}\n\n"

        system_prompt = (
            "你是一名资深大模型 API 竞品分析师，专注分析 OpenRouter 等平台的多维 API Token 调用数据。\n"
            "分析框架（三维）：\n"
            "  1. 主体维度 = OpenAI（其 API 调用量、市场份额、代表模型表现、竞争位置）\n"
            "  2. 竞品维度 = Google / Anthropic / DeepSeek / Meta / Mistral 等 API 提供商\n"
            "  3. 行业维度 = 大模型 API 整体趋势（上下文长度、开源模型、调用量变化等）\n"
            "请严格基于提供的检索上下文与数据卡片作答；若证据不足请明确指出数据缺口，"
            "禁止编造未出现的具体数字或事实。\n"
            "本报告主题为大模型 API 使用竞品分析，不涉及手机、硬件销量或其他无关品类。\n"
            "输出必须使用以下 Markdown 章节标题（逐字一致，便于下游 Generator 解析回填）：\n"
            f"{section_list}\n"
            "章节内容要求：\n"
            "  - 「二、主流 API 平台使用表现」聚焦 OpenAI 及主流 API 平台 Token 调用与排名\n"
            "  - 「三、大模型 API 行业趋势研判」聚焦行业整体趋势\n"
            "  - 「四、竞品 API 威胁与机会」聚焦 Google/Anthropic/DeepSeek 等竞品\n"
            "  - 「五、战略建议」分短期（0-6月）与中期（6-18月）\n"
            "  - 各章节须有具体数据引用（Token 量、排名、份额变化等）"
        )

        user_prompt = (
            f"## 分析需求\n{user_requirement.strip()}\n\n"
            f"{data_card_block}"
            f"## 检索上下文（RAG）\n{context_block}\n"
            "## 任务\n"
            "请综合以上数据卡片与三维检索证据，撰写《2025年大模型 API 竞品分析报告》正文。"
            "严格按照系统提示中的六个章节标题输出，不要增减章节，不要使用其他标题格式。"
        )

        return system_prompt, user_prompt

    def analyze_data(
        self,
        user_requirement: str,
        k: int | None = None,
        *,
        preprocess_result: PreprocessResult | None = None,
    ) -> AnalysisResult:
        requirement = (user_requirement or "").strip()
        if not requirement:
            raise ValueError("user_requirement 不能为空")

        pre = preprocess_result or self._preprocess_result

        try:
            queries = self._get_retrieval_queries()
            contexts: dict[str, list[str]] = {}
            for key in RETRIEVAL_KEYS:
                contexts[key] = self.retrieve_context(queries[key], k=k)

            system_prompt, user_prompt = self._build_prompts(requirement, contexts, pre)
            result = AnalysisResult(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                retrieved_contexts=contexts,
                preprocess=pre,
            )

            try:
                response = self.llm.invoke(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt),
                    ]
                )
                analysis_text = getattr(response, "content", None)
                if not analysis_text or not str(analysis_text).strip():
                    raise RuntimeError("DeepSeek 返回内容为空")
                result.analysis = str(analysis_text).strip()
            except Exception as exc:
                raise RuntimeError(
                    f"DeepSeek Chat API 调用失败: {exc}\n"
                    "请检查 openai.api_key、openai.base_url、openai.model 及网络连接。"
                ) from exc

            return result
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"API 竞品分析失败: {exc}") from exc

    def run(self, raw_texts: list, user_requirement: str, k: int | None = None) -> str:
        """预处理 → 知识库 → 三维 RAG 分析，输出 Generator 可解析的六章 Markdown。"""
        print("[Analyst] Step 2a: 多源 API JSON 结构化预处理...")
        preprocess_result = self.preprocess()
        if preprocess_result.warnings:
            for warn in preprocess_result.warnings:
                print(f"[Analyst] [WARN] {warn}")

        print("[Analyst] Step 2b: 构建 RAG 知识库（结构化摘要 + PDF + Collector 文本）...")
        self.build_knowledge_base(raw_texts=raw_texts, preprocess_result=preprocess_result)

        print("[Analyst] Step 2c: 三维 RAG 检索 + DeepSeek API 竞品分析...")
        result = self.analyze_data(user_requirement, k=k, preprocess_result=preprocess_result)
        return (result.analysis or "").strip()
