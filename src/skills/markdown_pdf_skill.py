"""Markdown → PDF 转换 Skill（Windows 友好，纯 Python）。

选用 ``markdown`` + ``xhtml2pdf``，无需安装 wkhtmltopdf / GTK（WeasyPrint 在 Windows 上依赖较重）。

用法::

    from src.skills.markdown_pdf_skill import markdown_to_pdf

    pdf_path = markdown_to_pdf(
        "data/processed/report.md",
        "data/outputs/report.pdf",
    )
"""

from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path

import markdown
from xhtml2pdf import pisa

# 报告 PDF 样式（xhtml2pdf 兼容的子集 CSS）
_REPORT_CSS = """
@page {
    size: A4;
    margin: 2cm;
}
body {
    font-family: MicrosoftYaHei, SimSun, sans-serif;
    font-size: 11pt;
    line-height: 1.65;
    color: #2c3e50;
}
h1 {
    color: #1a5276;
    font-size: 20pt;
    border-bottom: 2px solid #2d5f8a;
    padding-bottom: 8px;
    margin-top: 0;
}
h2 {
    color: #2d5f8a;
    font-size: 15pt;
    margin-top: 20px;
}
h3 {
    color: #4a8db7;
    font-size: 12pt;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 10pt;
}
th, td {
    border: 1px solid #d0dce8;
    padding: 6px 8px;
    text-align: left;
}
th {
    background-color: #f4f7fa;
}
blockquote {
    border-left: 4px solid #2d5f8a;
    margin: 12px 0;
    padding: 4px 12px;
    color: #566573;
}
code {
    background-color: #f4f7fa;
    padding: 2px 4px;
    font-size: 9pt;
}
pre {
    background-color: #f4f7fa;
    padding: 10px;
    font-size: 9pt;
    white-space: pre-wrap;
}
hr {
    border: none;
    border-top: 1px solid #d0dce8;
    margin: 16px 0;
}
ul, ol {
    margin: 8px 0;
    padding-left: 24px;
}
.meta {
    color: #7f8c8d;
    font-size: 9pt;
    margin-bottom: 16px;
}
"""

_MARKDOWN_EXTENSIONS = ["tables", "fenced_code", "nl2br", "sane_lists"]
_fonts_registered = False


class MarkdownPdfError(Exception):
    """Markdown 转 PDF 失败。"""


def _register_chinese_fonts() -> None:
    """注册 Windows 常见中文字体，避免 PDF 中文显示为方框。"""
    global _fonts_registered
    if _fonts_registered:
        return

    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return

    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [
        ("MicrosoftYaHei", fonts_dir / "msyh.ttc"),
        ("MicrosoftYaHei", fonts_dir / "msyhbd.ttc"),
        ("SimSun", fonts_dir / "simsun.ttc"),
        ("SimHei", fonts_dir / "simhei.ttf"),
    ]
    registered: set[str] = set()
    for font_name, font_path in candidates:
        if font_name in registered or not font_path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
            registered.add(font_name)
        except Exception:
            continue

    _fonts_registered = bool(registered)


def _resolve_output_path(pdf_file_path: str | Path) -> Path:
    path = Path(pdf_file_path)
    if not path.is_absolute():
        project_root = Path(__file__).resolve().parent.parent.parent
        path = project_root / path
    return path


def _rewrite_image_paths(html: str, md_dir: Path) -> str:
    """将 HTML 中相对路径图片改写为绝对 file:// 路径，便于 xhtml2pdf 嵌入。"""
    def _replace(match: re.Match[str]) -> str:
        before, src, after = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:", "file:")):
            return match.group(0)
        img_path = (md_dir / src).resolve()
        if img_path.is_file():
            return f'{before}{img_path.as_uri()}{after}'
        return match.group(0)

    return re.sub(r'(<img[^>]+src=")([^"]+)(")', _replace, html, flags=re.IGNORECASE)


def _build_html_document(markdown_text: str, *, title: str = "分析报告", md_dir: Path | None = None) -> str:
    body_html = markdown.markdown(
        markdown_text,
        extensions=_MARKDOWN_EXTENSIONS,
        output_format="html5",
    )
    if md_dir is not None:
        body_html = _rewrite_image_paths(body_html, md_dir)
    safe_title = re.sub(r"<[^>]+>", "", title)[:120]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8"/>
    <title>{safe_title}</title>
    <style>{_REPORT_CSS}</style>
</head>
<body>
    <p class="meta">OpenClaw 自动化分析报告 · PDF 导出</p>
    {body_html}
</body>
</html>"""


def _extract_title_from_markdown(markdown_text: str, fallback: str) -> str:
    for line in markdown_text.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return fallback


def markdown_to_pdf(md_file_path: str | Path, pdf_file_path: str | Path) -> Path:
    """
    将 Markdown 文件渲染为带样式的 PDF。

    Args:
        md_file_path: 源 Markdown 文件路径。
        pdf_file_path: 目标 PDF 输出路径（目录不存在时自动创建）。

    Returns:
        已写入的 PDF 绝对路径。

    Raises:
        MarkdownPdfError: 源文件无效或 PDF 生成失败。
    """
    md_path = Path(md_file_path)
    if not md_path.is_absolute():
        project_root = Path(__file__).resolve().parent.parent.parent
        md_path = project_root / md_path

    if not md_path.is_file():
        raise MarkdownPdfError(f"Markdown 文件不存在: {md_path}")

    content = md_path.read_text(encoding="utf-8").strip()
    if not content:
        raise MarkdownPdfError(f"Markdown 文件为空: {md_path}")

    pdf_path = _resolve_output_path(pdf_file_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    _register_chinese_fonts()
    title = _extract_title_from_markdown(content, fallback=md_path.stem)
    html_doc = _build_html_document(content, title=title, md_dir=md_path.parent)

    buffer = BytesIO()
    try:
        status = pisa.CreatePDF(
            html_doc.encode("utf-8"),
            dest=buffer,
            encoding="utf-8",
        )
    except Exception as exc:
        raise MarkdownPdfError(f"PDF 引擎异常: {exc}") from exc

    if status.err:
        raise MarkdownPdfError(f"PDF 生成失败（xhtml2pdf 返回错误）: {md_path}")

    pdf_bytes = buffer.getvalue()
    if not pdf_bytes:
        raise MarkdownPdfError(f"PDF 输出为空: {pdf_path}")

    pdf_path.write_bytes(pdf_bytes)
    return pdf_path.resolve()


def md_to_pdf_path(
    md_path: str | Path,
    *,
    output_dir: str | Path = "data/outputs",
) -> Path:
    """根据 Markdown 路径推导默认 PDF 输出路径（同名 .pdf）。"""
    md = Path(md_path)
    stem = md.stem
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent.parent.parent / out_dir
    return out_dir / f"{stem}.pdf"
