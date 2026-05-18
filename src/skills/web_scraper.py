"""本地 HTML 竞品页面解析 Skill。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Union

from bs4 import BeautifulSoup

DEFAULT_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript")
MAX_CHARS = 4000


def _remove_tags(soup: BeautifulSoup, tag_names: Iterable[str]) -> None:
    for tag_name in tag_names:
        for tag in soup.find_all(tag_name):
            tag.decompose()


def scrape_competitor_html(
    file_path: Union[str, Path],
    max_chars: int = MAX_CHARS,
    strip_tags: Iterable[str] | None = None,
) -> str:
    """
    解析本地 HTML 快照，剔除无用标签后返回纯文本（默认最多 4000 字）。

    Args:
        file_path: 本地 HTML 文件路径。
        max_chars: 返回文本最大字符数。
        strip_tags: 需要剔除的标签名列表。

    Returns:
        清洗后的页面文本。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 内容为空或无法解析。
        RuntimeError: 其他解析错误。
    """
    path = Path(file_path)
    tags_to_strip = tuple(strip_tags) if strip_tags is not None else DEFAULT_STRIP_TAGS

    try:
        if not path.exists():
            raise FileNotFoundError(f"HTML 文件不存在: {path}")

        if not path.is_file():
            raise ValueError(f"路径不是有效文件: {path}")

        raw_html = path.read_text(encoding="utf-8", errors="replace")

        if not raw_html.strip():
            raise ValueError(f"HTML 文件为空: {path}")

        soup = BeautifulSoup(raw_html, "html.parser")
        _remove_tags(soup, tags_to_strip)

        text = soup.get_text(separator="\n", strip=True)
        lines = [line for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        if not text:
            raise ValueError(f"HTML 解析后无有效文本: {path}")

        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]

        return text

    except FileNotFoundError:
        raise
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"解析 HTML 失败 [{path}]: {exc}") from exc
