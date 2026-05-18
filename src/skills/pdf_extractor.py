"""PDF 文本提取 Skill。"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from pypdf import PdfReader


def extract_pdf_text(file_path: Union[str, Path]) -> str:
    """
    逐页提取 PDF 文本，并在每页前标注页码。

    Args:
        file_path: PDF 文件路径。

    Returns:
        带页码标注的全文文本。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: PDF 无页或无法读取。
        RuntimeError: 其他提取错误。
    """
    path = Path(file_path)

    try:
        if not path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {path}")

        if not path.is_file():
            raise ValueError(f"路径不是有效文件: {path}")

        reader = PdfReader(str(path))

        if len(reader.pages) == 0:
            raise ValueError(f"PDF 无页面内容: {path}")

        parts: list[str] = []

        for page_num, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
                text = text.strip()
                marker = f"--- 第 {page_num} 页 ---"
                parts.append(f"{marker}\n{text}" if text else f"{marker}\n[本页无文本]")
            except Exception as exc:
                parts.append(f"--- 第 {page_num} 页 ---\n[提取失败: {exc}]")

        result = "\n\n".join(parts).strip()

        if not result:
            raise ValueError(f"PDF 提取结果为空: {path}")

        return result

    except FileNotFoundError:
        raise
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"提取 PDF 失败 [{path}]: {exc}") from exc
