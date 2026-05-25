"""Analyst-Agent：RAG 知识库构建与商业数据分析（双模型混用）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.utils.config_loader import ConfigError, load_app_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

RETRIEVAL_KEYS = ("sales", "industry", "competitor")
RETRIEVAL_LABELS = {
    "sales": "【自身销售】",
    "industry": "【行业趋势】",
    "competitor": "【竞品威胁】",
}

# 智谱 Embedding 单次请求 input 上限为 64 条；LangChain 按 chunk_size 自动分批
ZHIPU_EMBEDDING_CHUNK_SIZE = 32


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


class AnalystAgent:
    """双模型混用：智谱 Embedding（RAG）+ DeepSeek Chat（商业分析）。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self.analyst_cfg = self.config.get("analyst", {})

        self.zhipu_settings = resolve_zhipu_embedding_settings(self.config)
        self.openai_settings = resolve_openai_settings(self.config, "analyst")

        self.embeddings = self._init_embeddings()
        self.llm = self._init_llm()
        self.text_splitter = self._init_text_splitter()

        self.vectorstore: FAISS | None = None
        self._top_k = int(self.analyst_cfg.get("rag", {}).get("top_k", 3))

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
        """使用智谱 AI（zhipu 段）初始化 OpenAIEmbeddings。"""
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
            raise RuntimeError(
                "未配置智谱 base_url。请在 config/config.yaml 的 zhipu.base_url 中填写。"
            )

        model = self.zhipu_settings["embedding_model"]
        zhipu_cfg = self.config.get("zhipu") or {}
        batch_size = int(zhipu_cfg.get("embedding_batch_size", ZHIPU_EMBEDDING_CHUNK_SIZE))
        if not 1 <= batch_size <= 64:
            raise ValueError(
                f"zhipu.embedding_batch_size 须在 1~64 之间，当前为: {batch_size}"
            )

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
        """使用 DeepSeek（openai 段）初始化 ChatOpenAI。"""
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
            raise RuntimeError(
                f"初始化 DeepSeek ChatOpenAI 失败（model={model}）: {exc}"
            ) from exc

    def _init_text_splitter(self) -> RecursiveCharacterTextSplitter:
        rag_cfg = self.analyst_cfg.get("rag", {})
        return RecursiveCharacterTextSplitter(
            chunk_size=int(rag_cfg.get("chunk_size", 500)),
            chunk_overlap=int(rag_cfg.get("chunk_overlap", 50)),
        )

    @staticmethod
    def _normalize_raw_texts(raw_texts: list) -> list[Document]:
        documents: list[Document] = []
        for index, item in enumerate(raw_texts):
            try:
                if isinstance(item, str):
                    text = item.strip()
                    metadata = {"source_index": index, "source_type": "raw_text"}
                elif isinstance(item, dict):
                    text = str(item.get("text", "") or item.get("content", "")).strip()
                    metadata = {k: v for k, v in item.items() if k not in {"text", "content"}}
                    metadata.setdefault("source_index", index)
                else:
                    continue

                if not text:
                    continue

                documents.append(Document(page_content=text, metadata=metadata))
            except Exception:
                continue

        if not documents:
            raise ValueError("raw_texts 中无有效文本，无法构建知识库")

        return documents

    def build_knowledge_base(self, raw_texts: list) -> int:
        """对原始文本切片，经智谱 Embedding 构建 FAISS 向量库。"""
        try:
            source_docs = self._normalize_raw_texts(raw_texts)
            chunks = self.text_splitter.split_documents(source_docs)

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
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"构建知识库失败（智谱 Embedding）: {exc}") from exc

    def retrieve_context(self, query: str, k: int | None = None) -> list[str]:
        """从 FAISS 向量库检索与 query 最相关的文本片段。"""
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
            "sales": retrieval_cfg.get("sales_query", "自身销售 销量 销售额"),
            "industry": retrieval_cfg.get("industry_query", "行业趋势 市场规模 增长"),
            "competitor": retrieval_cfg.get("competitor_query", "竞品威胁 竞争对手 定价"),
        }

    def _build_prompts(
        self,
        user_requirement: str,
        contexts: dict[str, list[str]],
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
            "你是一名资深商业战略分析师，擅长整合多源数据（销售报表、行业报告、竞品情报）"
            "撰写结构化、可落地的深度分析。\n"
            "请严格基于提供的检索上下文作答，若某维度证据不足请明确指出数据缺口，"
            "禁止编造未出现的具体数字或事实。\n"
            "输出须包含以下 Markdown 章节：\n"
            "## 执行摘要\n"
            "## 自身销售表现\n"
            "## 行业趋势研判\n"
            "## 竞品威胁与机会\n"
            "## 战略建议（分短期/中期）\n"
            "## 风险提示"
        )

        user_prompt = (
            f"## 分析需求\n{user_requirement.strip()}\n\n"
            f"## 检索上下文（RAG）\n{context_block}\n"
            "## 任务\n"
            "请综合以上三类证据，完成一份深度商业分析报告。"
        )

        return system_prompt, user_prompt

    def analyze_data(self, user_requirement: str, k: int | None = None) -> AnalysisResult:
        """
        分维度 RAG 检索（智谱向量库）+ DeepSeek Chat 生成深度商业分析。
        """
        requirement = (user_requirement or "").strip()
        if not requirement:
            raise ValueError("user_requirement 不能为空")

        try:
            queries = self._get_retrieval_queries()
            contexts: dict[str, list[str]] = {}
            for key in RETRIEVAL_KEYS:
                contexts[key] = self.retrieve_context(queries[key], k=k)

            system_prompt, user_prompt = self._build_prompts(requirement, contexts)
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

    def run(self, raw_texts: list, user_requirement: str, k: int | None = None) -> str:
        self.build_knowledge_base(raw_texts)
        result = self.analyze_data(user_requirement, k=k)
        return (result.analysis or "").strip()
