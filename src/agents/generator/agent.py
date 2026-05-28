"""
Generator-Agent：通用商业分析报告生成智能体
=============================================

功能概述
--------
1. 【润色】  调用 DeepSeek 对各章节进行市场报告风格润色，不修改任何数据。
2. 【图表】  调用 DeepSeek 判断每节是否值得插图，并提取图表结构（标题/类型/数据）。
3. 【渲染】  用 matplotlib 以灰蓝色调渲染图表，保存 PNG 至输出目录。
4. 【组装】  将润色正文 + 图表引用拼装为完整 Markdown 报告，写入本地文件。

设计原则
--------
- 通用性：章节结构、报告标题、输出路径均可配置，不绑定具体产品或业务。
- 鲁棒性：每个 DeepSeek 调用都有独立 try/except，任意步骤失败只降级，不中断。
- 零侵入：完全兼容原 GeneratorAgent 接口（generate_markdown_report / save_report）。
- 无额外依赖：HTTP 客户端仅用标准库 urllib；matplotlib/numpy 为可选依赖。

快速使用
--------
命令行（最简）::

    python generator.py --input path/to/analysis.txt

命令行（完整参数）::

    python generator.py \\
        --input  path/to/analysis.txt \\
        --output path/to/report.md \\
        --output-dir path/to/data/ \\
        --api-key YOUR_DEEPSEEK_API_KEY \\
        --title  "2025年XX产品市场分析报告" \\
        --no-polish   # 跳过润色（调试用）
        --no-chart    # 跳过图表（调试用）

框架内调用::

    from src.agents.generator.generator import GeneratorAgent

    agent = GeneratorAgent(
        api_key     = "YOUR_DEEPSEEK_API_KEY",
        output_dir  = r"D:\\...\\data",
    )
    saved = agent.run_from_file("path/to/analysis.txt")
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ── 可选：matplotlib（图表渲染）──────────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import numpy as np
    _MPL_OK = True
except ImportError:
    _MPL_OK = False

# ── 可选：项目内部配置加载（框架内使用时启用，独立运行时自动降级）────────────
try:
    from src.utils.config_loader import ConfigError, load_app_config
    _CONFIG_OK = True
except ImportError:
    _CONFIG_OK = False
    ConfigError = Exception  # type: ignore[assignment,misc]


# ════════════════════════════════════════════════════════════════════════════════
# ① 全局常量与默认配置
# ════════════════════════════════════════════════════════════════════════════════

PROJECT_ROOT        = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# 报告输出目录：与本脚本同级的 data/ 子目录（可被外部覆盖）
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "data"

# ── 默认章节结构（可在初始化时替换为任意章节）────────────────────────────────
DEFAULT_REPORT_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("一、执行摘要",       ("执行摘要", "摘要", "overview", "executive summary")),
    ("二、自身销售表现",   ("自身销售表现", "自身销售", "销售表现", "销售业绩")),
    ("三、行业趋势研判",   ("行业趋势研判", "行业趋势", "行业研判", "市场趋势")),
    ("四、竞品威胁与机会", ("竞品威胁与机会", "竞品威胁", "竞争态势", "竞品分析")),
    ("五、战略建议",       ("战略建议", "策略建议", "行动建议", "建议")),
    ("六、风险提示",       ("风险提示", "风险", "风险因素")),
)

DEFAULT_REPORT_TITLE = "2025年某产品销售与市场分析报告"

# ── DeepSeek API ─────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY   = "YOUR_DEEPSEEK_API_KEY"   # ← 替换为真实 Key，或通过参数传入
DEEPSEEK_BASE_URL  = "https://api.deepseek.com"
DEEPSEEK_MODEL     = "deepseek-chat"            # deepseek-v3 的 chat endpoint
DEEPSEEK_TIMEOUT   = 120                        # 单次请求超时（秒）
DEEPSEEK_MAX_RETRY = 3                          # 网络错误最大重试次数

# ── 图表配色方案（灰蓝色调）─────────────────────────────────────────────────
_PALETTE = ["#2D5F8A", "#4A8DB7", "#7FB3D3", "#A8C8E0",
            "#6B8FA8", "#3D7A9E", "#9BB8CC", "#5A7D96"]
_BG      = "#F4F7FA"   # 背景色
_GRID    = "#D0DCE8"   # 网格线
_TEXT    = "#2C3E50"   # 文字
_ACCENT  = "#2D5F8A"   # 强调色（折线/面积）

# ── 支持的图表类型 ────────────────────────────────────────────────────────────
CHART_TYPES = ("bar", "line", "pie", "horizontal_bar")


# ════════════════════════════════════════════════════════════════════════════════
# ② DeepSeek HTTP 客户端（纯标准库，零额外依赖）
# ════════════════════════════════════════════════════════════════════════════════

class DeepSeekClient:
    """
    轻量级 DeepSeek Chat API 客户端。

    仅依赖 Python 标准库 urllib，无需安装 openai / httpx 等第三方包。
    支持指数退避自动重试，适用于网络不稳定环境。
    """

    def __init__(
        self,
        api_key:  str = DEEPSEEK_API_KEY,
        base_url: str = DEEPSEEK_BASE_URL,
        timeout:  int = DEEPSEEK_TIMEOUT,
    ) -> None:
        if not api_key or api_key == "YOUR_DEEPSEEK_API_KEY":
            raise ValueError(
                "请提供有效的 DeepSeek API Key。\n"
                "方式一：修改脚本顶部 DEEPSEEK_API_KEY 常量。\n"
                "方式二：命令行传入 --api-key YOUR_KEY。\n"
                "方式三：GeneratorAgent(api_key='YOUR_KEY')。"
            )
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

    def chat(
        self,
        messages:    list[dict[str, str]],
        model:       str   = DEEPSEEK_MODEL,
        temperature: float = 0.3,
        max_tokens:  int   = 4096,
    ) -> str:
        """
        发送对话请求，返回助手回复的纯文本。

        Args:
            messages:    符合 OpenAI 格式的消息列表。
            model:       模型名称。
            temperature: 生成温度，润色用 0.2~0.3，结构化输出用 0.1。
            max_tokens:  最大生成 token 数。

        Returns:
            模型回复的文本字符串。

        Raises:
            RuntimeError: API 调用失败且超出重试次数时抛出。
        """
        url     = f"{self.base_url}/v1/chat/completions"
        payload = json.dumps({
            "model":       model,
            "messages":    messages,
            "temperature": temperature,
            "max_tokens":  max_tokens,
        }).encode("utf-8")
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        last_err: Exception | None = None
        for attempt in range(1, DEEPSEEK_MAX_RETRY + 1):
            try:
                req = urllib.request.Request(
                    url, data=payload, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    return body["choices"][0]["message"]["content"].strip()

            except urllib.error.HTTPError as exc:
                detail   = exc.read().decode("utf-8", errors="replace")
                last_err = RuntimeError(f"DeepSeek HTTP {exc.code}: {detail[:300]}")
                if exc.code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                raise last_err from exc

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_err = RuntimeError(f"DeepSeek 网络错误（第{attempt}次）: {exc}")
                time.sleep(2 ** attempt)

        raise last_err or RuntimeError("DeepSeek 请求失败，已超出重试次数")


# ════════════════════════════════════════════════════════════════════════════════
# ③ 润色模块：让章节内容更符合市场报告文风
# ════════════════════════════════════════════════════════════════════════════════

_POLISH_SYSTEM = """\
你是一位资深市场研究分析师，擅长撰写专业的商业市场分析报告。

【任务】对用户提供的报告章节文本进行语义润色：
  - 使语言更规范、表达更专业，符合正式市场分析报告的行文要求。
  - 疏通逻辑衔接，使段落过渡自然流畅。
  - 适当提升词汇精准度和句式多样性。

【严格禁止】
  1. 不得修改、增减或捏造任何数值、百分比、年份、产品名称、机构名称等具体数据。
  2. 不得改变原文的核心结论与事实判断。
  3. 不得删除任何原有内容段落或要点。
  4. 保留原有 Markdown 格式标记（**加粗**、- 列表、### 小标题等）。
  5. 清除文本中任何形如"【章节标题】..."的提示词残留，不要将其输出。

【输出要求】直接输出润色后的文本，不要添加任何说明、前缀、后缀或注释。\
"""


def polish_section(
    client:        DeepSeekClient,
    section_title: str,
    body:          str,
) -> str:
    """
    对单节内容进行市场报告风格润色。

    失败时自动降级返回原文（不中断整体流程）。
    同时清除原文中可能残留的 prompt 标记（如"【章节标题】..."）。
    """
    body = re.sub(r"【章节标题】[^\n]*\n?", "", body).strip()

    if not body or body.startswith("（暂无数据"):
        return body

    try:
        messages = [
            {"role": "system", "content": _POLISH_SYSTEM},
            {"role": "user",   "content": (
                f"请对以下市场分析报告章节进行语义润色。\n\n"
                f"【章节】{section_title}\n\n"
                f"【原文】\n{body}"
            )},
        ]
        result = client.chat(messages, temperature=0.25, max_tokens=3000)
        result = re.sub(r"【章节标题】[^\n]*\n?", "", result).strip()
        return result if result else body
    except Exception as exc:
        print(f"    [WARN] 润色失败（{section_title}）: {exc}，使用原文。")
        return body


# ════════════════════════════════════════════════════════════════════════════════
# ④ 图表判断模块：让 DeepSeek 决定是否插图并提取数据
# ════════════════════════════════════════════════════════════════════════════════

_CHART_JUDGE_SYSTEM = """\
你是一位数据可视化专家，负责判断市场分析报告的章节是否需要插入图表来增强表达效果。

【判断标准】
  需要图表：文本中包含 3 个及以上可量化比较的数值（如多时期销量、多维度收入、预测数列等）。
  不需要图表：纯文字策略/建议/风险描述，或数值不足 3 个，或已有图表足以覆盖。

【图表类型选择】
  bar            → 同一时期多维度对比（如多渠道/多客群收入对比）
  line           → 随时间变化的趋势或预测数据（年度/季度增长曲线）
  pie            → 某一时期各组成部分的占比结构
  horizontal_bar → 多个项目横向排名或对比（项目名称较长时优先）

【若需要图表】严格输出以下 JSON，不要包含任何其他内容：
{
  "need_chart": true,
  "chart_type": "bar|line|pie|horizontal_bar",
  "title": "简洁图表标题（不超过25字）",
  "x_label": "X轴标签（饼图填空字符串）",
  "y_label": "Y轴标签（饼图填空字符串）",
  "labels": ["标签1", "标签2", ...],
  "values": [数值1, 数值2, ...]
}

【若不需要图表】输出：
{"need_chart": false}

【数据约束】
  - values 全部为纯数字（int 或 float），不带单位、货币符号或百分号。
  - values 数值必须与原文完全一致，严禁捏造或推算。
  - labels 与 values 长度必须相同，且至少包含 3 个元素。
  - chart_type 只能是上述四种之一。\
"""


def judge_and_extract_chart(
    client:        DeepSeekClient,
    section_title: str,
    body:          str,
) -> dict[str, Any] | None:
    """
    让 DeepSeek 判断章节是否需要图表，并提取结构化图表元数据。

    Returns:
        图表元数据字典，或 None（无需图表 / 提取失败）。
    """
    if not body or body.startswith("（暂无数据"):
        return None
    try:
        messages = [
            {"role": "system", "content": _CHART_JUDGE_SYSTEM},
            {"role": "user",   "content": (
                f"请判断以下章节是否需要插入图表，并按要求输出 JSON。\n\n"
                f"【章节】{section_title}\n\n"
                f"【内容】\n{body}"
            )},
        ]
        raw = client.chat(messages, temperature=0.1, max_tokens=600)

        # 去除模型可能输出的 markdown 代码块标记
        raw = re.sub(r"```(?:json)?\s*|```", "", raw).strip()

        # 提取最外层 JSON 对象
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return None
        meta = json.loads(json_match.group())

        if not meta.get("need_chart"):
            return None

        # 校验字段完整性
        if not {"chart_type", "title", "labels", "values"}.issubset(meta):
            return None
        if meta["chart_type"] not in CHART_TYPES:
            return None

        labels = meta["labels"]
        values = meta["values"]
        if not isinstance(labels, list) or not isinstance(values, list):
            return None
        if len(labels) != len(values) or len(values) < 2:
            return None

        meta["values"] = [float(v) for v in values]
        meta["labels"] = [str(l) for l in labels]
        meta.setdefault("x_label", "")
        meta.setdefault("y_label", "")
        return meta

    except Exception as exc:
        print(f"    [WARN] 图表判断失败（{section_title}）: {exc}")
        return None


# ════════════════════════════════════════════════════════════════════════════════
# ⑤ 图表渲染模块：matplotlib 灰蓝色调
# ════════════════════════════════════════════════════════════════════════════════

def _setup_fonts() -> None:
    """配置中文字体优先级，回退到 DejaVu Sans。"""
    plt.rcParams.update({
        "font.family": [
            "WenQuanYi Zen Hei",   # Linux
            "WenQuanYi Micro Hei", # Linux
            "SimHei",              # Windows
            "Microsoft YaHei",     # Windows
            "PingFang SC",         # macOS
            "DejaVu Sans",         # 回退
        ],
        "axes.unicode_minus": False,
        "figure.dpi":         150,
    })


def _base_style(ax: "plt.Axes", title: str, x_label: str = "", y_label: str = "") -> None:
    """统一应用灰蓝色调基础样式。"""
    ax.set_facecolor(_BG)
    ax.figure.set_facecolor(_BG)  # type: ignore[union-attr]
    ax.set_title(title, color=_TEXT, fontsize=13, fontweight="bold", pad=14)
    if x_label: ax.set_xlabel(x_label, color=_TEXT, fontsize=10)
    if y_label: ax.set_ylabel(y_label, color=_TEXT, fontsize=10)
    ax.tick_params(colors=_TEXT, labelsize=9)
    for sp in ax.spines.values():
        sp.set_edgecolor(_GRID)
    ax.grid(axis="y", color=_GRID, linewidth=0.8, linestyle="--", alpha=0.7)


def _bar_labels(ax: "plt.Axes", bars: Any, values: list[float]) -> None:
    """在柱顶标注数值。"""
    peak = max(abs(v) for v in values) or 1
    for bar, val in zip(bars, values):
        label = f"{val:,.0f}" if abs(val) >= 100 else f"{val:g}"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + peak * 0.012,
            label, ha="center", va="bottom",
            fontsize=8.5, color=_TEXT, fontweight="bold",
        )


def render_chart(meta: dict[str, Any], output_path: Path) -> bool:
    """
    根据图表元数据渲染 PNG 并保存。

    Args:
        meta:        judge_and_extract_chart() 返回的元数据字典。
        output_path: PNG 保存路径。

    Returns:
        True 表示成功，False 表示跳过（依赖缺失或数据异常）。
    """
    if not _MPL_OK:
        print("    [WARN] matplotlib 未安装，跳过图表。请运行: pip install matplotlib numpy")
        return False

    _setup_fonts()

    ctype   = meta["chart_type"]
    title   = meta["title"]
    xlabel  = meta.get("x_label", "")
    ylabel  = meta.get("y_label", "")
    labels  = meta["labels"]
    values  = meta["values"]
    colors  = (_PALETTE * ((len(values) // len(_PALETTE)) + 1))[:len(values)]

    fig, ax = plt.subplots(figsize=(9, 5))

    try:
        if ctype == "bar":
            bars = ax.bar(labels, values, color=colors, edgecolor="white",
                          linewidth=0.7, width=0.6)
            _bar_labels(ax, bars, values)
            _base_style(ax, title, xlabel, ylabel)
            if len(labels) > 5:
                ax.set_xticklabels(labels, rotation=20, ha="right")

        elif ctype == "horizontal_bar":
            y_pos = range(len(labels))
            hbars = ax.barh(list(y_pos), values, color=colors,
                            edgecolor="white", linewidth=0.7, height=0.55)
            ax.set_yticks(list(y_pos))
            ax.set_yticklabels(labels, fontsize=9)
            peak = max(abs(v) for v in values) or 1
            for bar, val in zip(hbars, values):
                ax.text(bar.get_width() + peak * 0.015,
                        bar.get_y() + bar.get_height() / 2,
                        f"{val:,.0f}" if abs(val) >= 100 else f"{val:g}",
                        va="center", fontsize=8.5, color=_TEXT, fontweight="bold")
            ax.set_facecolor(_BG); fig.set_facecolor(_BG)
            ax.set_title(title, color=_TEXT, fontsize=13, fontweight="bold", pad=14)
            if xlabel: ax.set_xlabel(xlabel, color=_TEXT, fontsize=10)
            ax.tick_params(colors=_TEXT, labelsize=9)
            for sp in ax.spines.values(): sp.set_edgecolor(_GRID)
            ax.grid(axis="x", color=_GRID, linewidth=0.8, linestyle="--", alpha=0.7)
            ax.set_xlim(0, max(values) * 1.22)

        elif ctype == "line":
            x_idx = range(len(labels))
            ax.fill_between(x_idx, values, alpha=0.12, color=_ACCENT)
            ax.plot(x_idx, values, color=_ACCENT, linewidth=2.3,
                    marker="o", markersize=7, markerfacecolor="white",
                    markeredgecolor=_ACCENT, markeredgewidth=2)
            peak = max(abs(v) for v in values) or 1
            for i, val in enumerate(values):
                ax.annotate(
                    f"{val:,.0f}" if abs(val) >= 100 else f"{val:g}",
                    xy=(i, val), xytext=(0, 10), textcoords="offset points",
                    ha="center", fontsize=8.5, color=_TEXT)
            ax.set_xticks(list(x_idx))
            ax.set_xticklabels(labels, rotation=20 if len(labels) > 6 else 0, ha="right")
            _base_style(ax, title, xlabel, ylabel)

        elif ctype == "pie":
            wedges, texts, autotexts = ax.pie(
                values, labels=labels, colors=colors, autopct="%1.1f%%",
                startangle=140,
                wedgeprops={"linewidth": 1.3, "edgecolor": "white"},
                pctdistance=0.82)
            for t  in texts:     t.set_color(_TEXT);    t.set_fontsize(9.5)
            for at in autotexts: at.set_color("white"); at.set_fontsize(8.5); at.set_fontweight("bold")
            ax.set_title(title, color=_TEXT, fontsize=13, fontweight="bold", pad=16)
            ax.set_facecolor(_BG); fig.set_facecolor(_BG)

        else:
            plt.close(fig)
            return False

    except Exception as exc:
        plt.close(fig)
        print(f"    [WARN] 图表渲染出错: {exc}")
        return False

    plt.tight_layout(pad=1.8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    return True


# ════════════════════════════════════════════════════════════════════════════════
# ⑥ GeneratorAgent 主类
# ════════════════════════════════════════════════════════════════════════════════

class GeneratorAgent:
    """
    通用商业分析报告生成智能体。

    将 Analyst-Agent 输出的分析文本经由以下流水线处理：
      原始分析文本
        → 章节解析（按 Markdown 标题切分）
        → DeepSeek 语义润色（逐节）
        → DeepSeek 图表判断（逐节）+ matplotlib 渲染
        → Markdown 报告组装 + 本地保存

    完全兼容原 GeneratorAgent 接口（generate_markdown_report / save_report）。
    """

    def __init__(
        self,
        config_path:     str | Path | None                         = None,
        api_key:         str                                       = DEEPSEEK_API_KEY,
        enable_polish:   bool                                      = True,
        enable_chart:    bool                                      = True,
        output_dir:      str | Path | None                        = None,
        report_title:    str | None                               = None,
        report_sections: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
    ) -> None:
        """
        Args:
            config_path:     项目 config.yaml 路径（框架内使用）。
            api_key:         DeepSeek API Key。
            enable_polish:   是否启用语义润色（默认 True）。
            enable_chart:    是否启用图表智能插入（默认 True）。
            output_dir:      报告与图表的输出根目录。
            report_title:    报告标题（覆盖 config 中的配置）。
            report_sections: 自定义章节结构（覆盖默认六章节）。
                             格式：((标题, (别名1, 别名2, ...)), ...)
        """
        # ── 配置加载 ──────────────────────────────────────────────────────────
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config      = self._load_config()
        gen_cfg          = self.config.get("generator", {})
        report_cfg       = gen_cfg.get("report", {})

        self.report_title    = report_title or report_cfg.get("title", DEFAULT_REPORT_TITLE)
        self.report_sections = report_sections or DEFAULT_REPORT_SECTIONS

        # 原接口兼容
        self.default_output = PROJECT_ROOT / report_cfg.get(
            "default_output",
            "data/processed/sales_and_market_report_2025.md",
        )

        # ── 输出目录 ──────────────────────────────────────────────────────────
        self.output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # ── DeepSeek 客户端（初始化失败时降级为纯文本模式）─────────────────
        self.enable_polish = enable_polish
        self.enable_chart  = enable_chart
        self._client: DeepSeekClient | None = None
        if enable_polish or enable_chart:
            try:
                self._client = DeepSeekClient(api_key=api_key)
            except ValueError as exc:
                print(f"  [WARN] DeepSeek 客户端初始化失败: {exc}")
                print("  [WARN] 将以纯文本模式运行（无润色、无图表）。")
                self.enable_polish = False
                self.enable_chart  = False

    # ── 配置加载 ─────────────────────────────────────────────────────────────

    def _load_config(self) -> dict[str, Any]:
        if not _CONFIG_OK:
            return {}
        try:
            if self.config_path.resolve() == DEFAULT_CONFIG_PATH.resolve():
                return load_app_config()           # type: ignore[name-defined]
            return load_app_config(config_path=self.config_path)  # type: ignore[name-defined]
        except Exception:
            return {}

    # ── 章节解析 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_heading(text: str) -> str:
        """标准化标题：去除序号、空白、大小写，便于模糊匹配。"""
        text = text.strip().lower()
        text = re.sub(r"^[#\s\d、.．\-—–]+", "", text)
        text = re.sub(r"\s+", "", text)
        return text

    def _match_section_key(self, heading: str) -> str | None:
        """将原文标题模糊匹配到预定义章节 key（子串双向包含）。"""
        norm = self._normalize_heading(heading)
        for section_title, aliases in self.report_sections:
            for alias in aliases:
                alias_norm = self._normalize_heading(alias)
                if alias_norm in norm or norm in alias_norm:
                    return section_title
        return None

    def _parse_analysis_sections(self, analysis_text: str) -> dict[str, str]:
        """
        按 Markdown 标题（# ~ ###）将分析文本切分为各章节正文。

        Returns:
            {章节 key → 正文字符串} 字典。
        """
        parsed: dict[str, str] = {}
        text = analysis_text.strip()
        if not text:
            return parsed
        try:
            pattern = re.compile(r"^#{1,3}\s*(.+?)\s*$", re.MULTILINE)
            matches = list(pattern.finditer(text))
            if not matches:
                return parsed
            for idx, match in enumerate(matches):
                heading = match.group(1).strip()
                start   = match.end()
                end     = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
                body    = text[start:end].strip()
                # 清除正文内可能残留的 prompt 标记
                body    = re.sub(r"【章节标题】[^\n]*\n?", "", body).strip()
                key     = self._match_section_key(heading)
                if key and body and key not in parsed:
                    parsed[key] = body
        except Exception:
            pass
        return parsed

    def _build_section_body(self, section_title: str, parsed: dict[str, str]) -> str:
        body = parsed.get(section_title, "").strip()
        return body if body else "（暂无数据，待 Analyst-Agent 分析回填。）"

    # ── 核心流水线 ───────────────────────────────────────────────────────────

    def generate_markdown_report(
        self,
        analysis_text:   str,
        report_filename: str | None = None,
    ) -> str:
        """
        将原始分析文本转换为完整 Markdown 报告。

        流程：解析 → 逐节润色（可选）→ 逐节图表判断+渲染（可选）→ 组装。

        Args:
            analysis_text:   Analyst-Agent 输出的原始分析文本。
            report_filename: 报告文件名（用于 Markdown 内图片相对路径引用）。

        Returns:
            完整 Markdown 报告字符串。
        """
        if not isinstance(analysis_text, str):
            raise TypeError("analysis_text 必须为字符串")
        content = analysis_text.strip()
        if not content:
            raise ValueError("analysis_text 不能为空")

        parsed       = self._parse_analysis_sections(content)
        tz_cn        = timezone(timedelta(hours=8))
        now          = datetime.now(tz_cn)
        generated_at = now.strftime("%Y-%m-%d %H:%M:%S")

        if not report_filename:
            report_filename = f"report_{now.strftime('%Y%m%d_%H%M%S')}.md"
        report_stem = Path(report_filename).stem

        print(f"\n[Generator] 共 {len(self.report_sections)} 个章节，"
              f"润色={'开' if self.enable_polish else '关'}，"
              f"图表={'开' if self.enable_chart else '关'}")

        # ── 表头 ──────────────────────────────────────────────────────────────
        header = (
            f"# {self.report_title}\n\n"
            f"| 项目 | 内容 |\n"
            f"| --- | --- |\n"
            f"| 报告类型 | 销售与市场综合分析 |\n"
            f"| 分析周期 | 2025 年度 |\n"
            f"| 生成时间 | {generated_at} |\n"
            f"| 生成引擎 | Generator-Agent v2（DeepSeek 润色 + 智能图表） |\n\n"
            f"---\n\n"
        )

        # ── 逐节处理 ──────────────────────────────────────────────────────────
        section_blocks: list[str] = []
        chart_idx = 1

        for section_title, _aliases in self.report_sections:
            print(f"\n  ── {section_title}")
            body = self._build_section_body(section_title, parsed)

            # Step 1：语义润色
            if self.enable_polish and self._client:
                print("    [润色] 调用 DeepSeek ...")
                body = polish_section(self._client, section_title, body)
                print(f"    [润色] 完成（{len(body)} 字）")

            # Step 2：图表判断与渲染
            chart_md = ""
            if self.enable_chart and self._client:
                print("    [图表] 判断是否需要图表 ...")
                meta = judge_and_extract_chart(self._client, section_title, body)
                if meta:
                    chart_filename = f"{report_stem}_chart{chart_idx:02d}.png"
                    chart_path     = self.output_dir / chart_filename
                    print(f"    [图表] 类型={meta['chart_type']}，标题={meta['title']}")
                    if render_chart(meta, chart_path):
                        chart_md = (
                            f"\n\n> **图 {chart_idx}：{meta['title']}**\n\n"
                            f"![{meta['title']}]({chart_filename})\n"
                        )
                        print(f"    [图表] 已保存 → {chart_filename}")
                        chart_idx += 1
                else:
                    print("    [图表] 无需插图")

            section_blocks.append(f"## {section_title}\n\n{body}{chart_md}\n")

        # ── 解析失败时附录保留原文 ────────────────────────────────────────────
        appendix = ""
        if not parsed:
            appendix = (
                "\n---\n\n"
                "## 附录：Analyst-Agent 原始分析全文\n\n"
                f"{content}\n"
            )

        # ── 页脚 ──────────────────────────────────────────────────────────────
        footer = (
            "\n---\n\n"
            "*本报告由 Generator-Agent v2 自动生成，内容来源于多源数据采集与 RAG 分析链路，"
            "经 DeepSeek 语义润色与智能图表增强，所有数据均源自原始分析文本，"
            "仅供课程大作业与商业研讨参考，不构成任何投资或商业决策建议。*\n"
        )

        return header + "\n".join(section_blocks) + appendix + footer

    # ── 文件保存（原接口兼容）────────────────────────────────────────────────

    def save_report(self, report_content: str, output_path: str | Path) -> Path:
        """
        将报告原子写入本地文件（先写 .tmp 再替换，防止写入中断损坏文件）。

        Returns:
            已保存文件的绝对路径。
        """
        if not isinstance(report_content, str):
            raise TypeError("report_content 必须为字符串")
        content = report_content.strip()
        if not content:
            raise ValueError("report_content 不能为空")

        path = Path(output_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            if tmp.exists():
                try:    tmp.unlink()
                except: pass
            raise

        return path.resolve()

    # ── 一键运行 ─────────────────────────────────────────────────────────────

    def run_from_file(
        self,
        input_txt:   str | Path,
        output_path: str | Path | None = None,
    ) -> Path:
        """
        从文本文件读取分析内容，完整执行流水线并保存报告。

        Args:
            input_txt:   输入文件路径（test.txt 或 Analyst-Agent 输出文件）。
            output_path: 报告保存路径；默认 output_dir/<stem>_report.md。

        Returns:
            已保存报告的绝对路径。
        """
        input_path = Path(input_txt)
        if not input_path.exists():
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        print(f"[Generator] 读取输入: {input_path}")
        analysis_text = input_path.read_text(encoding="utf-8")

        if not output_path:
            output_path = self.output_dir / f"{input_path.stem}_report.md"

        out             = Path(output_path)
        report_filename = out.name

        report_content = self.generate_markdown_report(
            analysis_text,
            report_filename=report_filename,
        )

        saved = self.save_report(report_content, out)
        print(f"\n[Generator] ✅ 报告已保存 → {saved}")
        return saved


# ════════════════════════════════════════════════════════════════════════════════
# ⑦ 命令行入口
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    _D_OUTDIR = (
        r"D:\better\openclaw-analysis-system-main"
        r"\openclaw-analysis-system-main\src\agents\generator\data"
    )

    parser = argparse.ArgumentParser(
        description="Generator-Agent v2：通用商业分析报告生成智能体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例
----
  # 必须指定输入文件，输出目录可选（默认使用硬编码路径）
  python generator.py -i analysis.txt

  # 自定义输入/输出目录
  python generator.py -i analysis.txt -d ./output/

  # 纯文本模式（跳过 API 调用，用于调试解析逻辑）
  python generator.py --no-polish --no-chart
        """,
    )
    parser.add_argument("--input",      "-i", required=True,
                        metavar="PATH", help="输入分析文本路径")
    parser.add_argument("--output",     "-o", default=None,
                        metavar="PATH", help="输出报告路径（默认：output-dir/<stem>_report.md）")
    parser.add_argument("--output-dir", "-d", default=_D_OUTDIR,
                        metavar="DIR",  help="报告与图表的输出目录")
    parser.add_argument("--api-key",    "-k", default=DEEPSEEK_API_KEY,
                        metavar="KEY",  help="DeepSeek API Key")
    parser.add_argument("--title",      "-t", default=None,
                        metavar="STR",  help="报告标题（覆盖默认值）")
    parser.add_argument("--no-polish", action="store_true", help="跳过语义润色")
    parser.add_argument("--no-chart",  action="store_true", help="跳过图表插入")

    args = parser.parse_args()

    agent = GeneratorAgent(
        api_key        = args.api_key,
        enable_polish  = not args.no_polish,
        enable_chart   = not args.no_chart,
        output_dir     = args.output_dir,
        report_title   = args.title,
    )
    saved = agent.run_from_file(args.input, args.output)
    print(f"\n完成！报告路径：{saved}")
