"""Collector-Agent：多源异构数据采集与结构化整理。"""

from __future__ import annotations

import re
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup
from pypdf import PdfReader

from src.agents.base import BaseAgent

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_GSMA_REPORT_URL = (
    "https://www.gsma.com/solutions-and-impact/connectivity-for-good/"
    "mobile-economy/the-mobile-economy-2024/"
)


class CollectorAgent(BaseAgent):
    """多源异构数据采集 Agent。"""

    name = "collector"

    @staticmethod
    def _resolve_path(path: str | Path) -> Path:
        p = Path(path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @staticmethod
    def _validate_file(path: Path, label: str) -> None:
        if not path.exists():
            raise FileNotFoundError(f"{label} 文件不存在: {path}")
        if not path.is_file():
            raise ValueError(f"{label} 路径不是有效文件: {path}")

    def _get_collector_config(self) -> dict[str, Any]:
        return self.config.get("collector", {}) or {}

    @staticmethod
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

    @staticmethod
    def _download_kaggle_dataset(dataset_url: str, output_dir: Path) -> None:
        """调用 Kaggle REST API 直接下载并解压，不依赖易报错的旧版 kaggle 库"""
        output_dir.mkdir(parents=True, exist_ok=True)
        dataset_id = "/".join(dataset_url.rstrip("/").split("/")[-2:])
        zip_path = output_dir / f"{dataset_id.split('/')[-1]}.zip"
        
        # 读取你刚刚在 PowerShell 中成功生成的 Token
        token_path = Path.home() / ".kaggle" / "access_token"
        if not token_path.exists():
            raise FileNotFoundError(f"未找到 Kaggle Token，请确认文件存在: {token_path}")
            
        token = token_path.read_text().strip()
        api_url = f"https://www.kaggle.com/api/v1/datasets/download/{dataset_id}"
        
        request = urllib.request.Request(
            api_url,
            headers={"Authorization": f"Bearer {token}"}
        )
        
        print(f"[*] 正在通过 Kaggle API 下载数据集: {dataset_id} ...")
        with urllib.request.urlopen(request, timeout=120) as response:
            zip_path.write_bytes(response.read())
            
        print(f"[*] 下载完成，正在解压...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(output_dir)
            
        # 解压后删除 zip 文件节省空间
        if zip_path.exists():
            zip_path.unlink()

    def _download_industry_report(self, report_url: str, pdf_path: Path) -> Path:
        page_path = pdf_path.with_suffix(".html")
        self._download_url(report_url, page_path)
        html = page_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        candidates: list[str] = []
        for link in soup.find_all("a", href=True):
            href = str(link["href"]).strip()
            text = link.get_text(" ", strip=True).lower()
            if ".pdf" in href.lower() or "download" in text or "report" in text:
                candidates.append(href)

        pdf_url = ""
        for href in candidates:
            if ".pdf" in href.lower():
                pdf_url = urllib.request.urljoin(report_url, href)
                break

        if not pdf_url:
            raise RuntimeError(f"未能在 GSMA 页面中发现 PDF 下载链接: {report_url}")

        return self._download_url(pdf_url, pdf_path)

    @staticmethod
    def _dataframe_to_markdown(df: pd.DataFrame, max_rows: int = 80) -> str:
        preview = df.head(max_rows).copy()
        return preview.fillna("").to_markdown(index=False)

    def _csv_to_multisheet_excel(self, csv_path: Path, output_path: Path, brand: str) -> tuple[Path, str]:
        self._validate_file(csv_path, f"{brand} CSV")
        df = pd.read_csv(csv_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        sheet_map: dict[str, pd.DataFrame] = {
            "sales_detail": df,
            "region_summary": (
                df.groupby(["year", "region", "country"], dropna=False)
                .agg(units_sold=("units_sold", "sum"), revenue_usd=("revenue_usd", "sum"))
                .reset_index()
                .sort_values(["year", "revenue_usd"], ascending=[True, False])
            ),
            "product_summary": (
                df.groupby(["year", "category", "product_name"], dropna=False)
                .agg(
                    units_sold=("units_sold", "sum"),
                    revenue_usd=("revenue_usd", "sum"),
                    avg_unit_price_usd=("unit_price_usd", "mean"),
                    avg_customer_rating=("customer_rating", "mean"),
                )
                .reset_index()
                .sort_values(["year", "revenue_usd"], ascending=[True, False])
            ),
            "channel_customer": (
                df.groupby(["year", "sales_channel", "customer_segment"], dropna=False)
                .agg(units_sold=("units_sold", "sum"), revenue_usd=("revenue_usd", "sum"))
                .reset_index()
                .sort_values(["year", "revenue_usd"], ascending=[True, False])
            ),
        }

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            for sheet_name, sheet_df in sheet_map.items():
                sheet_df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

        sections = [f"# {brand} sales dataset"]
        sections.append(f"Rows: {len(df)}")
        sections.append(f"Columns: {', '.join(df.columns)}")
        for sheet_name, sheet_df in sheet_map.items():
            sections.append(f"\n## Sheet: {sheet_name}\n")
            sections.append(self._dataframe_to_markdown(sheet_df))

        return output_path, "\n".join(sections)

    @staticmethod
    def _extract_pdf_text(pdf_path: Path, max_pages: int = 20) -> str:
        reader = PdfReader(str(pdf_path))
        pages = []
        for index, page in enumerate(reader.pages[:max_pages], start=1):
            text = (page.extract_text() or "").strip()
            pages.append(f"--- Page {index} ---\n{text or '[本页无文本]'}")
        return "\n\n".join(pages).strip()

    @staticmethod
    def _normalize_report_url(url_or_path: str | Path | None) -> str:
        value = str(url_or_path or "").strip()
        return value or DEFAULT_GSMA_REPORT_URL
        
    def _find_csv(self, dir_path: Path, keyword: str) -> Path:
        for p in dir_path.glob("*.csv"):
            if keyword in p.name.lower():
                return p
        raise FileNotFoundError(f"未在 {dir_path} 找到包含 '{keyword}' 的 CSV 文件")

    def run(self, *args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        cfg = self._get_collector_config()
        
        data_dir = self._resolve_path("data")
        pdf_path = data_dir / "The-Mobile-Economy-2024.pdf"
        
        apple_url = "https://www.kaggle.com/datasets/ashyou09/apple-global-product-sales-dataset"
        samsung_url = "https://www.kaggle.com/datasets/ashyou09/samsung-global-product-sales-dataset"
        report_url = self._normalize_report_url(kwargs.get("industry_report_url") or cfg.get("industry_report_url"))

        # 1. 联网下载 Kaggle 数据集与 PDF 报告至本地
        self._download_kaggle_dataset(apple_url, data_dir)
        self._download_kaggle_dataset(samsung_url, data_dir)
        industry_pdf = self._download_industry_report(report_url, pdf_path)

        # 2. 从本地读取刚刚下载的数据集
        apple_csv = self._find_csv(data_dir, "apple")
        samsung_csv = self._find_csv(data_dir, "samsung")

        processed_dir = self._resolve_path(cfg.get("processed_dir") or "data/processed")
        apple_xlsx, apple_text = self._csv_to_multisheet_excel(
            apple_csv,
            processed_dir / "apple_sales_multisheet.xlsx",
            "Apple",
        )
        samsung_xlsx, samsung_text = self._csv_to_multisheet_excel(
            samsung_csv,
            processed_dir / "samsung_sales_multisheet.xlsx",
            "Samsung",
        )

        industry_text = self._extract_pdf_text(industry_pdf)

        raw_texts = [
            {
                "source_type": "sales_excel",
                "source_file": apple_xlsx.name,
                "text": apple_text,
            },
            {
                "source_type": "industry_report_pdf",
                "source_file": industry_pdf.name,
                "text": industry_text,
                "source_url": report_url,
            },
            {
                "source_type": "competitor_excel",
                "source_file": samsung_xlsx.name,
                "text": samsung_text,
            },
        ]

        summary = "\n\n".join(
            f"## {item['source_type']} | {item['source_file']}\n\n{item['text']}"
            for item in raw_texts
        )
        summary = re.sub(r"\n{3,}", "\n\n", summary).strip()
        return raw_texts, summary