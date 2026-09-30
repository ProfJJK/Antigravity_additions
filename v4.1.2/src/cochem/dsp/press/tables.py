"""Publication Data Table Formatter (MC-DSP-19)."""
from __future__ import annotations

from typing import Any, Sequence
import pandas as pd


def _extract_table_data(
    headers_or_data: Any,
    rows: Sequence[Sequence[Any]] | str | None = None,
) -> tuple[list[str], list[list[Any]]]:
    """Extracts normalized headers and row values from a DataFrame or lists."""
    if isinstance(headers_or_data, pd.DataFrame):
        headers = [str(c) for c in headers_or_data.columns]
        row_values = headers_or_data.values.tolist()
        return headers, [[str(val) for val in r] for r in row_values]

    if hasattr(headers_or_data, "columns") and hasattr(headers_or_data, "values"):
        headers = [str(c) for c in headers_or_data.columns]
        return headers, [[str(val) for val in r] for r in headers_or_data.values.tolist()]

    headers = [str(h) for h in headers_or_data] if headers_or_data else []
    if rows is not None and not isinstance(rows, str):
        row_values = [[str(cell) for cell in r] for r in rows]
    else:
        row_values = []
    return headers, row_values


def format_dataframe_to_booktabs(df: pd.DataFrame) -> str:
    """Formats a pandas DataFrame directly into LaTeX booktabs format."""
    headers, rows = _extract_table_data(df)
    return format_publication_table(headers, rows, fmt="latex")


def format_dataframe_to_typst(df: pd.DataFrame) -> str:
    """Formats a pandas DataFrame directly into Typst table syntax."""
    headers, rows = _extract_table_data(df)
    return format_publication_table(headers, rows, fmt="typst")


def format_publication_table(
    headers: list[str] | Any,
    rows: list[list[Any]] | Sequence[Sequence[Any]] | str | None = None,
    fmt: str = "typst",
) -> str:
    """Formats structured tabular data or pandas DataFrames into publication-grade Typst or LaTeX booktabs."""
    if isinstance(rows, str) and fmt == "typst":
        fmt = rows
        rows = None

    hdr_list, row_list = _extract_table_data(headers, rows)
    num_cols = len(hdr_list)

    if fmt.lower() in ("typst", "typ"):
        cell_items = [f"[{h}]" for h in hdr_list]
        for row in row_list:
            cell_items.extend(f"[{c}]" for c in row)
        cells_str = ", ".join(cell_items)
        return f"#table(columns: {num_cols}, {cells_str})" if cells_str else f"#table(columns: {num_cols})"

    # LaTeX booktabs format
    col_spec = "l" * max(num_cols, 1)
    latex_lines = [
        f"\\begin{{tabular}}{{{col_spec}}}",
        "\\toprule",
    ]
    if hdr_list:
        latex_lines.append(" & ".join(hdr_list) + " \\\\")
        latex_lines.append("\\midrule")
    for r in row_list:
        latex_lines.append(" & ".join(r) + " \\\\")
    latex_lines.append("\\bottomrule")
    latex_lines.append("\\end{tabular}")
    return "\n".join(latex_lines)
