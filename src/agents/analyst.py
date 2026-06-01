"""Analyst-Agent：基于 OpenRouter 多源 JSON + 行业 PDF 的 RAG 商业分析。

工作流：
  1) preprocess(): 读取 data/merged_rankings.json，把各榜单 JSON 解析为
     Pandas DataFrame，落盘为 data/processed/openrouter_rankings.xlsx
     （多 Sheet）。同时生成数据资产说明 + 各 Sheet 摘要 + 行业 PDF 文本。
  2) build_knowledge_base(): 对清洗后的多段文本切片 + 智谱 Embedding，
     存入本地 FAISS 向量库。
  3) analyze_data(): 按 OpenAI 主体 / 竞品厂商 / 行业趋势 三个维度分别
     检索，调 DeepSeek 输出结构化 Markdown 报告。

主体定义：主产品 = OpenAI 旗下所有模型；其余厂商均为竞品。
"""

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_RANKINGS_JSON = PROJECT_ROOT / "data" / "merged_rankings.json"
DEFAULT_RANKINGS_XLSX = PROJECT_ROOT / "data" / "processed" / "openrouter_rankings.xlsx"

RETRIEVAL_KEYS = ("openai", "competitor", "industry")
RETRIEVAL_LABELS = {
    "openai": "【OpenAI 主体表现】",
    "competitor": "【竞品厂商格局】",
    "industry": "【行业与应用层趋势】",
}

ZHIPU_EMBEDDING_CHUNK_SIZE = 32

# ────────────────────────────────────────────────────────────────────────────
# Vendor 归一化
# ────────────────────────────────────────────────────────────────────────────
DEFAULT_VENDOR_MAPPING: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "google-vertex": "Google",
    "meta-llama": "Meta",
    "mistralai": "Mistral",
    "deepseek": "DeepSeek",
    "qwen": "Alibaba (Qwen)",
    "alibaba": "Alibaba (Qwen)",
    "x-ai": "xAI",
    "xai": "xAI",
    "moonshotai": "Moonshot",
    "moonshot": "Moonshot",
    "cohere": "Cohere",
    "perplexity": "Perplexity",
    "nousresearch": "Nous Research",
    "01-ai": "01.AI",
    "zhipu": "智谱",
    "baichuan": "Baichuan",
    "stepfun": "StepFun",
    "minimax": "MiniMax",
}
OPENAI_VENDOR_NAME = "OpenAI"


def _normalize_vendor(slug: str, mapping: dict[str, str]) -> tuple[str, str]:
    """从 model_permaslug（形如 `openai/gpt-4o`）抽出 vendor 与 model 名。"""
    if not slug:
        return "Unknown", ""
    parts = slug.split("/", 1)
    prefix = parts[0].lower().strip()
    model_name = parts[1].strip() if len(parts) > 1 else parts[0]
    vendor = mapping.get(prefix, prefix.title() if prefix else "Unknown")
    return vendor, model_name


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# ────────────────────────────────────────────────────────────────────────────
# 配置解析
# ────────────────────────────────────────────────────────────────────────────
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


@dataclass
class AnalysisResult:
    """商业分析输出与 Prompt 骨架。"""

    system_prompt: str
    user_prompt: str
    retrieved_contexts: dict[str, list[str]] = field(default_factory=dict)
    analysis: str | None = None


@dataclass
class PreprocessResult:
    """preprocess 阶段产物。"""

    xlsx_path: Path
    sheet_summaries: dict[str, str]   # sheet_name -> markdown 摘要
    industry_text: str                # 行业 PDF 全文（或空）
    data_card: str                    # 数据资产说明（生成报告用）

# ────────────────────────────────────────────────────────────────────────────
# JSON → DataFrame 解析器
# 各 JSON 文件结构差异较大，逐个写解析；任意 KeyError / 类型异常都返回空 DF。
# ────────────────────────────────────────────────────────────────────────────
def _parse_top_apps(payload: Any, vendor_mapping: dict[str, str]) -> pd.DataFrame:
    """Top Apps.json：{day:[...], week:[...], month:[...]}"""
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return pd.DataFrame(rows)
    for granularity in ("day", "week", "month"):
        items = payload.get(granularity) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            app = item.get("app") or {}
            categories = app.get("categories") or []
            rows.append({
                "granularity": granularity,
                "rank": _to_int(item.get("rank")),
                "app_title": str(app.get("title") or "").strip(),
                "total_tokens": _to_int(item.get("total_tokens")),
                "total_requests": _to_int(item.get("total_requests")),
                "categories": ", ".join(c for c in categories if isinstance(c, str)),
                "origin_url": str(app.get("origin_url") or "").strip(),
                "slug": str(app.get("slug") or "").strip(),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["granularity", "rank"]).reset_index(drop=True)
    _ = vendor_mapping  # 保持签名一致
    return df


def _parse_leaderboard(payload: Any, granularity: str, vendor_mapping: dict[str, str]) -> pd.DataFrame:
    """{day,week,month}_leaderboard.json：list[{rank, model_permaslug, total_tokens, ...}]"""
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return pd.DataFrame(rows)
    for item in payload:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("model_permaslug") or item.get("model_slug") or "").strip()
        vendor, model_name = _normalize_vendor(slug, vendor_mapping)
        rows.append({
            "granularity": granularity,
            "rank": _to_int(item.get("rank")),
            "vendor": vendor,
            "model_slug": slug,
            "model_name": model_name,
            "total_tokens": _to_int(item.get("total_tokens")),
            "total_requests": _to_int(item.get("total_requests")),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["rank"]).reset_index(drop=True)
    return df


def _parse_top_model_trend(payload: Any) -> pd.DataFrame:
    """Top Model.json：{data: [{x: <date>, ys: {model: tokens, ...}}, ...]}"""
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return pd.DataFrame(rows)
    series = payload.get("data") or []
    if not isinstance(series, list):
        return pd.DataFrame(rows)
    for point in series:
        if not isinstance(point, dict):
            continue
        x = str(point.get("x") or "").strip()
        ys = point.get("ys") or {}
        if not isinstance(ys, dict):
            continue
        for model, value in ys.items():
            rows.append({
                "date": x,
                "model": str(model),
                "tokens": _to_int(value),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["date", "tokens"], ascending=[True, False]).reset_index(drop=True)
    return df


def _parse_market_share(payload: Any, vendor_mapping: dict[str, str]) -> pd.DataFrame:
    """Market Share.json：list[{x: <date>, ys: {vendor_key: share, ...}}]（已是 long 化输入）"""
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return pd.DataFrame(rows)
    for point in payload:
        if not isinstance(point, dict):
            continue
        x = str(point.get("x") or "").strip()
        ys = point.get("ys") or {}
        if not isinstance(ys, dict):
            continue
        for vendor_key, share in ys.items():
            vendor = vendor_mapping.get(str(vendor_key).lower(), str(vendor_key))
            try:
                share_val = float(share)
            except (TypeError, ValueError):
                share_val = 0.0
            rows.append({
                "date": x,
                "vendor": vendor,
                "vendor_raw": str(vendor_key),
                "share": share_val,
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["date", "share"], ascending=[True, False]).reset_index(drop=True)
    return df


def _parse_label_value_chart(payload: Any) -> pd.DataFrame:
    """Categories / Languages / Programming / Context Length 这类
    list[{x: <label>, ys: {<single_key>: <value>}}] 通用形态。"""
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return pd.DataFrame(rows)
    for point in payload:
        if not isinstance(point, dict):
            continue
        label = str(point.get("x") or "").strip()
        ys = point.get("ys") or {}
        if not isinstance(ys, dict) or not ys:
            continue
        # 优先取数值最大的那一项作为 value（多数图只有一个 key）
        first_key, first_val = next(iter(ys.items()))
        try:
            value = float(first_val)
        except (TypeError, ValueError):
            value = 0.0
        rows.append({
            "label": label,
            "metric": str(first_key),
            "value": value,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("value", ascending=False).reset_index(drop=True)
    return df


def _build_vendor_summary(leaderboards: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """基于三粒度 leaderboard 汇总各厂商。"""
    rows: list[dict[str, Any]] = []
    for granularity, df in leaderboards.items():
        if df is None or df.empty:
            continue
        total_tokens_all = df["total_tokens"].sum() or 1
        grouped = (
            df.groupby("vendor", dropna=False)
            .agg(
                model_count=("model_slug", "nunique"),
                tokens_sum=("total_tokens", "sum"),
                requests_sum=("total_requests", "sum"),
                top_model=("model_name", lambda s: s.iloc[0] if len(s) else ""),
                best_rank=("rank", "min"),
            )
            .reset_index()
        )
        grouped["granularity"] = granularity
        grouped["tokens_share_pct"] = (grouped["tokens_sum"] / total_tokens_all * 100).round(2)
        grouped = grouped.sort_values(["granularity", "tokens_sum"], ascending=[True, False])
        rows.extend(grouped.to_dict("records"))
    if not rows:
        return pd.DataFrame()
    summary = pd.DataFrame(rows)
    cols = [
        "granularity", "vendor", "model_count", "tokens_sum",
        "tokens_share_pct", "requests_sum", "best_rank", "top_model",
    ]
    return summary.reindex(columns=cols)

# ────────────────────────────────────────────────────────────────────────────
# AnalystAgent 主类
# ────────────────────────────────────────────────────────────────────────────
class AnalystAgent:
    """OpenRouter 多源 JSON + AI Index PDF → 多 Sheet 表格 + RAG 商业分析。

    主体定义：主产品 = OpenAI（旗下所有模型）；其余厂商 = 竞品。
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self.analyst_cfg = self.config.get("analyst", {})

        # vendor 映射（yaml 优先；缺省回退默认表）
        cfg_mapping = self.analyst_cfg.get("vendor_mapping") or {}
        self.vendor_mapping: dict[str, str] = {
            **DEFAULT_VENDOR_MAPPING,
            **{str(k).lower(): str(v) for k, v in cfg_mapping.items()},
        }

        self.zhipu_settings = resolve_zhipu_embedding_settings(self.config)
        self.openai_settings = resolve_openai_settings(self.config, "analyst")

        self.embeddings = self._init_embeddings()
        self.llm = self._init_llm()
        self.text_splitter = self._init_text_splitter()

        self.vectorstore: FAISS | None = None
        self._top_k = int(self.analyst_cfg.get("rag", {}).get("top_k", 3))

        # preprocess 产物（缓存供后续步骤复用）
        self._last_preprocess: PreprocessResult | None = None

    # ── 配置加载 ─────────────────────────────────────────────────────────────

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
        debug_cfg = self.analyst_cfg.get("debug", {})
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
            raise RuntimeError("未配置智谱 base_url。")

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
                f"初始化智谱 Embedding 失败（model={model}）: {exc}"
            ) from exc

    def _init_llm(self) -> ChatOpenAI:
        openai_kwargs = self.openai_settings["openai_kwargs"]
        if not openai_kwargs.get("api_key"):
            raise RuntimeError("未配置 DeepSeek API Key。")
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
        rag_cfg = self.analyst_cfg.get("rag", {})
        return RecursiveCharacterTextSplitter(
            chunk_size=int(rag_cfg.get("chunk_size", 600)),
            chunk_overlap=int(rag_cfg.get("chunk_overlap", 80)),
        )

    # ────────────────────────────────────────────────────────────────────
    # Step 1: preprocess —— 多源 JSON → 多 Sheet xlsx + 文本摘要
    # ────────────────────────────────────────────────────────────────────

    def preprocess(
        self,
        raw_texts: list[dict[str, Any]] | None = None,
        rankings_json: str | Path | None = None,
        xlsx_output: str | Path | None = None,
    ) -> PreprocessResult:
        """读 OpenRouter merged_rankings.json + 行业 PDF 文本 → 多 Sheet xlsx + 各 Sheet 摘要。

        优先级：
          - 如果 raw_texts 中存在 source_type=industry_report_pdf，则用它作为行业文本；
          - rankings_json：默认 data/merged_rankings.json，也可从 raw_texts 推断（暂保留 JSON 路径）。
        """
        cfg = self.analyst_cfg.get("preprocess", {}) or {}
        rankings_path = Path(rankings_json) if rankings_json else Path(
            cfg.get("rankings_json") or DEFAULT_RANKINGS_JSON
        )
        if not rankings_path.is_absolute():
            rankings_path = PROJECT_ROOT / rankings_path
        xlsx_path = Path(xlsx_output) if xlsx_output else Path(
            cfg.get("output_xlsx") or DEFAULT_RANKINGS_XLSX
        )
        if not xlsx_path.is_absolute():
            xlsx_path = PROJECT_ROOT / xlsx_path

        if not rankings_path.exists():
            raise FileNotFoundError(f"merged_rankings.json 不存在: {rankings_path}")

        merged: dict[str, Any] = json.loads(rankings_path.read_text(encoding="utf-8"))

        # ── 解析各 JSON ─────────────────────────────────────────────────────
        sheets: dict[str, pd.DataFrame] = {}

        # Top Apps（含 day/week/month）
        if "Top Apps" in merged:
            df = _parse_top_apps(merged["Top Apps"], self.vendor_mapping)
            if not df.empty:
                sheets["top_apps"] = df

        # 三粒度 leaderboard
        leaderboards: dict[str, pd.DataFrame] = {}
        for gran in ("day", "week", "month"):
            key = f"{gran}_leaderboard"
            if key in merged:
                df = _parse_leaderboard(merged[key], gran, self.vendor_mapping)
                if not df.empty:
                    leaderboards[gran] = df
                    sheets[f"leaderboard_{gran}"] = df

        # 厂商汇总（基于 leaderboards 计算）
        vendor_summary = _build_vendor_summary(leaderboards)
        if not vendor_summary.empty:
            sheets["vendor_summary"] = vendor_summary

        # Top Model 时间序列
        if "Top Model" in merged:
            df = _parse_top_model_trend(merged["Top Model"])
            if not df.empty:
                sheets["top_model_trend"] = df

        # Market Share 时间序列
        if "Market Share" in merged:
            df = _parse_market_share(merged["Market Share"], self.vendor_mapping)
            if not df.empty:
                sheets["market_share_trend"] = df

        # 标签-数值类图表
        for label_key, sheet_name in (
            ("Categories", "categories"),
            ("Languages", "languages"),
            ("Programming", "programming"),
            ("Context Length", "context_length"),
        ):
            if label_key in merged:
                df = _parse_label_value_chart(merged[label_key])
                if not df.empty:
                    sheets[sheet_name] = df

        if not sheets:
            raise RuntimeError(
                f"merged_rankings.json 中没有解析到任何已知榜单数据。"
                f"请检查 {rankings_path}"
            )

        # ── 落盘 xlsx ───────────────────────────────────────────────────────
        xlsx_path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name[:31], index=False)
        print(f"[Analyst] OpenRouter 多 Sheet 表格已写入 -> {xlsx_path}")

        # ── 生成各 Sheet 的 markdown 摘要（供 RAG 使用） ─────────────────────
        sheet_summaries: dict[str, str] = {}
        for name, df in sheets.items():
            sheet_summaries[name] = self._summarize_sheet(name, df)

        # ── 行业 PDF 文本（从 raw_texts 中取） ──────────────────────────────
        industry_text = ""
        if raw_texts:
            for item in raw_texts:
                if not isinstance(item, dict):
                    continue
                if item.get("source_type") == "industry_report_pdf":
                    industry_text = str(item.get("text") or "").strip()
                    break

        # ── 数据资产说明 ────────────────────────────────────────────────────
        data_card = self._build_data_card(sheets, xlsx_path, bool(industry_text))

        result = PreprocessResult(
            xlsx_path=xlsx_path,
            sheet_summaries=sheet_summaries,
            industry_text=industry_text,
            data_card=data_card,
        )
        self._last_preprocess = result
        return result

    # ── 摘要生成器 ───────────────────────────────────────────────────────────

    @staticmethod
    def _df_to_markdown(df: pd.DataFrame, max_rows: int = 30) -> str:
        return df.head(max_rows).fillna("").to_markdown(index=False)

    def _summarize_sheet(self, name: str, df: pd.DataFrame) -> str:
        """为单个 sheet 生成自然语言 + 关键表格的摘要。"""
        if df.empty:
            return f"### sheet: {name}\n\n（无数据）\n"
        lines: list[str] = [f"### sheet: {name}（{len(df)} 行）"]

        if name == "vendor_summary":
            for gran in df["granularity"].unique():
                sub = df[df["granularity"] == gran].head(10)
                lines.append(f"\n#### 厂商汇总（{gran} 粒度，前 10）")
                lines.append(self._df_to_markdown(sub))
                # OpenAI 单独标记
                openai_row = sub[sub["vendor"] == OPENAI_VENDOR_NAME]
                if not openai_row.empty:
                    r = openai_row.iloc[0]
                    lines.append(
                        f"- **OpenAI 主体**：上榜 {int(r['model_count'])} 个模型，"
                        f"tokens={int(r['tokens_sum']):,}，份额≈{r['tokens_share_pct']:.2f}%，"
                        f"代表模型：{r['top_model']}（最佳排名 #{int(r['best_rank'])}）"
                    )
        elif name.startswith("leaderboard_"):
            head = df.head(20)
            lines.append(self._df_to_markdown(head))
            top_vendors = (
                df.groupby("vendor")["total_tokens"].sum().sort_values(ascending=False).head(8)
            )
            lines.append("\n**该粒度厂商 tokens 排行（Top 8）**")
            for v, t in top_vendors.items():
                lines.append(f"- {v}: {int(t):,}")
        elif name == "top_apps":
            lines.append(self._df_to_markdown(df.head(30)))
        elif name == "market_share_trend":
            # 取最新日期与最早日期对比
            dates = sorted(df["date"].dropna().unique().tolist())
            if dates:
                first, last = dates[0], dates[-1]
                first_df = (
                    df[df["date"] == first].sort_values("share", ascending=False).head(8)
                )
                last_df = (
                    df[df["date"] == last].sort_values("share", ascending=False).head(8)
                )
                lines.append(f"\n**起始日期 {first} 各厂商份额（Top 8）**")
                lines.append(self._df_to_markdown(first_df[["vendor", "share"]]))
                lines.append(f"\n**最新日期 {last} 各厂商份额（Top 8）**")
                lines.append(self._df_to_markdown(last_df[["vendor", "share"]]))
        elif name == "top_model_trend":
            dates = sorted(df["date"].dropna().unique().tolist())
            if dates:
                last = dates[-1]
                last_df = df[df["date"] == last].sort_values("tokens", ascending=False).head(15)
                lines.append(f"\n**最新日期 {last} 模型 tokens 排行（Top 15）**")
                lines.append(self._df_to_markdown(last_df[["model", "tokens"]]))
        else:
            lines.append(self._df_to_markdown(df.head(25)))

        return "\n".join(lines) + "\n"

    @staticmethod
    def _build_data_card(
        sheets: dict[str, pd.DataFrame],
        xlsx_path: Path,
        has_industry: bool,
    ) -> str:
        # 从带 date 列的 sheet 中抽出真实时间范围
        date_pool: set[str] = set()
        for df in sheets.values():
            if "date" in df.columns:
                date_pool.update(d for d in df["date"].astype(str).tolist() if d and d != "nan")
        period_line = ""
        if date_pool:
            sorted_dates = sorted(date_pool)
            period_line = (
                f"- 数据观测时间范围：{sorted_dates[0]} → {sorted_dates[-1]}"
                f"（共 {len(sorted_dates)} 个时间点）"
            )

        lines = ["# 数据资产说明（Analyst 预处理产物）", ""]
        lines.append(f"- 多 Sheet 表格：`{xlsx_path}`")
        lines.append(f"- 共 {len(sheets)} 个 Sheet：")
        for name, df in sheets.items():
            cols = ", ".join(df.columns)
            lines.append(f"  - `{name}`：{len(df)} 行；列：{cols}")
        if period_line:
            lines.append(period_line)
        if has_industry:
            lines.append("- 行业报告：Stanford HAI AI Index 2026 Chapter 4 (Economy)，已抽取文本")
        else:
            lines.append("- 行业报告：未提供（行业维度仅依赖 OpenRouter 数据）")
        lines.append("")
        lines.append(
            "> 主体定义：主产品 = OpenAI（旗下所有模型）；其余厂商均视为竞品。"
            " 数据来源于 OpenRouter API 中转站真实调用日志，反映通过 API 直接消费的开发者偏好，"
            " 不代表全市场。撰写报告时若提及年份，请直接使用上述观测时间范围里的真实日期，"
            "不要写未在数据中出现的年份。"
        )
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────────────────
    # Step 2: 构建 RAG 知识库
    # ────────────────────────────────────────────────────────────────────

    def build_knowledge_base(
        self,
        preprocess_result: PreprocessResult | None = None,
        raw_texts: list[dict[str, Any]] | None = None,
    ) -> int:
        """把预处理后的 Sheet 摘要 + 行业 PDF 切片入 FAISS。"""
        if preprocess_result is None:
            preprocess_result = self.preprocess(raw_texts=raw_texts)

        documents: list[Document] = []

        # 数据资产说明
        documents.append(Document(
            page_content=preprocess_result.data_card,
            metadata={"source_type": "data_card"},
        ))

        # 各 Sheet 摘要
        for sheet_name, summary in preprocess_result.sheet_summaries.items():
            if not summary.strip():
                continue
            tag = self._sheet_to_dimension_tag(sheet_name)
            documents.append(Document(
                page_content=summary,
                metadata={
                    "source_type": "openrouter_sheet",
                    "sheet": sheet_name,
                    "dimension": tag,
                },
            ))

        # 行业 PDF（直接切块入库，不二次摘要）
        if preprocess_result.industry_text:
            documents.append(Document(
                page_content=preprocess_result.industry_text,
                metadata={"source_type": "industry_report_pdf", "dimension": "industry"},
            ))

        if not documents:
            raise ValueError("没有可用的预处理文本，无法构建知识库")

        chunks = self.text_splitter.split_documents(documents)
        if not chunks:
            raise ValueError("文本切片结果为空")

        self.vectorstore = FAISS.from_documents(chunks, self.embeddings)

        persist_dir = self.analyst_cfg.get("rag", {}).get("vector_store_dir")
        if persist_dir:
            save_path = PROJECT_ROOT / persist_dir
            try:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                self.vectorstore.save_local(str(save_path))
            except Exception:
                pass

        return len(chunks)

    @staticmethod
    def _sheet_to_dimension_tag(sheet_name: str) -> str:
        """把 sheet 名映射到 openai / competitor / industry 维度标签。"""
        if sheet_name in {"top_apps", "categories", "languages", "programming", "context_length"}:
            return "industry"
        # leaderboard / vendor_summary / market_share / top_model_trend 同时含 openai 与竞品信息
        return "openai+competitor"

    # ────────────────────────────────────────────────────────────────────
    # Step 3: 商业分析（三维度检索 + LLM 生成）
    # ────────────────────────────────────────────────────────────────────

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
        retrieval_cfg = self.analyst_cfg.get("retrieval", {})
        return {
            "openai": retrieval_cfg.get(
                "openai_query",
                "OpenAI GPT 模型 OpenRouter tokens 排名 份额 vendor_summary",
            ),
            "competitor": retrieval_cfg.get(
                "competitor_query",
                "Anthropic Google Meta DeepSeek xAI Qwen 模型 份额 排名 竞品 leaderboard",
            ),
            "industry": retrieval_cfg.get(
                "industry_query",
                "AI Index 经济 应用 类别 编程语言 上下文长度 趋势 OpenRouter Top Apps",
            ),
        }

    def _build_prompts(
        self,
        user_requirement: str,
        contexts: dict[str, list[str]],
        data_card: str,
    ) -> tuple[str, str]:
        def _format_block(key: str) -> str:
            label = RETRIEVAL_LABELS[key]
            snippets = contexts.get(key) or []
            if not snippets:
                return f"{label}\n（未检索到相关内容）\n"
            body = "\n\n---\n\n".join(f"片段 {i + 1}:\n{s}" for i, s in enumerate(snippets))
            return f"{label}\n{body}\n"

        context_block = "\n".join(_format_block(key) for key in RETRIEVAL_KEYS)

        system_prompt = (
            "你是一名资深 AI 行业商业战略分析师，本次任务是基于 OpenRouter "
            "（API 中转站）的真实模型调用数据 + Stanford HAI AI Index 行业报告，"
            "撰写一份结构化的厂商竞争格局分析。\n\n"
            "【主体定义】\n"
            "- 主产品：OpenAI 旗下所有模型（vendor 列等于 'OpenAI'）。\n"
            "- 竞品：除 OpenAI 之外的所有厂商（Anthropic / Google / Meta / "
            "Mistral / DeepSeek / Alibaba (Qwen) / xAI / Moonshot 等）。\n\n"
            "【数据范围与口径】\n"
            "- OpenRouter 数据来自该 API 中转站真实调用日志，反映通过 API 直接"
            "消费的开发者偏好，不能简单等同于全市场份额。\n"
            "- 数据中的日期是周数据的代表日（例如 2026-03-09 代表该日所在周）；"
            "请从原始数据中推断时间区间，不要写死任何年份。\n"
            "- AI Index Chapter 4 仅作为行业宏观趋势佐证，不作为 OpenRouter 份额"
            "的来源。\n\n"
            "【硬性约束】\n"
            "1. 严格基于检索上下文作答；任何未在上下文中出现的具体数字、份额、"
            "厂商、模型名称都不得编造。\n"
            "2. 涉及年份时，必须直接引用上下文里出现的日期；不允许把 2024 写成 "
            "2025、不允许凭空给「年度」贴标签。\n"
            "3. 若某维度证据不足，请在该章节明确指出「数据缺口」。\n\n"
            "【输出 Markdown 章节（顺序固定）】\n"
            "## 执行摘要\n"
            "## OpenAI 在 OpenRouter 的表现\n"
            "## 竞品厂商格局\n"
            "## 应用与生态趋势\n"
            "## 行业宏观趋势\n"
            "## 战略建议（分短期/中期）\n"
            "## 风险提示\n\n"
            "【表达要求】关键章节请使用 Markdown 表格列出排名 / 份额 / tokens 等"
            "数值（直接从上下文复制，不要四舍五入到看不出原始数据）。"
        )

        user_prompt = (
            f"## 数据资产卡\n{data_card.strip()}\n\n"
            f"## 分析需求\n{user_requirement.strip()}\n\n"
            f"## 检索上下文（RAG）\n{context_block}\n"
            "## 任务\n"
            "请综合以上证据，按系统消息要求的章节顺序输出完整 Markdown 报告。"
        )
        return system_prompt, user_prompt

    def analyze_data(self, user_requirement: str, k: int | None = None) -> AnalysisResult:
        requirement = (user_requirement or "").strip()
        if not requirement:
            raise ValueError("user_requirement 不能为空")

        try:
            queries = self._get_retrieval_queries()
            contexts: dict[str, list[str]] = {}
            for key in RETRIEVAL_KEYS:
                contexts[key] = self.retrieve_context(queries[key], k=k)

            data_card = (
                self._last_preprocess.data_card
                if self._last_preprocess is not None
                else "（数据资产卡未生成）"
            )
            system_prompt, user_prompt = self._build_prompts(requirement, contexts, data_card)
            result = AnalysisResult(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                retrieved_contexts=contexts,
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
            raise RuntimeError(f"商业分析失败: {exc}") from exc

    # ────────────────────────────────────────────────────────────────────
    # Pipeline 适配接口
    # ────────────────────────────────────────────────────────────────────
    def run(
        self,
        raw_texts: list[dict[str, Any]],
        user_requirement: str,
        k: int | None = None,
    ) -> str:
        """Pipeline 调用入口：preprocess → build_knowledge_base → analyze_data。"""
        preprocess_result = self.preprocess(raw_texts=raw_texts)
        self.build_knowledge_base(preprocess_result=preprocess_result)
        result = self.analyze_data(user_requirement, k=k)
        return (result.analysis or "").strip()



