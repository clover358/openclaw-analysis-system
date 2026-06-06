"""
Generator-Agent：通用商业分析报告生成智能体
=============================================

功能概述
--------
1. 【润色】  调用 DeepSeek 对各章节进行市场报告风格润色，不修改任何数据。
2. 【图表】  从原文中精确提取数值数据，仅生成条形图（bar），绝不捏造数据。
3. 【降级】  无法提取有效数据时，生成 Markdown 表格替代图表。
4. 【渲染】  用 matplotlib 以灰蓝色调渲染图表，保存 PNG 至输出目录。
5. 【组装】  将润色正文 + 图表/表格引用拼装为完整 Markdown 报告，写入本地文件。

设计原则
--------
- 数据真实性：所有图表数据必须可验证地来自原文，绝不捏造。
- 仅条形图：统一使用条形图展示对比数据，保持视觉一致性。
- 优雅降级：无法生成图表时自动降级为表格，确保信息完整呈现。
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
import math
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

PROJECT_ROOT        = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# 报告与图表默认输出目录（可被外部覆盖）
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

# ── 默认章节结构（可在初始化时替换为任意章节）────────────────────────────────
DEFAULT_REPORT_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("一、执行摘要",           ("执行摘要", "摘要", "overview", "executive summary")),
    ("二、主流 API 平台使用表现", ("主流 API 平台使用表现", "API 平台使用", "平台使用表现", "API 使用")),
    ("三、大模型 API 行业趋势研判", ("大模型 API 行业趋势研判", "行业趋势研判", "API 行业趋势", "市场趋势")),
    ("四、竞品 API 威胁与机会",   ("竞品 API 威胁与机会", "竞品威胁与机会", "竞品 API", "竞品分析")),
    ("五、战略建议",           ("战略建议", "策略建议", "行动建议", "建议")),
    ("六、风险提示",           ("风险提示", "风险", "风险因素")),
)

DEFAULT_REPORT_TITLE = ""  # 空值，由 DeepSeek 自动生成

# ── DeepSeek API ─────────────────────────────────────────────────────────────
DEEPSEEK_BASE_URL  = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL     = "deepseek-chat"
DEEPSEEK_TIMEOUT   = 120
DEEPSEEK_MAX_RETRY = 3

# ── 图表配色方案（灰蓝色调）─────────────────────────────────────────────────
_PALETTE = ["#2D5F8A", "#4A8DB7", "#7FB3D3", "#A8C8E0",
            "#6B8FA8", "#3D7A9E", "#9BB8CC", "#5A7D96"]
_BG      = "#F4F7FA"   # 背景色
_GRID    = "#D0DCE8"   # 网格线
_TEXT    = "#2C3E50"   # 文字
_ACCENT  = "#2D5F8A"   # 强调色（折线/面积）

# ── 支持的图表类型（仅条形图）─────────────────────────────────────────────────
CHART_TYPES = ("bar",)


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
        api_key:  str = "",
        base_url: str = DEEPSEEK_BASE_URL,
        timeout:  int = DEEPSEEK_TIMEOUT,
    ) -> None:
        if not api_key:
            raise ValueError(
                "请提供有效的 DeepSeek API Key。\n"
                "方式一：在项目根目录 .env 中设置 OPENAI_API_KEY。\n"
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
        max_tokens:  int   = 10000,
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
        base    = self.base_url.rstrip("/")
        url     = (
            f"{base}/chat/completions"
            if base.endswith("/v1")
            else f"{base}/v1/chat/completions"
        )
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


_TITLE_GENERATOR_SYSTEM = """\
你是一位资深市场研究分析师，擅长为商业分析报告生成专业、准确的标题。

【任务】根据用户提供的分析文本，生成一个简洁、专业的报告标题。

【要求】
  1. 标题必须能准确反映报告的核心内容和分析对象。
  2. 必须包含年份（从文本中提取或使用当前年份）。
  3. 长度不超过 30 个汉字。
  4. 格式：[年份][分析对象][报告类型]
  5. 示例："2025年大模型 API 竞品分析报告"、"2025年云计算市场趋势报告"

【输出要求】直接输出标题文本，不要添加任何说明、前缀、后缀或注释。\
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


def generate_report_title(
    client:         DeepSeekClient,
    analysis_text:  str,
) -> str:
    """
    根据分析文本自动生成报告标题。

    Args:
        client:        DeepSeek 客户端实例。
        analysis_text: Analyst-Agent 输出的分析文本。

    Returns:
        生成的报告标题字符串。如果生成失败，返回基于当前年份的默认标题。
    """
    if not analysis_text or not analysis_text.strip():
        return f"{datetime.now().year}年市场分析报告"

    try:
        messages = [
            {"role": "system", "content": _TITLE_GENERATOR_SYSTEM},
            {"role": "user",   "content": (
                f"请根据以下分析文本生成一个专业的报告标题。\n\n"
                f"【分析文本】\n{analysis_text[:2000]}..."  # 限制输入长度
            )},
        ]
        result = client.chat(messages, temperature=0.1, max_tokens=100)
        result = result.strip()
        
        # 如果生成的标题有效，返回它
        if result and len(result) <= 50:
            # 清理可能的引号
            result = result.strip('"').strip("'").strip()
            return result
        
        # 生成失败，返回默认标题
        return f"{datetime.now().year}年市场分析报告"
        
    except Exception as exc:
        print(f"    [WARN] 标题生成失败: {exc}，使用默认标题。")
        return f"{datetime.now().year}年市场分析报告"


# ════════════════════════════════════════════════════════════════════════════════
# ④ 图表判断模块：从原文提取数据，仅生成条形图，绝不捏造
# ════════════════════════════════════════════════════════════════════════════════

_CHART_JUDGE_SYSTEM = """\
你是一位严谨的数据提取专家，负责从市场分析报告中提取可验证的数值数据。

【核心原则】
  - 数据真实性：所有提取的数值必须能在原文中找到完全匹配的内容
  - 绝不捏造：如果原文中数据不足或无法明确提取，直接返回不需要图表
  - 仅条形图：统一使用条形图展示对比数据

【判断标准】
  需要图表：文本中明确包含 3 个及以上可量化比较的数值（如多维度收入、市场份额、性能指标等）。
  不需要图表：纯文字策略/建议/风险描述，或数值不足 3 个，或数据无法明确对应。

【若需要图表】严格输出以下 JSON，不要包含任何其他内容：
{
  "need_chart": true,
  "chart_type": "bar",
  "title": "简洁图表标题（不超过25字）",
  "x_label": "X轴标签",
  "y_label": "Y轴标签",
  "labels": ["标签1", "标签2", "标签3", ...],
  "values": [数值1, 数值2, 数值3, ...],
  "source_texts": ["原文中对应数据的片段1", "原文中对应数据的片段2", "原文中对应数据的片段3", ...]
}

【若不需要图表】输出：
{"need_chart": false}

【数据约束】
  - values 全部为纯数字（int 或 float），不带单位、货币符号或百分号。
  - values 数值必须与原文完全一致，严禁捏造、估算或推算。
  - labels 与 values 长度必须相同，且至少包含 3 个元素。
  - source_texts 必须包含每个数据点在原文中的来源片段，用于验证数据真实性。
  - chart_type 必须为 "bar"。
  - 当数据来源不明确或无法在原文中验证时，直接返回 {"need_chart": false}。\
"""


def _validate_data_from_source(body: str, labels: list[str], values: list[float], source_texts: list[str]) -> bool:
    """
    验证提取的数据是否真实来源于原文，防止幻觉。
    
    Args:
        body: 原文内容
        labels: 标签列表
        values: 数值列表
        source_texts: 声称的数据来源片段
        
    Returns:
        True 表示数据验证通过，False 表示数据可能存在幻觉
    """
    if len(labels) != len(values) or len(labels) != len(source_texts):
        return False
    
    # 预处理原文，移除空白和标点
    clean_body = re.sub(r'[\s，。,.""\']', '', body)
    
    # 检查每个来源片段是否确实存在于原文中
    for src_text in source_texts:
        if not isinstance(src_text, str) or src_text.strip() == "":
            return False
        # 检查来源文本是否在原文中存在（容错处理）
        clean_src = re.sub(r'[\s，。,.""\']', '', src_text).strip()
        if clean_src and clean_src not in clean_body:
            # 尝试更宽松的匹配：检查标签是否在原文中
            matched = False
            for label in labels:
                clean_label = re.sub(r'[\s，。,.""\']', '', label)
                if clean_label and clean_label in clean_body:
                    matched = True
                    break
            if not matched:
                return False
    
    # 检查数值是否能在原文中找到对应
    for val in values:
        str_val = str(val)
        # 检查数值是否在原文中出现
        if str_val in body:
            continue
        
        # 尝试不同格式
        formats_to_try = [
            f"{val:,.0f}",    # 带千分位
            f"{val:.1f}",     # 一位小数
            f"{val:.2f}",     # 两位小数
            f"{val:.3f}",     # 三位小数
            f"{val:.4f}",     # 四位小数
            f"{val:.5f}",     # 五位小数
            str(int(val)),    # 整数形式
            f"{val:.1e}",     # 科学计数法
            f"{val:.2e}",     # 科学计数法
            f"{val:.3e}",     # 科学计数法
            # Unicode 科学计数法格式
            f"{val:.2f}×10¹¹".replace('×10¹¹', 'e+11'),  # 处理科学计数法
        ]
        
        # 处理科学计数法的不同表示形式
        if val >= 1e10:
            # 尝试不同的指数表示
            exponent = int(math.log10(val))
            mantissa = val / (10 ** exponent)
            formats_to_try.append(f"{mantissa:.5f}×10^{exponent}")
            formats_to_try.append(f"{mantissa:.5f}e+{exponent}")
            formats_to_try.append(f"{mantissa:.5f}×10{chr(0x2070 + exponent)}")  # Unicode上标
        
        found = False
        for fmt_val in formats_to_try:
            if fmt_val.lower() in body.lower() or fmt_val in body:
                found = True
                break
        
        # 额外检查：在原文中查找接近的数值（允许微小误差）
        if not found:
            # 从原文中提取所有数字进行比对（包含 Unicode 上标）
            # 上标数字：⁰(U+2070), ¹(U+00B9), ²(U+00B2), ³(U+00B3), ⁴-⁹(U+2074-U+2079)
            numbers_in_body = re.findall(r'[\d.××10⁰¹²³⁴⁵⁶⁷⁸⁹]+[eE]?[+-]?[\d⁰¹²³⁴⁵⁶⁷⁸⁹]*', body)
            for num_str in numbers_in_body:
                try:
                    # 尝试解析各种格式
                    clean_num = num_str.replace('×', '*').replace('x', '*')
                    # 转换上标数字
                    sup_map = {'⁰':'0','¹':'1','²':'2','³':'3','⁴':'4','⁵':'5','⁶':'6','⁷':'7','⁸':'8','⁹':'9'}
                    for sup, digit in sup_map.items():
                        clean_num = clean_num.replace(sup, digit)
                    # 处理 10^xx 格式
                    clean_num = re.sub(r'10\^?(\d+)', r'10**\1', clean_num)
                    
                    body_val = eval(clean_num)  # 小心使用 eval，但这里只处理数字
                    if abs(body_val - val) / max(abs(body_val), abs(val), 1) < 0.001:
                        found = True
                        break
                except:
                    continue
        
        if not found:
            return False
    
    return True


def judge_and_extract_chart(
    client:        DeepSeekClient,
    section_title: str,
    body:          str,
) -> dict[str, Any] | None:
    """
    从原文中提取可验证的数据，仅生成条形图。
    
    数据验证流程：
    1. 调用 DeepSeek 提取数据
    2. 验证来源文本是否存在于原文中
    3. 验证数值是否能在原文中找到
    
    Returns:
        图表元数据字典，或 None（无需图表 / 提取失败 / 数据无法验证）。
    """
    if not body or body.startswith("（暂无数据"):
        return None
    
    try:
        messages = [
            {"role": "system", "content": _CHART_JUDGE_SYSTEM},
            {"role": "user",   "content": (
                f"请从以下章节中提取可验证的数据，并按要求输出 JSON。\n\n"
                f"【章节】{section_title}\n\n"
                f"【内容】\n{body}"
            )},
        ]
        raw = client.chat(messages, temperature=0.0, max_tokens=800)

        # 去除模型可能输出的 markdown 代码块标记
        raw = re.sub(r"```(?:json)?\s*|```", "", raw).strip()

        # 提取最外层 JSON 对象
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            print(f"    [WARN] 图表判断失败（{section_title}）: 无法提取 JSON")
            return None
        
        meta = json.loads(json_match.group())

        if not meta.get("need_chart"):
            return None

        # 校验字段完整性
        required_fields = {"chart_type", "title", "labels", "values", "source_texts"}
        if not required_fields.issubset(meta):
            print(f"    [WARN] 图表判断失败（{section_title}）: 字段不完整")
            return None
        
        if meta["chart_type"] != "bar":
            print(f"    [WARN] 图表判断失败（{section_title}）: 只支持条形图")
            return None

        labels = meta["labels"]
        values = meta["values"]
        source_texts = meta.get("source_texts", [])
        
        if not isinstance(labels, list) or not isinstance(values, list) or not isinstance(source_texts, list):
            print(f"    [WARN] 图表判断失败（{section_title}）: 数据类型错误")
            return None
        
        if len(labels) != len(values) or len(values) < 3:
            print(f"    [WARN] 图表判断失败（{section_title}）: 数据数量不足")
            return None
        
        # 严格验证数据真实性
        if not _validate_data_from_source(body, labels, values, source_texts):
            print(f"    [WARN] 图表判断失败（{section_title}）: 数据验证失败，可能存在幻觉")
            return None

        meta["values"] = [float(v) for v in values]
        meta["labels"] = [str(l) for l in labels]
        meta.setdefault("x_label", "")
        meta.setdefault("y_label", "")
        return meta

    except Exception as exc:
        print(f"    [WARN] 图表判断失败（{section_title}）: {exc}")
        return None


def generate_markdown_table(labels: list[str], values: list[float], title: str) -> str:
    """
    当无法生成图表时，生成 Markdown 表格作为替代。
    
    Args:
        labels: 标签列表
        values: 数值列表
        title: 表格标题
        
    Returns:
        Markdown 表格字符串
    """
    if len(labels) != len(values):
        return ""
    
    table_lines = [f"> **表：{title}**\n",]
    table_lines.append("\n")
    table_lines.append("| 项目 | 数值 |\n")
    table_lines.append("| --- | --- |\n")
    
    for label, val in zip(labels, values):
        # 格式化数值显示
        if abs(val) >= 1000000:
            val_str = f"{val/1000000:.1f}M"
        elif abs(val) >= 1000:
            val_str = f"{val/1000:.1f}K"
        elif abs(val) >= 100:
            val_str = f"{val:,.0f}"
        else:
            val_str = f"{val:g}"
        table_lines.append(f"| {label} | {val_str} |\n")
    
    return "".join(table_lines)


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
    根据图表元数据渲染条形图 PNG 并保存。

    Args:
        meta:        judge_and_extract_chart() 返回的元数据字典。
        output_path: PNG 保存路径。

    Returns:
        True 表示成功，False 表示跳过（依赖缺失或数据异常）。
    """
    if not _MPL_OK:
        print("    [WARN] matplotlib 未安装，无法生成图表")
        return False

    # 确保只处理条形图
    if meta.get("chart_type") != "bar":
        print("    [WARN] 只支持条形图类型")
        return False

    _setup_fonts()

    title   = meta["title"]
    xlabel  = meta.get("x_label", "")
    ylabel  = meta.get("y_label", "")
    labels  = meta["labels"]
    values  = meta["values"]
    colors  = (_PALETTE * ((len(values) // len(_PALETTE)) + 1))[:len(values)]

    fig, ax = plt.subplots(figsize=(9, 5))

    try:
        bars = ax.bar(labels, values, color=colors, edgecolor="white",
                      linewidth=0.7, width=0.6)
        _bar_labels(ax, bars, values)
        _base_style(ax, title, xlabel, ylabel)
        if len(labels) > 5:
            ax.set_xticklabels(labels, rotation=20, ha="right")

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
        api_key:         str | None                               = None,
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

        paths_cfg = self.config.get("paths", {}) or {}
        openai_cfg = self.config.get("openai", {}) or {}

        # ── 输出目录 ──────────────────────────────────────────────────────────
        default_out = PROJECT_ROOT / paths_cfg.get("output_dir", "data/processed")
        self.output_dir = Path(output_dir) if output_dir else default_out
        self.output_dir.mkdir(parents=True, exist_ok=True)

        resolved_key = (api_key or openai_cfg.get("api_key") or "").strip()
        resolved_base = (openai_cfg.get("base_url") or DEEPSEEK_BASE_URL).strip()

        # ── DeepSeek 客户端（初始化失败时降级为纯文本模式）─────────────────
        self.enable_polish = enable_polish
        self.enable_chart  = enable_chart
        self._client: DeepSeekClient | None = None
        if enable_polish or enable_chart:
            try:
                self._client = DeepSeekClient(
                    api_key=resolved_key,
                    base_url=resolved_base,
                )
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

        流程：解析 → 生成标题（可选）→ 逐节润色（可选）→ 逐节图表判断+渲染（可选）→ 组装。

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

        # ── 自动生成报告标题（如果未指定）────────────────────────────────────────
        current_title = self.report_title
        if not current_title and self.enable_polish and self._client:
            print("[Generator] [标题] 调用 DeepSeek 自动生成报告标题 ...")
            current_title = generate_report_title(self._client, content)
            print(f"[Generator] [标题] 生成完成: {current_title}")
        elif not current_title:
            current_title = f"{now.year}年市场分析报告"
            print(f"[Generator] [标题] 使用默认标题: {current_title}")

        print(f"\n[Generator] 共 {len(self.report_sections)} 个章节，"
              f"润色={'开' if self.enable_polish else '关'}，"
              f"图表={'开' if self.enable_chart else '关'}")

        # ── 表头 ──────────────────────────────────────────────────────────────
        header = (
            f"# {current_title}\n\n"
            f"| 项目 | 内容 |\n"
            f"| --- | --- |\n"
            f"| 报告类型 | 销售与市场综合分析 |\n"
            f"| 分析周期 | {now.year} 年度 |\n"
            f"| 生成时间 | {generated_at} |\n"
            f"| 生成引擎 | Generator-Agent v2（DeepSeek 润色 + 智能图表 + 自动标题） |\n\n"
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

            # Step 2：图表判断与渲染（仅条形图，数据验证失败则降级为表格）
            chart_md = ""
            if self.enable_chart and self._client:
                print("    [图表] 从原文提取数据并验证 ...")
                meta = judge_and_extract_chart(self._client, section_title, body)
                if meta:
                    chart_filename = f"{report_stem}_chart{chart_idx:02d}.png"
                    chart_path     = self.output_dir / chart_filename
                    print(f"    [图表] 标题={meta['title']}，数据点={len(meta['values'])}个")
                    if render_chart(meta, chart_path):
                        chart_md = (
                            f"\n\n> **图 {chart_idx}：{meta['title']}**\n\n"
                            f"![{meta['title']}]({chart_filename})\n"
                        )
                        print(f"    [图表] 已保存 → {chart_filename}")
                        chart_idx += 1
                    else:
                        # 图表渲染失败，尝试生成表格
                        print("    [表格] 图表渲染失败，生成表格替代")
                        chart_md = generate_markdown_table(
                            meta["labels"], meta["values"], meta["title"]
                        )
                else:
                    print("    [图表] 无法提取可验证数据，跳过图表")

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
            "经 DeepSeek 语义润色与智能图表增强。所有图表数据均经过严格验证，确保来源于原始分析文本，"
            "绝不捏造。图表渲染失败时自动降级为表格展示。仅供课程大作业与商业研讨参考，"
            "不构成任何投资或商业决策建议。*\n"
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

    def save(self, content: str, output_path: str | Path) -> Path:
        """Pipeline 落盘接口（委托 save_report）。"""
        return self.save_report(content, output_path)

    def run(self, *args: Any, **kwargs: Any) -> str:
        """
        Pipeline 调用入口：将 Analyst 分析正文转为完整 Markdown 报告。

        Args:
            analysis_text: Analyst-Agent 产出的分析正文。

        Returns:
            完整 Markdown 报告字符串。
        """
        analysis_text = kwargs.get("analysis_text")
        if analysis_text is None and args:
            analysis_text = args[0]
        if not isinstance(analysis_text, str):
            raise TypeError("analysis_text 必须为字符串")

        report_filename = Path(self.default_output).name
        return self.generate_markdown_report(
            analysis_text,
            report_filename=report_filename,
        )

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
    parser.add_argument("--api-key",    "-k", default=None,
                        metavar="KEY",  help="DeepSeek API Key（默认读取 .env）")
    parser.add_argument("--title",      "-t", default=None,
                        metavar="STR",  help="报告标题（覆盖默认值）")
    parser.add_argument("--no-polish", action="store_true", help="跳过语义润色")
    parser.add_argument("--no-chart",  action="store_true", help="跳过图表插入")

    args = parser.parse_args()

    agent = GeneratorAgent(
        api_key        = args.api_key or None,
        enable_polish  = not args.no_polish,
        enable_chart   = not args.no_chart,
        output_dir     = args.output_dir,
        report_title   = args.title,
    )
    saved = agent.run_from_file(args.input, args.output)
    print(f"\n完成！报告路径：{saved}")
