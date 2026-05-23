# """基于 Playwright 的动态竞品网页实时捕获 Skill。"""

# from __future__ import annotations

# import re
# from typing import Iterable
# from urllib.parse import urlparse

# from bs4 import BeautifulSoup
# from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
# from playwright.sync_api import sync_playwright

# DEFAULT_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript")
# DEFAULT_USER_AGENT = (
#     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
#     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
# )
# DEFAULT_MAX_CHARS = 8000
# DEFAULT_TIMEOUT_MS = 45_000
# DEFAULT_SCROLL_STEPS = 4
# DEFAULT_SCROLL_PAUSE_MS = 800


# def _validate_market_url(market_url: str) -> str:
#     url = (market_url or "").strip()
#     if not url:
#         raise ValueError("market_url 不能为空")

#     parsed = urlparse(url)
#     if parsed.scheme not in {"http", "https"}:
#         raise ValueError(f"market_url 须为 http/https 协议: {url}")
#     if not parsed.netloc:
#         raise ValueError(f"market_url 格式无效: {url}")

#     return url


# def _remove_tags(soup: BeautifulSoup, tag_names: Iterable[str]) -> None:
#     for tag_name in tag_names:
#         for tag in soup.find_all(tag_name):
#             tag.decompose()


# def _html_to_clean_text(raw_html: str, strip_tags: Iterable[str], max_chars: int) -> str:
#     if not raw_html.strip():
#         raise ValueError("页面 HTML 为空")

#     soup = BeautifulSoup(raw_html, "html.parser")
#     _remove_tags(soup, strip_tags)

#     text = soup.get_text(separator="\n", strip=True)
#     lines = [line.strip() for line in text.splitlines() if line.strip()]
#     compact = "\n".join(lines)
#     compact = re.sub(r"\n{3,}", "\n\n", compact)

#     if not compact:
#         raise ValueError("页面清洗后无有效文本")

#     if max_chars > 0 and len(compact) > max_chars:
#         compact = compact[:max_chars]

#     return compact


# class WebCollectorSkill:
#     """使用 Playwright 无头浏览器动态抓取竞品网页文本。"""

#     def __init__(
#         self,
#         *,
#         max_chars: int = DEFAULT_MAX_CHARS,
#         strip_tags: Iterable[str] | None = None,
#         user_agent: str = DEFAULT_USER_AGENT,
#         timeout_ms: int = DEFAULT_TIMEOUT_MS,
#         scroll_steps: int = DEFAULT_SCROLL_STEPS,
#         scroll_pause_ms: int = DEFAULT_SCROLL_PAUSE_MS,
#         headless: bool = True,
#     ) -> None:
#         self.max_chars = max_chars
#         self.strip_tags = tuple(strip_tags) if strip_tags is not None else DEFAULT_STRIP_TAGS
#         self.user_agent = user_agent
#         self.timeout_ms = timeout_ms
#         self.scroll_steps = max(1, scroll_steps)
#         self.scroll_pause_ms = scroll_pause_ms
#         self.headless = headless

#     def _scroll_page(self, page) -> None:
#         """模拟人类向下滚动，触发懒加载。"""
#         try:
#             page.evaluate("window.scrollTo(0, 0)")
#             page.wait_for_timeout(300)

#             for step in range(1, self.scroll_steps + 1):
#                 ratio = step / self.scroll_steps
#                 page.evaluate(
#                     "(ratio) => {"
#                     "  const height = Math.max("
#                     "    document.body.scrollHeight,"
#                     "    document.documentElement.scrollHeight"
#                     "  );"
#                     "  window.scrollTo(0, height * ratio);"
#                     "}",
#                     ratio,
#                 )
#                 page.wait_for_timeout(self.scroll_pause_ms)

#             page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
#             page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
#         except PlaywrightTimeoutError:
#             # 部分站点持续有 minutely activity，滚动后超时不阻断主流程
#             pass
#         except Exception as exc:
#             raise RuntimeError(f"页面滚动触发懒加载失败: {exc}") from exc

#     def collect(self, market_url: str) -> str:
#         """
#         动态捕获竞品网页并返回清洗后的纯文本。

#         Args:
#             market_url: 竞品页面 URL（http/https）。

#         Returns:
#             清洗、紧凑的竞品文本。

#         Raises:
#             ValueError: URL 无效或抓取结果为空。
#             RuntimeError: 浏览器启动或页面加载失败。
#         """
#         url = _validate_market_url(market_url)

#         try:
#             with sync_playwright() as playwright:
#                 browser = playwright.chromium.launch(headless=self.headless)
#                 context = browser.new_context(user_agent=self.user_agent, locale="zh-CN")
#                 page = context.new_page()

#                 try:
#                     # 先用 domcontentloaded 保证首屏可达，再滚动触发懒加载
#                     page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
#                     self._scroll_page(page)
#                     raw_html = page.content()
#                 except PlaywrightTimeoutError as exc:
#                     raise RuntimeError(
#                         f"页面加载超时（{self.timeout_ms}ms）: {url}\n"
#                         "可尝试增大 timeout_ms 或检查网络/目标站点可达性。"
#                     ) from exc
#                 finally:
#                     context.close()
#                     browser.close()

#             return _html_to_clean_text(raw_html, self.strip_tags, self.max_chars)

#         except ValueError:
#             raise
#         except RuntimeError:
#             raise
#         except Exception as exc:
#             raise RuntimeError(f"动态网页采集失败 [{url}]: {exc}") from exc


# def collect_competitor_web(
#     market_url: str,
#     *,
#     max_chars: int = DEFAULT_MAX_CHARS,
#     strip_tags: Iterable[str] | None = None,
# ) -> str:
#     """函数式入口：采集指定 URL 的竞品网页文本。"""
#     skill = WebCollectorSkill(max_chars=max_chars, strip_tags=strip_tags)
#     return skill.collect(market_url)

# 修改之后
"""基于 Playwright 的动态竞品网页实时捕获 Skill。"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_STRIP_TAGS = (
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "noscript",
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_MAX_CHARS = 8000
DEFAULT_TIMEOUT_MS = 45_000
DEFAULT_SCROLL_STEPS = 4
DEFAULT_SCROLL_PAUSE_MS = 800


# ==========================================================
# URL 校验
# ==========================================================

def _validate_market_url(market_url: str) -> str:

    url = (market_url or "").strip()

    if not url:
        raise ValueError("market_url 不能为空")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"market_url 须为 http/https 协议: {url}"
        )

    if not parsed.netloc:
        raise ValueError(
            f"market_url 格式无效: {url}"
        )

    return url


# ==========================================================
# HTML 标签清理
# ==========================================================

def _remove_tags(
    soup: BeautifulSoup,
    tag_names: Iterable[str],
) -> None:

    for tag_name in tag_names:

        for tag in soup.find_all(tag_name):

            tag.decompose()


# ==========================================================
# 商品 + 价格结构化提取
# ==========================================================

def extract_products_and_prices(text: str):

    results = []

    product_pattern = re.compile(
        r"(HUAWEI\s+WATCH[^\n]*)",
        re.IGNORECASE,
    )

    price_pattern = re.compile(
        r"(￥\s*\d+\s*起?)"
    )

    lines = text.splitlines()

    for i, line in enumerate(lines):

        line = line.strip()

        product_match = product_pattern.search(line)

        if not product_match:
            continue

        product_name = (
            product_match.group(1).strip()
        )

        price = "未找到价格"

        nearby_lines = lines[i:i + 5]

        for nearby_line in nearby_lines:

            nearby_line = nearby_line.strip()

            price_match = price_pattern.search(
                nearby_line
            )

            if price_match:

                price = (
                    price_match.group(1)
                )

                break

        results.append({
            "product_name": product_name,
            "price": price,
        })

    return results


# ==========================================================
# HTML -> 清洗文本
# ==========================================================

def _html_to_clean_text(
    raw_html: str,
    strip_tags: Iterable[str],
    max_chars: int,
) -> tuple[str, str]:

    if not raw_html.strip():
        raise ValueError("页面 HTML 为空")

    soup = BeautifulSoup(
        raw_html,
        "html.parser",
    )

    # 页面标题
    title = ""

    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    _remove_tags(
        soup,
        strip_tags,
    )

    text = soup.get_text(
        separator="\n",
        strip=True,
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    compact = "\n".join(lines)

    compact = re.sub(
        r"\n{3,}",
        "\n\n",
        compact,
    )

    if not compact:
        raise ValueError(
            "页面清洗后无有效文本"
        )

    if (
        max_chars > 0
        and len(compact) > max_chars
    ):
        compact = compact[:max_chars]

    return compact, title


# ==========================================================
# WebCollectorSkill
# ==========================================================

class WebCollectorSkill:
    """动态竞品网页采集 Skill"""

    def __init__(
        self,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        strip_tags: Iterable[str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        scroll_steps: int = DEFAULT_SCROLL_STEPS,
        scroll_pause_ms: int = DEFAULT_SCROLL_PAUSE_MS,
        headless: bool = True,
    ) -> None:

        self.max_chars = max_chars

        self.strip_tags = (
            tuple(strip_tags)
            if strip_tags is not None
            else DEFAULT_STRIP_TAGS
        )

        self.user_agent = user_agent
        self.timeout_ms = timeout_ms

        self.scroll_steps = max(
            1,
            scroll_steps,
        )

        self.scroll_pause_ms = (
            scroll_pause_ms
        )

        self.headless = headless

    # ======================================================
    # 模拟滚动
    # ======================================================

    def _scroll_page(self, page) -> None:

        try:

            page.evaluate(
                "window.scrollTo(0, 0)"
            )

            page.wait_for_timeout(300)

            for step in range(
                1,
                self.scroll_steps + 1,
            ):

                ratio = (
                    step / self.scroll_steps
                )

                page.evaluate(
                    "(ratio) => {"
                    " const height = Math.max("
                    " document.body.scrollHeight,"
                    " document.documentElement.scrollHeight"
                    " );"
                    " window.scrollTo(0, height * ratio);"
                    "}",
                    ratio,
                )

                page.wait_for_timeout(
                    self.scroll_pause_ms
                )

            page.evaluate(
                "window.scrollTo("
                "0, document.body.scrollHeight)"
            )

            page.wait_for_load_state(
                "networkidle",
                timeout=self.timeout_ms,
            )

        except PlaywrightTimeoutError:

            pass

        except Exception as exc:

            raise RuntimeError(
                f"页面滚动失败: {exc}"
            ) from exc

    # ======================================================
    # 主采集逻辑
    # ======================================================

    def collect(self, market_url: str):

        url = _validate_market_url(
            market_url
        )

        try:

            with sync_playwright() as playwright:

                browser = (
                    playwright.chromium.launch(
                        headless=self.headless
                    )
                )

                context = browser.new_context(
                    user_agent=self.user_agent,
                    locale="zh-CN",
                )

                page = context.new_page()

                try:

                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )

                    self._scroll_page(page)

                    raw_html = page.content()

                except PlaywrightTimeoutError as exc:

                    raise RuntimeError(
                        f"页面加载超时: {url}"
                    ) from exc

                finally:

                    context.close()
                    browser.close()

            # ==================================================
            # HTML 清洗
            # ==================================================

            text, title = _html_to_clean_text(
                raw_html,
                self.strip_tags,
                self.max_chars,
            )

            # ==================================================
            # 商品结构化提取
            # ==================================================

            products = (
                extract_products_and_prices(
                    text
                )
            )

            # ==================================================
            # 返回结构化结果
            # ==================================================

            return {
                "source_type": "web",
                "source_name": title,
                "url": url,
                "content": text,
                "structured_products": products,
                "metadata": {
                    "title": title,
                    "content_length": len(text),
                    "product_count": len(products),
                    "max_chars": self.max_chars,
                    "strip_tags": list(
                        self.strip_tags
                    ),
                },
            }

        except ValueError:
            raise

        except RuntimeError:
            raise

        except Exception as exc:

            raise RuntimeError(
                f"动态网页采集失败 [{url}]: {exc}"
            ) from exc


# ==========================================================
# 函数式入口
# ==========================================================

def collect_competitor_web(
    market_url: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    strip_tags: Iterable[str] | None = None,
):

    skill = WebCollectorSkill(
        max_chars=max_chars,
        strip_tags=strip_tags,
    )

    return skill.collect(market_url)

