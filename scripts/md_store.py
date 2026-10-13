#!/usr/bin/env python3
"""markdown 表格存储模块：读写 data/*.md 中的数据表

md 文件结构：
  # 标题
  - 元信息若干行
  （空行）
  | 列1 | 列2 | ... | 登记时间 |
  | --- | --- | ... | --- |
  | 数据行... |

单元格转义：`|` 写为 `\\|`；换行写为 `<br>`。
"""
import os
import re
from datetime import datetime, timedelta, timezone

# 东八区（B站数据均为北京时间）
TZ_CN = timezone(timedelta(hours=8))


def now_cn() -> str:
    """当前北京时间（yyyy-MM-dd HH:mm）。"""
    return datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")


def ts_to_cn(ts) -> str:
    """秒级 Unix 时间戳 → 北京时间字符串。"""
    return datetime.fromtimestamp(int(ts), TZ_CN).strftime("%Y-%m-%d %H:%M")


def esc_cell(s: str) -> str:
    """单元格转义：换行 → <br>，| → \\|。"""
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return s.replace("|", "\\|")


def _split_row(line: str) -> list:
    cells = re.split(r"(?<!\\)\|", line.strip())
    return [c.strip().replace("\\|", "|") for c in cells[1:-1]]


def read_table(path: str):
    """读取 md 中的数据表，返回 (rows, columns)。文件不存在或无表时返回 ([], [])。

    rows 为 {列名: 单元格文本} 的列表（保持文件内顺序）。
    """
    if not os.path.exists(path):
        return [], []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("|") and "登记时间" in line:
            columns = [c.strip() for c in _split_row(line)]
            rows = []
            for ln in lines[i + 2:]:
                if not ln.startswith("|"):
                    break
                cells = _split_row(ln)
                if len(cells) != len(columns):
                    continue
                rows.append(dict(zip(columns, cells)))
            return rows, columns
    return [], []


def write_table(path: str, title: str, meta_lines: list, columns: list, rows: list):
    """整表写入 md 文件。"""
    out = [f"# {title}", ""]
    out += [f"- {m}" for m in meta_lines]
    out += ["",
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join(["---"] * len(columns)) + " |"]
    for r in rows:
        out.append("| " + " | ".join(esc_cell(str(r.get(c, "") or "")) for c in columns) + " |")
    out.append("")
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    os.replace(tmp, path)
