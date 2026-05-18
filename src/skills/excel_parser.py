"""Excel 多 Sheet 解析 Skill。"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import pandas as pd


def parse_sales_excel(file_path: Union[str, Path]) -> str:
    """
    读取多 Sheet Excel，空值 fillna 后转为 Markdown 表格文本。

    Args:
        file_path: Excel 文件路径。

    Returns:
        所有 Sheet 拼接后的 Markdown 字符串。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件为空或无法解析。
        RuntimeError: 其他解析错误。
    """
    path = Path(file_path)

    try:
        if not path.exists():
            raise FileNotFoundError(f"Excel 文件不存在: {path}")

        if not path.is_file():
            raise ValueError(f"路径不是有效文件: {path}")

        sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")

        if not sheets:
            raise ValueError(f"Excel 无可用 Sheet: {path}")

        parts: list[str] = []

        for sheet_name, df in sheets.items():
            try:
                df = df.fillna("")
                markdown_table = df.to_markdown(index=False)
                parts.append(f"## Sheet: {sheet_name}\n\n{markdown_table}")
            except Exception as exc:
                parts.append(f"## Sheet: {sheet_name}\n\n[解析失败: {exc}]")

        result = "\n\n".join(parts).strip()

        if not result:
            raise ValueError(f"Excel 解析结果为空: {path}")

        return result

    except FileNotFoundError:
        raise
    except ValueError:
        raise
    except pd.errors.EmptyDataError as exc:
        raise ValueError(f"Excel 文件无数据: {path}") from exc
    except Exception as exc:
        raise RuntimeError(f"解析 Excel 失败 [{path}]: {exc}") from exc
