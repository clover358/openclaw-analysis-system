"""Analyst-Agent：多源 API 竞品结构化预处理 + RAG 知识库 + 商业分析（双模型混用）。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
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
    "categories": "data/Categories.json",
    "languages": "data/Languages.json",
    "programming": "data/Programming.json",
    "context_length": "data/Context Length.json",
    "top_apps": "data/Top Apps.json",
    "excel_output": "data/processed/openrouter_rankings.xlsx",
    "ai_index_pdf": "data/ai_index_report_2026_chapter_4_economy.pdf",
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


def _parse_date(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if isinstance(value, datetime):
        return pd.Timestamp(value).normalize()
    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    normalized = re.sub(r"^(\d{4})-(\d{1,2})$", r"\1-\2-01", normalized)
    normalized = re.sub(r"^(\d{4})(\d{2})(\d{2})$", r"\1-\2-\3", normalized)
    normalized = re.sub(r"^(\d{4})(\d{2})$", r"\1-\2-01", normalized)

    for candidate in (normalized, text):
        try:
            ts = pd.to_datetime(candidate, errors="coerce")
            if pd.isna(ts):
                continue
            return pd.Timestamp(ts).normalize()
        except Exception:
            continue
    return None


def _date_key(value: Any) -> tuple[int, pd.Timestamp | str]:
    parsed = _parse_date(value)
    if parsed is not None:
        return (1, parsed)
    return (0, str(value or ""))


def _date_text(value: Any) -> str:
    parsed = _parse_date(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d")
    return str(value or "")


def _format_value_for_prompt(value: Any, column: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    col = column.lower()
    if isinstance(value, pd.Timestamp):
        return _date_text(value)
    if any(key in col for key in ("token", "count", "request")):
        return _format_tokens(value)
    if "share" in col or "change" in col:
        try:
            number = float(value)
            if "share" in col:
                return f"{number:.2f}%"
            if abs(number) <= 5:
                number *= 100
            return f"{number:+.1f}%"
        except Exception:
            return str(value)
    return str(value)


def _series_token_column(df: pd.DataFrame) -> str | None:
    for candidate in (
        "tokens",
        "analysis_tokens",
        "total_tokens",
        "total_prompt_tokens",
        "total_completion_tokens",
        "count",
    ):
        if candidate in df.columns:
            return candidate
    return None


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


def _extract_app_ranking_blocks(payload: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    """提取 Top Apps 这类 day/week/month 应用榜单。"""
    blocks: list[tuple[str, list[dict[str, Any]]]] = []
    if isinstance(payload, dict):
        for key in ("day", "week", "month"):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                blocks.append((key, value))
    return blocks


def _timeseries_to_dataframes(
    series: list[dict[str, Any]],
    *,
    entity_col: str = "provider",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """将 {x, ys} 时间序列转为最新一期快照表。"""
    rows: list[dict[str, Any]] = []
    for point in series:
        period = str(point.get("x", ""))
        ys: dict[str, Any] = point.get("ys") or {}
        if not period or not ys:
            continue
        period_dt = _parse_date(period)
        for name, tokens in ys.items():
            try:
                rows.append(
                    {
                        "period": _date_text(period_dt) if period_dt is not None else period,
                        "period_dt": period_dt,
                        entity_col: str(name),
                        "tokens": float(tokens or 0),
                    }
                )
            except (TypeError, ValueError):
                continue

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(rows)
    valid_dates = df.dropna(subset=["period_dt"])
    if not valid_dates.empty:
        latest_dt = valid_dates["period_dt"].max()
        snapshot_df = df[df["period_dt"] == latest_dt].copy()
        latest_period = _date_text(latest_dt)
    else:
        latest_period = max(df["period"].unique(), key=_date_key)
        snapshot_df = df[df["period"] == latest_period].copy()

    snapshot_df = snapshot_df.sort_values("tokens", ascending=False).reset_index(drop=True)
    snapshot_df["period"] = latest_period
    snapshot_df["rank"] = range(1, len(snapshot_df) + 1)
    total_tokens = float(snapshot_df["tokens"].sum() or 0)
    snapshot_df["share_pct"] = 0.0 if total_tokens <= 0 else (snapshot_df["tokens"] / total_tokens * 100).round(2)

    full_df = df.sort_values(["period", "tokens"], ascending=[False, False]).reset_index(drop=True)
    full_df["rank"] = full_df.groupby("period")["tokens"].rank(method="first", ascending=False).astype(int)
    period_totals = full_df.groupby("period")["tokens"].transform("sum")
    full_df["share_pct"] = (full_df["tokens"] / period_totals.replace(0, pd.NA) * 100).fillna(0).round(2)
    retained_df = _retain_latest_plus_openai_periods(full_df, "period")
    return retained_df, snapshot_df


def _row_contains_openai(row: pd.Series) -> bool:
    text = " ".join(str(v).lower() for v in row.to_dict().values())
    return bool(re.search(r"openai|gpt[-\w]*|chatgpt|\bo1\b|\bo3\b|\bo4\b|o1[-\w]*|o3[-\w]*|o4[-\w]*", text))


def _retain_latest_plus_openai_periods(df: pd.DataFrame, date_col: str = "period") -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df
    out = df.copy()
    parsed_col = f"{date_col}_retain_dt"
    out[parsed_col] = out[date_col].map(_parse_date)
    valid = out.dropna(subset=[parsed_col])
    if valid.empty:
        return df
    latest_dt = valid[parsed_col].max()
    keep_dates = {latest_dt}
    openai_dates = (
        valid[valid.apply(_row_contains_openai, axis=1)][parsed_col]
        .dropna()
        .drop_duplicates()
        .sort_values(ascending=False)
        .head(3)
        .tolist()
    )
    keep_dates.update(openai_dates)
    kept = out[out[parsed_col].isin(keep_dates)].copy()
    kept = kept.drop(columns=[parsed_col])
    return kept.sort_values([date_col, "tokens"] if "tokens" in kept.columns else [date_col], ascending=[False, False] if "tokens" in kept.columns else [False]).reset_index(drop=True)


def _latest_rows_by_date(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df
    df = df.copy()
    parsed_col = f"{date_col}_dt"
    df[parsed_col] = df[date_col].map(_parse_date)
    valid_dates = df.dropna(subset=[parsed_col])
    if not valid_dates.empty:
        latest_dt = valid_dates[parsed_col].max()
        df = df[df[parsed_col] == latest_dt].copy()
        df[date_col] = _date_text(latest_dt)
    else:
        values = [value for value in df[date_col].dropna().unique() if str(value).strip()]
        if values:
            latest_value = max(values, key=_date_key)
            df = df[df[date_col] == latest_value].copy()
    return df


def _leaderboard_to_dataframe(rows: list[dict[str, Any]], *, top_n: int = LEADERBOARD_TOP_N) -> pd.DataFrame:
    """将 day/week/month leaderboard 转为最新日期榜单表格。"""
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    token_candidates = (
        "total_tokens",
        "total_prompt_tokens",
        "total_completion_tokens",
        "tokens",
        "count",
    )
    for col in token_candidates:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if "date" in df.columns:
        df = _latest_rows_by_date(df, "date")

    sort_col = next((col for col in token_candidates if col in df.columns), None)
    if sort_col:
        df["analysis_tokens"] = df[sort_col]
        if "total_completion_tokens" in df.columns and "total_prompt_tokens" in df.columns:
            df["analysis_tokens"] = df["total_completion_tokens"] + df["total_prompt_tokens"]
            sort_col = "prompt+completion_tokens"
        df = df.sort_values("analysis_tokens", ascending=False).head(top_n)

    df = df.reset_index(drop=True)
    if sort_col:
        df["rank"] = range(1, len(df) + 1)
        df["analysis_token_field"] = sort_col
    return df


def _app_rankings_to_dataframe(rows: list[dict[str, Any]], *, view: str, top_n: int = 30) -> pd.DataFrame:
    """将 Top Apps 的 day/week/month 榜单压平为表格。"""
    out: list[dict[str, Any]] = []
    for item in rows[:top_n]:
        if not isinstance(item, dict):
            continue
        app = item.get("app") if isinstance(item.get("app"), dict) else {}
        out.append(
            {
                "view": view,
                "rank": item.get("rank"),
                "app_title": app.get("title") or item.get("title") or app.get("name"),
                "app_slug": app.get("slug") or item.get("slug"),
                "total_tokens": item.get("total_tokens") or item.get("tokens") or 0,
                "prompt_tokens": item.get("total_prompt_tokens") or 0,
                "completion_tokens": item.get("total_completion_tokens") or 0,
                "total_requests": item.get("total_requests") or item.get("requests") or 0,
                "count": item.get("count") or 0,
            }
        )
    df = pd.DataFrame(out)
    if df.empty:
        return df
    for col in ("total_tokens", "prompt_tokens", "completion_tokens", "total_requests", "count"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "date" in df.columns:
        df = _latest_rows_by_date(df, "date")
    if "rank" in df.columns:
        df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
        df = df.sort_values(["rank", "total_tokens"], ascending=[True, False])
    else:
        df = df.sort_values("total_tokens", ascending=False)
        df["rank"] = range(1, len(df) + 1)
    return df.head(top_n).reset_index(drop=True)


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
    latest_period = "最新周期"
    if "period_dt" in snapshot_df.columns and not snapshot_df["period_dt"].isna().all():
        latest_period = _date_text(snapshot_df["period_dt"].max())
    elif "period" in snapshot_df.columns:
        latest_period = str(snapshot_df["period"].iloc[0])

    lines = [
        f"【分表摘要】{sheet_name}",
        "- 数据类型: OpenRouter API Token 调用量",
        "- 取数口径: 仅保留最新日期快照",
        f"- 最新周期: {latest_period}",
        f"- Top {min(TIMESERIES_TOP_N, len(snapshot_df))} 排名:",
    ]
    for _, row in snapshot_df.head(TIMESERIES_TOP_N).iterrows():
        entity = str(row.get(entity_col, ""))
        tokens = _format_tokens(row.get("tokens", 0))
        share = row.get("share_pct", "")
        share_text = f", 份额 {share}%" if share != "" else ""
        lines.append(f"  {int(row.get('rank', 0))}. {entity}: {tokens} tokens{share_text}")

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
    token_col = "analysis_tokens" if "analysis_tokens" in df.columns else None
    if token_col is None:
        for candidate in ("total_tokens", "total_prompt_tokens", "total_completion_tokens", "tokens", "count"):
            if candidate in df.columns:
                token_col = candidate
                break
    if model_col and token_col:
        raw_field = df.get("analysis_token_field")
        source_field = str(raw_field.iloc[0]) if raw_field is not None and not raw_field.empty else token_col
        lines.append(f"- 排序/摘要口径: {source_field}")
        lines.append(f"- Top {min(10, len(df))} 模型:")
        for _, row in df.head(10).iterrows():
            rank = int(row.get("rank", 0)) if str(row.get("rank", "")).strip() else 0
            lines.append(
                f"  {rank}. {row[model_col]}: "
                f"{_format_tokens(row[token_col])} tokens"
            )
    return "\n".join(lines)


def _summarize_app_sheet(sheet_name: str, df: pd.DataFrame) -> str:
    if df.empty:
        return f"【分表摘要】{sheet_name}\n（无有效应用榜单数据）"
    lines = [
        f"【分表摘要】{sheet_name}",
        "- 数据类型: OpenRouter 应用层 Top Apps 榜单",
        f"- 记录数: {len(df)}",
    ]
    if "view" in df.columns and not df["view"].empty:
        lines.append(f"- 视图: {df['view'].iloc[0]}")
    title_col = "app_title" if "app_title" in df.columns else None
    token_col = "total_tokens" if "total_tokens" in df.columns else None
    if title_col and token_col:
        lines.append(f"- Top {min(10, len(df))} 应用:")
        for _, row in df.head(10).iterrows():
            rank = int(row.get("rank", 0)) if str(row.get("rank", "")).strip() else 0
            lines.append(
                f"  {rank}. {row.get(title_col, '')}: {_format_tokens(row.get(token_col, 0))} tokens"
            )
    return "\n".join(lines)


def _dataframe_to_markdown_snapshot(sheet_name: str, df: pd.DataFrame, *, max_rows: int | None = 20) -> str:
    """把关键结构化表格转为 Markdown，直接纳入 RAG。"""
    if df.empty:
        return ""
    cols = list(df.columns[:14])
    priority = [
        "rank", "period", "date", "view", "provider", "model", "model_permaslug",
        "app_title", "app_slug", "tokens", "analysis_tokens", "total_tokens",
        "total_prompt_tokens", "total_completion_tokens", "prompt_tokens", "completion_tokens",
        "total_requests", "share_pct", "change", "count", "analysis_token_field",
    ]
    ordered = [c for c in priority if c in df.columns]
    ordered.extend(c for c in cols if c not in ordered and not c.endswith("_dt"))
    ordered = ordered[:14]
    slim = df.loc[:, ordered].copy()
    if max_rows is not None:
        slim = slim.head(max_rows)
    for col in slim.columns:
        slim[col] = slim[col].map(lambda v, c=col: _format_value_for_prompt(v, c))
    latest_text = ""
    for date_col in ("period", "date"):
        if date_col in df.columns and not df[date_col].dropna().empty:
            latest_text = f"\n- 最新周期: {_date_text(df[date_col].dropna().iloc[0])}"
            break
    return f"【结构化表格】{sheet_name}{latest_text}\n" + slim.to_markdown(index=False)


def _latest_period_declaration(sheet_name: str, df: pd.DataFrame) -> str:
    """为每个核心表输出明确的最新周期声明，避免下游模型改写年份。"""
    if df.empty:
        return f"### {sheet_name} 最新周期声明\n- {sheet_name}: 无有效数据。"
    for date_col in ("period", "date"):
        if date_col in df.columns and not df[date_col].dropna().empty:
            latest = _date_text(df[date_col].dropna().iloc[0])
            return (
                f"### {sheet_name} 最新周期声明\n"
                f"- {sheet_name} 最新周期: {latest}\n"
                f"- 下方表格所有日期/年份均以该字段和表格中的 period/date 为准，不得推断或改写。"
            )
    return (
        f"### {sheet_name} 最新周期声明\n"
        f"- {sheet_name}: 未提供 period/date 字段；不得自行补写日期或年份。"
    )


def _observation_range_from_tables(sheets: dict[str, pd.DataFrame]) -> tuple[str, str]:
    dates: list[pd.Timestamp] = []
    for df in sheets.values():
        for col in ("period_dt", "date_dt", "period", "date"):
            if col not in df.columns:
                continue
            for value in df[col].dropna().tolist():
                parsed = value if isinstance(value, pd.Timestamp) else _parse_date(value)
                if parsed is not None:
                    dates.append(parsed)
    if not dates:
        return "", ""
    return min(dates).strftime("%Y-%m-%d"), max(dates).strftime("%Y-%m-%d")


def _openai_rows_from_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for col in ("provider", "model", "model_permaslug", "variant_permaslug", "slug", "name"):
        if col in df.columns:
            values = df[col].astype(str).str.lower()
            mask = mask | values.str.contains(
                r"(^|[/\s:_-])openai($|[/\s:_-])|openai/|gpt[-\w]*|chatgpt|\bo1\b|\bo3\b|\bo4\b|o1[-\w]*|o3[-\w]*|o4[-\w]*",
                regex=True,
                na=False,
            )
    return df[mask].copy()


def _summarize_openai_model_presence(df: pd.DataFrame, *, label: str, max_rows: int = 8) -> str:
    openai_df = _openai_rows_from_df(df)
    if openai_df.empty:
        return f"- {label}: 未在当前 Top 榜单中检索到 OpenAI 相关模型。"

    token_col = _series_token_column(openai_df)
    rank_col = "rank" if "rank" in openai_df.columns else None
    name_col = next(
        (col for col in ("model", "model_permaslug", "variant_permaslug", "provider", "app_title") if col in openai_df.columns),
        None,
    )
    if not name_col:
        return f"- {label}: 检索到 {len(openai_df)} 条 OpenAI 相关记录。"

    lines = [f"- {label}: 检索到 {len(openai_df)} 条 OpenAI 相关记录"]
    sort_df = openai_df.copy()
    if rank_col:
        sort_df[rank_col] = pd.to_numeric(sort_df[rank_col], errors="coerce")
        sort_df = sort_df.sort_values(rank_col, na_position="last")
    elif token_col:
        sort_df[token_col] = pd.to_numeric(sort_df[token_col], errors="coerce").fillna(0)
        sort_df = sort_df.sort_values(token_col, ascending=False)

    for _, row in sort_df.head(max_rows).iterrows():
        rank = row.get(rank_col, "") if rank_col else ""
        rank_text = f"第 {int(rank)} 名 " if str(rank).strip() and not pd.isna(rank) else ""
        token_text = f"，{_format_tokens(row.get(token_col, 0))} tokens" if token_col else ""
        share_text = f"，份额 {row.get('share_pct')}%" if "share_pct" in row and str(row.get("share_pct", "")).strip() else ""
        lines.append(f"  · {rank_text}{row.get(name_col, '')}{token_text}{share_text}")
    return "\n".join(lines)


def _build_core_data_pack(
    sheets: dict[str, pd.DataFrame],
    snapshot_tables: dict[str, pd.DataFrame],
    observation_start: str,
    observation_end: str,
) -> str:
    """为 LLM 直接注入的核心数据包，避免只依赖向量检索命中。"""
    parts: list[str] = ["## 强制核心数据包"]
    if observation_start and observation_end:
        parts.append(f"- 数据观测范围: {observation_start} → {observation_end}")

    market = snapshot_tables.get("Market Share")
    if market is not None and not market.empty:
        parts.append(_latest_period_declaration("Market Share", market))
        parts.append("\n### Market Share 最新厂商份额核心字段表（完整最新组）")
        parts.append(_dataframe_to_markdown_snapshot("Market Share Latest Full", market, max_rows=None))
        openai = market[market["provider"].astype(str).str.lower() == "openai"] if "provider" in market.columns else pd.DataFrame()
        if not openai.empty:
            row = openai.iloc[0]
            parts.append(
                f"- OpenAI 主体最新排名第 {int(row.get('rank', 0))} 名，"
                f"调用量 {_format_tokens(row.get('tokens', 0))} tokens，"
                f"份额 {row.get('share_pct', 0)}%。"
            )
        competitors = market[market["provider"].astype(str).str.lower() != "openai"].head(5) if "provider" in market.columns else pd.DataFrame()
        if not competitors.empty:
            competitor_text = "；".join(
                f"第 {int(row.get('rank', 0))} 名 {_provider_label(str(row.get('provider', '')))} {_format_tokens(row.get('tokens', 0))} tokens/{row.get('share_pct', 0)}%"
                for _, row in competitors.iterrows()
            )
            parts.append(f"- 主要竞品最新份额: {competitor_text}。")

    openai_summaries: list[str] = []
    for key in ("Top Model", "Categories", "Programming", "Languages", "Context Length"):
        latest = snapshot_tables.get(key)
        if latest is not None and not latest.empty:
            parts.append(_latest_period_declaration(key, latest))
            parts.append(f"\n### {key} 最新核心字段表")
            parts.append(_dataframe_to_markdown_snapshot(f"{key} Latest Core", latest, max_rows=None))
            openai_latest = _openai_rows_from_df(latest)
            if not openai_latest.empty:
                openai_summaries.append(_summarize_openai_model_presence(latest, label=key))
                parts.append(f"\n### {key} 中 OpenAI 模型表现")
                parts.append(_dataframe_to_markdown_snapshot(f"{key} OpenAI", openai_latest.head(10), max_rows=10))

    for key in ("day_leaderboard", "week_leaderboard", "month_leaderboard"):
        df = sheets.get(key)
        if df is not None and not df.empty:
            parts.append(_latest_period_declaration(key, df))
            parts.append(f"\n### {key} 核心字段表（最新榜单）")
            parts.append(_dataframe_to_markdown_snapshot(key, df, max_rows=None))
            parts.append(_summarize_openai_rows(df, label=f"{key} 中 OpenAI 模型表现", max_rows=10))
            openai_df = _openai_rows_from_df(df)
            if not openai_df.empty:
                openai_summaries.append(_summarize_openai_model_presence(df, label=key))
                parts.append(f"\n### {key} 中 OpenAI 模型明细")
                parts.append(_dataframe_to_markdown_snapshot(f"{key} OpenAI", openai_df.head(10), max_rows=10))
        else:
            parts.append(f"\n### {key} 数据状态\n- {key}: 未解析到有效榜单数据。")

    if openai_summaries:
        parts.append("\n### OpenAI 主体表现摘要")
        parts.append("\n".join(openai_summaries))

    for key in ("Top Apps_day", "Top Apps_week", "Top Apps_month"):
        df = sheets.get(key)
        if df is not None and not df.empty:
            parts.append(_latest_period_declaration(key, df))
            parts.append(f"\n### {key} 核心字段表")
            parts.append(_dataframe_to_markdown_snapshot(key, df, max_rows=10))

    parts.append(
        "\n### 写作硬约束\n"
        "- 报告标题和所有日期判断必须使用上方每个 sheet 的最新周期声明、period/date 字段与数据观测范围，不得从旧样例或生成时间推断。\n"
        "- 当前任务发生在 2026 年；若表格中 period/date 为 2026 年，严禁改写成 2025 年或其他年份。\n"
        "- 所有具体年份、日期、周期表述必须能在上方核心字段表中逐字找到来源；找不到来源时必须写“数据未提供”，不得补写。\n"
        "- 必须单独分析 OpenAI 旗下模型在 Top Model、各 leaderboard 和细分榜单中的表现。\n"
        "- 数字应使用 B/T/M tokens 等可读单位，不要输出科学计数法。"
    )
    return "\n\n".join(p for p in parts if p).strip()


def _build_data_cards(
    sheet_summaries: dict[str, str],
    snapshot_tables: dict[str, pd.DataFrame],
    observation_start: str = "",
    observation_end: str = "",
) -> str:
    """基于各表快照生成 API 竞品数据卡片。"""
    lines = [
        "【数据卡片】大模型 API 竞品数据概览",
        "- 数据来源: OpenRouter 多维度 API 调用统计（Token 调用量）",
        "- 分析对象: 主流大模型 API 提供商（OpenAI、Google、Anthropic、DeepSeek 等）",
    ]
    if observation_start and observation_end:
        lines.append(f"- 数据观测范围: {observation_start} → {observation_end}")
    lines.append("")

    market_df = snapshot_tables.get("Market Share")
    if market_df is not None and not market_df.empty:
        top3 = market_df.head(3)
        lines.append("## API 厂商市场份额（最新周期）")
        for _, row in top3.iterrows():
            provider = _provider_label(str(row.get("provider", "")))
            lines.append(
                f"- 第 {int(row['rank'])} 名 {provider}: {_format_tokens(row['tokens'])} tokens "
                f"({row.get('share_pct', 0)}%)"
            )
        openai_row = market_df[market_df["provider"].str.lower() == "openai"]
        if not openai_row.empty:
            row = openai_row.iloc[0]
            lines.append(
                f"- OpenAI（主体）排名第 {int(row['rank'])} 名，份额 {row.get('share_pct', 0)}%，"
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
            openai_text = _summarize_openai_model_presence(df, label=f"{sheet_name} OpenAI 表现", max_rows=5)
            lines.append(openai_text)
            lines.append("")

    lines.append("## 分表索引")
    for name in sheet_summaries:
        lines.append(f"- {name}")
    return "\n".join(lines).strip()


def _latest_snapshot_from_timeseries_payload(payload: Any, *, entity_col: str) -> pd.DataFrame:
    """直接从任意 {x, ys} JSON 中取全局最新日期快照，作为硬数据兜底。"""
    snapshots: list[pd.DataFrame] = []
    for _block_name, series in _extract_timeseries_blocks(payload):
        _trend_df, snapshot_df = _timeseries_to_dataframes(series, entity_col=entity_col)
        if not snapshot_df.empty:
            snapshots.append(snapshot_df)
    if not snapshots:
        return pd.DataFrame()
    combined = pd.concat(snapshots, ignore_index=True)
    if "period_dt" in combined.columns and not combined["period_dt"].isna().all():
        latest_dt = combined["period_dt"].max()
        combined = combined[combined["period_dt"] == latest_dt].copy()
        combined["period"] = _date_text(latest_dt)
    elif "period" in combined.columns:
        latest_period = max(combined["period"].dropna().unique(), key=_date_key)
        combined = combined[combined["period"] == latest_period].copy()
    token_col = _series_token_column(combined) or "tokens"
    combined[token_col] = pd.to_numeric(combined[token_col], errors="coerce").fillna(0)
    combined = combined.sort_values(token_col, ascending=False).reset_index(drop=True)
    combined["rank"] = range(1, len(combined) + 1)
    total_tokens = float(combined[token_col].sum() or 0)
    if token_col == "tokens":
        combined["share_pct"] = 0.0 if total_tokens <= 0 else (combined[token_col] / total_tokens * 100).round(2)
    return combined


def _format_rank(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return ""
        return f"第 {int(value)} 名"
    except Exception:
        return ""


def _summarize_openai_rows(df: pd.DataFrame, *, label: str, max_rows: int = 10) -> str:
    """明确总结某榜单中 OpenAI 行，避免报告误写“原始数据未提供”。"""
    if df is None or df.empty:
        return f"- {label}: 榜单数据为空。"
    openai_df = _openai_rows_from_df(df)
    if openai_df.empty:
        return f"- {label}: 榜单已提供，但 OpenAI 相关模型未进入当前注入范围。"
    token_col = _series_token_column(openai_df) or "analysis_tokens"
    name_col = next(
        (col for col in ("model", "model_permaslug", "variant_permaslug", "provider", "app_title") if col in openai_df.columns),
        None,
    )
    if not name_col:
        return f"- {label}: 检索到 {len(openai_df)} 条 OpenAI 相关记录。"
    rows = []
    sort_df = openai_df.copy()
    if "rank" in sort_df.columns:
        sort_df["rank"] = pd.to_numeric(sort_df["rank"], errors="coerce")
        sort_df = sort_df.sort_values("rank", na_position="last")
    elif token_col in sort_df.columns:
        sort_df[token_col] = pd.to_numeric(sort_df[token_col], errors="coerce").fillna(0)
        sort_df = sort_df.sort_values(token_col, ascending=False)
    for _, row in sort_df.head(max_rows).iterrows():
        rank_text = _format_rank(row.get("rank"))
        token_text = f"，{_format_tokens(row.get(token_col, 0))} tokens" if token_col in row else ""
        share_text = f"，份额 {row.get('share_pct')}%" if "share_pct" in row and str(row.get("share_pct", "")).strip() else ""
        rows.append(f"{rank_text} {row.get(name_col, '')}{token_text}{share_text}".strip())
    return f"- {label}: " + "；".join(rows)


def _add_latest_snapshot_from_file(
    *,
    snapshot_tables: dict[str, pd.DataFrame],
    result: "PreprocessResult",
    sheet_name: str,
    file_path: Path,
    entity_col: str,
) -> None:
    """从独立 JSON 读取最新日期快照，覆盖/补齐核心数据块。"""
    try:
        payload = _load_json_file(file_path)
        latest_df = _latest_snapshot_from_timeseries_payload(payload, entity_col=entity_col)
        if latest_df.empty:
            result.warnings.append(f"{file_path.name}: 未找到可解析的最新快照")
            return
        snapshot_tables[sheet_name] = latest_df
        result.table_snapshots[f"{sheet_name}_Latest"] = _dataframe_to_markdown_snapshot(
            f"{sheet_name}_Latest", latest_df
        )
        result.sheet_summaries[sheet_name] = _summarize_timeseries_sheet(sheet_name, latest_df, latest_df)
        print(f"[Analyst] 已刷新最新快照: {file_path.name}")
    except Exception as exc:
        result.warnings.append(f"跳过最新快照 {file_path.name}: {exc}")


@dataclass
class PreprocessResult:
    """结构化预处理产物。"""

    excel_path: Path | None = None
    data_cards: str = ""
    sheet_summaries: dict[str, str] = field(default_factory=dict)
    table_snapshots: dict[str, str] = field(default_factory=dict)
    core_data_pack: str = ""
    observation_start: str = ""
    observation_end: str = ""
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

        try:
            return OpenAIEmbeddings(
                model=model,
                chunk_size=32,
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
                retained_df, snapshot_df = _timeseries_to_dataframes(series, entity_col=entity_col)
                if snapshot_df.empty:
                    continue
                sheets[sheet_key] = retained_df if not retained_df.empty else snapshot_df
                snapshot_tables[sheet_key] = snapshot_df
                result.sheet_summaries[sheet_key] = _summarize_timeseries_sheet(
                    sheet_key, snapshot_df, snapshot_df
                )
                result.table_snapshots[sheet_key] = _dataframe_to_markdown_snapshot(
                    sheet_key, snapshot_df
                )

        def _ingest_leaderboard(name: str, payload: Any) -> None:
            rows = payload if isinstance(payload, list) else []
            df = _leaderboard_to_dataframe(rows)
            if df.empty:
                result.warnings.append(f"{name}: 榜单为空或格式不符")
                return
            sheets[name] = df
            result.sheet_summaries[name] = _summarize_leaderboard_sheet(name, df)
            result.table_snapshots[name] = _dataframe_to_markdown_snapshot(name, df)

        def _ingest_app_rankings(name: str, payload: Any) -> None:
            blocks = _extract_app_ranking_blocks(payload)
            if not blocks:
                result.warnings.append(f"{name}: 未找到可解析的应用榜单")
                return
            for view, rows in blocks:
                df = _app_rankings_to_dataframe(rows, view=view)
                if df.empty:
                    continue
                sheet_key = f"{name}_{view}"
                sheets[sheet_key] = df
                snapshot_tables[sheet_key] = df
                result.sheet_summaries[sheet_key] = _summarize_app_sheet(sheet_key, df)
                result.table_snapshots[sheet_key] = _dataframe_to_markdown_snapshot(sheet_key, df)

        # 1) 优先解析 merged_rankings 内各维度
        if merged_payload:
            if "Market Share" in merged_payload:
                _ingest_timeseries("Market Share", merged_payload["Market Share"])
            if "Top Model" in merged_payload:
                _ingest_timeseries("Top Model", merged_payload["Top Model"], entity_col="model")
            for lb_key in ("day_leaderboard", "week_leaderboard", "month_leaderboard"):
                if lb_key in merged_payload:
                    _ingest_leaderboard(lb_key, merged_payload[lb_key])
            for extra_key in ("Categories", "Context Length", "Programming", "Languages"):
                if extra_key in merged_payload:
                    _ingest_timeseries(extra_key, merged_payload[extra_key], entity_col="model")
            if "Top Apps" in merged_payload:
                _ingest_app_rankings("Top Apps", merged_payload["Top Apps"])

        # 2) 独立 JSON 补全/覆盖（磁盘直读，不完全依赖 pipeline）
        standalone_sources = {
            "Market Share": (_resolve_path(self.data_cfg["market_share"]), "provider"),
            "Top Model": (_resolve_path(self.data_cfg["top_model"]), "model"),
            "Categories": (_resolve_path(self.data_cfg.get("categories", "data/Categories.json")), "model"),
            "Languages": (_resolve_path(self.data_cfg.get("languages", "data/Languages.json")), "model"),
            "Programming": (_resolve_path(self.data_cfg.get("programming", "data/Programming.json")), "model"),
            "Context Length": (_resolve_path(self.data_cfg.get("context_length", "data/Context Length.json")), "model"),
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

        top_apps_path = _resolve_path(self.data_cfg.get("top_apps", "data/Top Apps.json"))
        try:
            payload = _load_json_file(top_apps_path)
            _ingest_app_rankings("Top Apps", payload)
            print(f"[Analyst] 已加载应用榜单: {top_apps_path.name}")
        except Exception as exc:
            result.warnings.append(f"跳过应用榜单 {top_apps_path.name}: {exc}")

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

        result.observation_start, result.observation_end = _observation_range_from_tables(sheets)
        result.core_data_pack = _build_core_data_pack(
            sheets,
            snapshot_tables,
            result.observation_start,
            result.observation_end,
        )
        result.data_cards = _build_data_cards(
            result.sheet_summaries,
            snapshot_tables,
            result.observation_start,
            result.observation_end,
        )
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
            if preprocess_result.core_data_pack:
                docs.append(
                    Document(
                        page_content=preprocess_result.core_data_pack,
                        metadata={"source_type": "core_data_pack"},
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
            for sheet_name, table_md in preprocess_result.table_snapshots.items():
                if table_md.strip():
                    docs.append(
                        Document(
                            page_content=table_md,
                            metadata={"source_type": "table_snapshot", "sheet": sheet_name},
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
                # 结构化摘要、数据卡片和表格快照尽量保持完整，便于检索命中关键指标；
                # 但智谱 Embedding 对单条 input 长度敏感，超长结构化块仍需切分，避免 1210 参数错误。
                if source_type in {"data_card", "sheet_summary", "table_snapshot", "core_data_pack"}:
                    if len(doc.page_content or "") > 8000:
                        chunks.extend(self.text_splitter.split_documents([doc]))
                    else:
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
                or "LLM API industry trend context length open source model adoption"
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

        core_data_block = ""
        if preprocess_result and preprocess_result.core_data_pack:
            core_data_block = f"## 强制核心数据上下文\n{preprocess_result.core_data_pack}\n\n"

        system_prompt = (
            "你是一名资深大模型 API 竞品分析师，专注分析 OpenRouter 等平台的多维 API Token 调用数据。\n"
            "分析框架（三维）：\n"
            "  1. 主体维度 = OpenAI（其 API 调用量、市场份额、代表模型表现、竞争位置）\n"
            "  2. 竞品维度 = Google / Anthropic / DeepSeek / Meta / Mistral 等 API 提供商\n"
            "  3. 行业维度 = 大模型 API 整体趋势（上下文长度、开源模型、调用量变化等）\n"
            "请严格基于提供的检索上下文、结构化数据卡片与强制核心数据上下文作答；"
            "强制核心数据上下文的优先级高于 RAG 片段。若证据不足请明确指出数据缺口，"
            "禁止编造未出现的具体数字、日期、年份或事实。"
            "当前任务发生在 2026 年；当表格 period/date 明确为 2026 年时，严禁改写为 2025 年。"
            "所有周期表述必须逐字来自强制核心数据上下文中的最新周期声明、period/date 或数据观测范围，不得按模型常识自动修正。\n"
            "必须在正文中直接使用已注入的观测范围、Market Share、Top Model、leaderboard、"
            "OpenAI 主体表现与竞品对比等硬数据；不得只写泛泛趋势判断。\n"
            "OpenAI 主体识别口径包括 provider=openai、openai/ 路径，以及 gpt-、chatgpt、o1/o3/o4 等模型名。\n"
            "本报告主题为大模型 API 使用竞品分析，不涉及手机、硬件销量或其他无关品类。\n"
            "输出必须使用以下 Markdown 章节标题（逐字一致，便于下游 Generator 解析回填）：\n"
            f"{section_list}\n"
            "章节内容要求：\n"
            "  - 「一、执行摘要」必须包含数据观测范围与 OpenAI 主体总体位置\n"
            "  - 「二、主流 API 平台使用表现」聚焦 OpenAI 及主流 API 平台 Token 调用与排名\n"
            "  - 「三、大模型 API 行业趋势研判」聚焦行业整体趋势\n"
            "  - 「四、竞品 API 威胁与机会」聚焦 Google/Anthropic/DeepSeek 等竞品\n"
            "  - 「五、战略建议」分短期（0-6月）与中期（6-18月）\n"
            "  - 各章节须有具体数据引用（Token 量、排名、份额变化等）"
        )

        user_prompt = (
            f"## 分析需求\n{user_requirement.strip()}\n\n"
            f"{data_card_block}"
            f"{core_data_block}"
            f"## 检索上下文（RAG）\n{context_block}\n"
            "## 任务\n"
            "请综合以上数据卡片与三维检索证据，撰写大模型 API 竞品分析报告正文。"
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
