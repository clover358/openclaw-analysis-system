"""Collector-Agent：多源异构数据采集与结构化整理。

- 数据源 1：通过 Playwright + CDP 抓取 OpenRouter Rankings（day / week / month
  三个视图下的多张榜单与图表数据），合并为 merged_rankings.json。
- 数据源 2：Stanford HAI AI Index Report 2026 Chapter 4（行业报告 PDF），
  联网下载并提取文本。

核心抓取逻辑保留自原 `src/collector.py`，仅在路径解析与对外接口上做必要适配，
以便 Pipeline 串联（CollectorAgent.run -> raw_texts, summary）。
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from src.agents.base import BaseAgent

# ────────────────────────────────────────────────────────────────────────────
# 配置区
# ────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SAVE_DIR = str((PROJECT_ROOT / "data").resolve())
os.makedirs(SAVE_DIR, exist_ok=True)
MERGED_FILE_PATH = os.path.join(SAVE_DIR, "merged_rankings.json")

INDUSTRY_REPORT_URL = (
    "https://hai.stanford.edu/assets/files/ai_index_report_2026_chapter_4_economy.pdf"
)
INDUSTRY_REPORT_FILENAME = "ai_index_report_2026_chapter_4_economy.pdf"

# ────────────────────────────────────────────────────────────────────────────
# 全局状态（保留原 collector.py 设计）
# ────────────────────────────────────────────────────────────────────────────
data_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
collected_fingerprints: set[str] = set()
agent_running = True
sequential_chart_count = 0


# ────────────────────────────────────────────────────────────────────────────
# 抓取核心逻辑（来自原 src/collector.py，未修改业务逻辑）
# ────────────────────────────────────────────────────────────────────────────
def get_data_fingerprint(data):
    try:
        if isinstance(data, dict):
            for key, val in data.items():
                if key in ["a", "f", "q", "i", "cachedAt"]:
                    continue
                if isinstance(val, list) and len(val) > 0:
                    first_item_str = json.dumps(val[0], sort_keys=True)
                    return hashlib.md5(first_item_str.encode()).hexdigest()
                elif isinstance(val, dict) and len(val) > 0:
                    first_k = next(iter(val))
                    first_item_str = json.dumps({first_k: val[first_k]}, sort_keys=True)
                    return hashlib.md5(first_item_str.encode()).hexdigest()
        elif isinstance(data, list) and len(data) > 0:
            first_item_str = json.dumps(data[0], sort_keys=True)
            return hashlib.md5(first_item_str.encode()).hexdigest()
    except Exception:
        pass
    return hashlib.md5(str(data)[:500].encode()).hexdigest()


def classify_and_name_data(data, current_view):
    global sequential_chart_count

    if isinstance(data, dict) and "day" in data and "week" in data and "month" in data:
        return "Top Apps.json"

    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
        if "x" in data["data"][0] and "ys" in data["data"][0]:
            return "Top Model.json"

    if isinstance(data, list) and len(data) > 0 and "model_permaslug" in data[0]:
        return f"{current_view}_leaderboard.json"

    if isinstance(data, list) and len(data) > 0 and "x" in data[0] and "ys" in data[0]:
        ys_keys = list(data[0]["ys"].keys())

        if "openai" in ys_keys or "google" in ys_keys or "anthropic" in ys_keys:
            return "Market Share.json"
        else:
            sequential_names = ["Categories.json", "Languages.json", "Programming.json", "Context Length.json"]
            if sequential_chart_count < 4:
                file_name = sequential_names[sequential_chart_count]
                sequential_chart_count += 1
                return file_name
            else:
                return f"Extra_Chart_{sequential_chart_count}.json"

    return None


def parse_rsc_text(text_data):
    lines = text_data.strip().split("\n")
    for line in lines:
        colon_idx = line.find(":")
        if colon_idx != -1:
            json_str = line[colon_idx + 1 :]
            try:
                data = json.loads(json_str)
                if len(str(data)) > 100:
                    return data
            except json.JSONDecodeError:
                continue
    return None


def data_processor_thread():
    global agent_running

    print("👷 数据处理大脑已启动，等待特征匹配...")
    while agent_running:
        try:
            current_view, raw_text = data_queue.get(timeout=1)
            json_data = parse_rsc_text(raw_text)

            if json_data:
                file_name = classify_and_name_data(json_data, current_view)

                if file_name and file_name.endswith("_leaderboard.json"):
                    file_path = os.path.join(SAVE_DIR, file_name)
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(json_data, f, ensure_ascii=False, indent=2)
                    print(f"🎯 [命中目标] 成功捕捉并保存: {file_name}")
                    data_queue.task_done()
                    continue

                fingerprint = get_data_fingerprint(json_data)

                if fingerprint not in collected_fingerprints:
                    if file_name:
                        collected_fingerprints.add(fingerprint)
                        file_path = os.path.join(SAVE_DIR, file_name)

                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump(json_data, f, ensure_ascii=False, indent=2)

                        print(f"🎯 [命中目标] 成功捕捉并保存: {file_name}")

            data_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"❌ 数据处理报错: {e}")


def merge_json_files():
    print("\n📦 开始合并采集到的榜单数据...")
    merged_data: dict[str, Any] = {}

    for file_name in os.listdir(SAVE_DIR):
        if file_name.endswith(".json") and file_name != "merged_rankings.json":
            file_path = os.path.join(SAVE_DIR, file_name)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    key_name = file_name.replace(".json", "")
                    merged_data[key_name] = json.load(f)
            except Exception as e:
                print(f"⚠️ 读取 {file_name} 失败: {e}")

    with open(MERGED_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=4)

    print(
        f"🎉 终极数据合并完成！总共合并了 {len(merged_data)} 个维度。"
        f"文件已保存至:\n   {MERGED_FILE_PATH}"
    )


def run_agent():
    """通过 Playwright 连接已开启 CDP 的 Chromium，监听并保存 OpenRouter 榜单数据。"""
    global agent_running, sequential_chart_count

    from playwright.sync_api import sync_playwright

    agent_running = True
    sequential_chart_count = 0
    collected_fingerprints.clear()
    while not data_queue.empty():
        try:
            data_queue.get_nowait()
            data_queue.task_done()
        except queue.Empty:
            break

    processor = threading.Thread(target=data_processor_thread)
    processor.start()

    with sync_playwright() as p:
        print("🔗 智能体正在连接浏览器底层通信接口...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()
        page.set_default_navigation_timeout(120000)

        current_view_state = {"view": "week"}

        def receive_data(data_str):
            data_queue.put((current_view_state["view"], data_str))

        page.expose_function("sendDataToAgent", receive_data)

        stealth_js = """
        const originalFetch = window.fetch;
        window.fetch = async function(...args) {
            const response = await originalFetch.apply(this, args);
            const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');

            if (url.includes("rankings") || url.includes("_rsc")) {
                response.clone().text().then(text => {
                    if (text.length > 10000) {
                        window.sendDataToAgent(text);
                    }
                }).catch(e => {});
            }
            return response;
        };
        """
        page.add_init_script(stealth_js)

        target_tasks = [
            {"url": "https://openrouter.ai/rankings", "view": "week"},
            {"url": "https://openrouter.ai/rankings?view=day", "view": "day"},
            {"url": "https://openrouter.ai/rankings?view=month", "view": "month"},
        ]

        for task in target_tasks:
            current_view_state["view"] = task["view"]
            print(f"\n🚀 智能体正在前往: {task['url']}")
            try:
                page.goto(task["url"], wait_until="commit")

                if task["view"] == "week":
                    page.wait_for_timeout(2000)
                    print("🔄 正在执行强制刷新，唤醒底层 fetch 请求...")
                    page.reload(wait_until="commit")

                page.wait_for_timeout(3000)

                print("=======================================================")
                print(f"🟢 已加载 {task['view']} 视图！")
                print("👉 【请在浏览器中手动向下滚动网页】，触发所有的榜单数据加载。")
                print("👉 看到终端显示成功保存了你需要的榜单后，在这里按回车键！")
                print("=======================================================")

                print("⌨️ 滚动完成后，请在这里按【回车键】继续...")
                enter_pressed = [False]

                def wait_for_enter():
                    input()
                    enter_pressed[0] = True

                threading.Thread(target=wait_for_enter, daemon=True).start()

                while not enter_pressed[0]:
                    page.wait_for_timeout(200)

            except Exception as e:
                print(f"⚠️ 访问 {task['url']} 时出现异常: {e}")

        print("\n🛑 所有维度的采集任务结束，正在清理现场...")
        page.close()
        agent_running = False
        processor.join()

        merge_json_files()


# ────────────────────────────────────────────────────────────────────────────
# 行业报告 PDF 联网下载与文本抽取
# ────────────────────────────────────────────────────────────────────────────
def _download_url(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        output_path.write_bytes(response.read())
    return output_path


def download_industry_report(output_path: Path | None = None) -> Path:
    """下载 Stanford HAI AI Index 2026 Chapter 4 行业报告 PDF。"""
    target = output_path or Path(SAVE_DIR) / INDUSTRY_REPORT_FILENAME
    if target.exists() and target.stat().st_size > 0:
        return target
    print(f"[*] 正在下载行业报告: {INDUSTRY_REPORT_URL}")
    return _download_url(INDUSTRY_REPORT_URL, target)


def extract_pdf_text(pdf_path: Path, max_pages: int = 30) -> str:
    reader = PdfReader(str(pdf_path))
    pages = []
    for index, page in enumerate(reader.pages[:max_pages], start=1):
        text = (page.extract_text() or "").strip()
        pages.append(f"--- Page {index} ---\n{text or '[本页无文本]'}")
    return "\n\n".join(pages).strip()


# ────────────────────────────────────────────────────────────────────────────
# Pipeline 适配层：CollectorAgent
# ────────────────────────────────────────────────────────────────────────────
class CollectorAgent(BaseAgent):
    """多源异构数据采集 Agent。

    - 每次调用都启动 Playwright 交互式采集 OpenRouter rankings；
    - 始终联网下载 Stanford AI Index 行业报告 PDF；
    - 最终返回 `(raw_texts, summary)` 供下游 Analyst-Agent RAG 使用。
    """

    name = "collector"

    def run(self, *args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        save_dir = Path(SAVE_DIR)

        print("[Collector] 启动 Playwright 交互式采集 OpenRouter rankings ...")
        run_agent()

        if not Path(MERGED_FILE_PATH).exists():
            raise RuntimeError(
                f"采集结束但未生成 {MERGED_FILE_PATH}，"
                "请确认 Playwright 流程是否完整完成（每个视图按回车后才会写入）。"
            )

        with open(MERGED_FILE_PATH, "r", encoding="utf-8") as f:
            merged_data: dict[str, Any] = json.load(f)

        pdf_path = save_dir / INDUSTRY_REPORT_FILENAME
        industry_pdf = download_industry_report(pdf_path)
        industry_text = extract_pdf_text(industry_pdf)

        ranking_inventory_lines = [
            f"- `{key}.json`：约 {self._estimate_size(value)} 字节，"
            f"主键示例 = {self._sample_keys(value)}"
            for key, value in merged_data.items()
        ]
        ranking_inventory = (
            "OpenRouter 榜单清单（由 Analyst 从 merged_rankings.json 中读取并解析为多 Sheet 表格）：\n"
            + "\n".join(ranking_inventory_lines)
        )

        raw_texts: list[dict[str, Any]] = [
            {
                "source_type": "openrouter_rankings_inventory",
                "source_file": "merged_rankings.json",
                "text": ranking_inventory,
            },
            {
                "source_type": "industry_report_pdf",
                "source_file": industry_pdf.name,
                "text": industry_text,
                "source_url": INDUSTRY_REPORT_URL,
            },
        ]

        summary_blocks: list[str] = [f"## openrouter_rankings_inventory\n\n{ranking_inventory}"]
        for key, value in merged_data.items():
            preview = self._preview_ranking(value)
            if preview:
                summary_blocks.append(f"## {key} (前若干条)\n\n{preview}")
        summary_blocks.append(
            f"## industry_report_pdf | {industry_pdf.name}\n\n{industry_text[:6000]}"
        )
        summary = re.sub(r"\n{3,}", "\n\n", "\n\n".join(summary_blocks)).strip()
        return raw_texts, summary

    @staticmethod
    def _estimate_size(value: Any) -> int:
        try:
            return len(json.dumps(value, ensure_ascii=False))
        except Exception:
            return -1

    @staticmethod
    def _sample_keys(value: Any) -> str:
        if isinstance(value, dict):
            return ", ".join(list(value.keys())[:6])
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return ", ".join(list(value[0].keys())[:6])
        return type(value).__name__

    @staticmethod
    def _preview_ranking(value: Any, top_n: int = 10) -> str:
        """从原始榜单中抽出关键字段，生成简短的可读摘要（Reviewer 用）。"""
        def _date_key(item: Any, field: str) -> str:
            if not isinstance(item, dict):
                return ""
            raw = str(item.get(field) or "").strip()
            if not raw:
                return ""
            return raw.replace("T", " ").split(" ")[0]

        def _format_timeseries(items: list[dict[str, Any]], *, limit: int | None) -> str:
            sorted_items = sorted(items, key=lambda item: _date_key(item, "x"), reverse=True)
            selected = sorted_items if limit is None else sorted_items[:limit]
            return "\n".join(f"- {it.get('x')}: {it.get('ys')}" for it in selected)

        try:
            if isinstance(value, dict) and isinstance(value.get("data"), list):
                rows = [item for item in value["data"] if isinstance(item, dict)]
                return _format_timeseries(rows, limit=None)

            if isinstance(value, list) and value and isinstance(value[0], dict) and "x" in value[0]:
                ys_keys = set((value[0].get("ys") or {}).keys()) if isinstance(value[0].get("ys"), dict) else set()
                is_market_share = bool({"openai", "google", "anthropic"} & ys_keys)
                return _format_timeseries(value, limit=None if is_market_share else 1)

            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows: list[str] = []
                items = value
                if "date" in value[0]:
                    items = sorted(value, key=lambda item: _date_key(item, "date"), reverse=True)
                    latest_date = _date_key(items[0], "date") if items else ""
                    items = [item for item in items if _date_key(item, "date") == latest_date]
                for item in items[:top_n]:
                    rank = item.get("rank")
                    date = item.get("date")
                    slug = item.get("model_permaslug") or item.get("model_slug") or item.get("title")
                    tokens = item.get("total_tokens")
                    if tokens is None:
                        tokens = (item.get("total_completion_tokens") or 0) + (item.get("total_prompt_tokens") or 0)
                    if rank is not None and slug is not None:
                        date_text = f"date {date} | " if date else ""
                        rows.append(f"- {date_text}rank {rank}: {slug} | tokens={tokens}")
                if rows:
                    return "\n".join(rows)

            if isinstance(value, dict) and any(g in value for g in ("day", "week", "month")):
                rows = []
                for gran in ("day", "week", "month"):
                    items = value.get(gran) or []
                    if isinstance(items, list):
                        for it in items[:top_n]:
                            if not isinstance(it, dict):
                                continue
                            app = it.get("app") or {}
                            rows.append(
                                f"- [{gran}] rank {it.get('rank')}: {app.get('title')} | "
                                f"tokens={it.get('total_tokens')}"
                            )
                return "\n".join(rows)
        except Exception:
            return ""
        return ""


if __name__ == "__main__":
    run_agent()
