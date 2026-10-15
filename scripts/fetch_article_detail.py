#!/usr/bin/env python3
"""专栏详情抓取模块：opus id → 正文全文 → 输出 md 文件

取数策略（两路，数据同构，解析代码复用）：
1. API：GET https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail?id={opus_id}
   - 无需登录/WBI 签名，HEADERS 与 fetch_opus.py 一致；
   - data.item 非 null 时直接可用；
   - 老专栏返回 item=null + fallback{type:2, id:cvid}，转第 2 路。
2. 网页兜底：GET https://www.bilibili.com/opus/{opus_id}，
   解析 window.__INITIAL_STATE__.detail（结构同 API 的 data.item）。

用法：python3 scripts/fetch_article_detail.py <opus_id> [opus_id ...]
      python3 scripts/fetch_article_detail.py --from-table [表格路径]
        （--from-table：读 data/专栏.md 的 opus_id 列，补抓缺失详情，已有则跳过）
输出：data/专栏详情/{发布时间YYYYMMDD_HHMM}_{标题}.md
"""
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

import md_store
from md_store import TZ_CN

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPUS_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail"
OPUS_PAGE = "https://www.bilibili.com/opus/"
OUT_DIR = os.path.join(BASE_DIR, "data", "专栏详情")
TABLE_FILE = os.path.join(BASE_DIR, "data", "专栏.md")

RETRIES = int(os.environ.get("DETAIL_RETRIES", "3"))
RETRY_SLEEP = float(os.environ.get("DETAIL_RETRY_SLEEP", "5"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bilibili.com/",
}
PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _http_get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
        return resp.read()


def fetch_detail_api(opus_id: str):
    """opus/detail API。返回 detail dict，item 为 null（老专栏）时返回 None。"""
    url = OPUS_API + "?" + urllib.parse.urlencode({"id": opus_id})
    data = json.loads(_http_get(url, HEADERS).decode("utf-8", "ignore"))
    if data.get("code") != 0:
        raise RuntimeError(f"opus/detail code={data.get('code')} {data.get('message')}")
    return (data.get("data") or {}).get("item")


def _extract_initial_state(html: str) -> dict:
    """从页面 HTML 提取 window.__INITIAL_STATE__（括号配平，跳过字符串）。"""
    i = html.find("window.__INITIAL_STATE__=")
    if i == -1:
        raise RuntimeError("页面中未找到 __INITIAL_STATE__")
    start = html.find("{", i)
    depth, j, in_str, esc = 0, start, False, False
    while j < len(html):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return json.loads(html[start:j + 1])


def _html_to_paras(html_text: str) -> list:
    """专栏 content HTML → 伪 paragraphs（图片段/文本段，同 opus 结构）。"""
    parts = re.split(r'<img[^>]*\bsrc=["\']([^"\']+)["\'][^>]*/?>', html_text or "")
    paras = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # img src 捕获组
            url = "https:" + part if part.startswith("//") else part
            paras.append({"para_type": 2, "pic": {"pics": [{"url": url}]}})
        else:
            t = re.sub(r"</p>\s*<p[^>]*>", "\n\n", part)
            t = re.sub(r"<br\s*/?>", "\n", t)
            t = re.sub(r"<[^>]+>", "", t)
            t = html.unescape(t)
            if t.strip():
                paras.append({"para_type": 1, "text": {"nodes": [{"word": {"words": t}}]}})
    return paras


def _read_state_to_detail(state: dict) -> dict:
    """read 页面 INITIAL_STATE（readInfo）→ 伪 opus detail 结构，供 parse_detail 复用。"""
    ri = state.get("readInfo") or {}
    author = ri.get("author") or {}
    modules = [
        {"module_type": "MODULE_TYPE_TITLE",
         "module_title": {"text": ri.get("title", "") or ""}},
        {"module_type": "MODULE_TYPE_AUTHOR",
         "module_author": {"name": author.get("name", "") or "",
                           "pub_ts": ri.get("publish_time", 0) or 0}},
        {"module_type": "MODULE_TYPE_TOP",
         "module_top": {"display": {"album": {"pics": (
             [{"url": ri["banner_url"]}] if ri.get("banner_url") else [])}}}},
        {"module_type": "MODULE_TYPE_CONTENT",
         "module_content": {"paragraphs": _html_to_paras(ri.get("content", ""))}},
        {"module_type": "MODULE_TYPE_BOTTOM",
         "module_bottom": {"share_info": {"summary": ri.get("summary", "") or ""}}},
    ]
    return {"id_str": str(ri.get("id", "") or ""), "modules": modules}


def fetch_detail_page(opus_id: str) -> dict:
    """网页兜底：解析页面内嵌 INITIAL_STATE。

    opus 页面返回 detail（与 API item 同构）；2024 年前的老专栏会重定向到
    read/cv 页面，返回 readInfo 结构（content 为 HTML），转换为伪 detail。
    """
    html_text = _http_get(OPUS_PAGE + opus_id, PAGE_HEADERS).decode("utf-8", "ignore")
    state = _extract_initial_state(html_text)
    detail = state.get("detail")
    if detail and detail.get("modules"):
        return detail
    if state.get("readInfo"):
        converted = _read_state_to_detail(state)
        if converted["modules"][3]["module_content"]["paragraphs"]:
            return converted
    raise RuntimeError("INITIAL_STATE 中无 detail/readInfo 数据")


def fetch_detail(opus_id: str) -> dict:
    """opus id → detail dict（API 优先，老专栏/失败走网页兜底）。"""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            item = fetch_detail_api(opus_id)
            if item:
                return item
            last_err = RuntimeError("opus/detail 返回 item=null（老专栏，走网页）")
            break  # item=null 是确定性的老专栏，直接换路，不重试
        except Exception as e:
            last_err = e
            print(f"[{opus_id}] API 第{attempt}次失败: {e}", flush=True)
            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP)
    for attempt in range(1, RETRIES + 1):
        try:
            return fetch_detail_page(opus_id)
        except Exception as e:
            last_err = e
            print(f"[{opus_id}] 网页第{attempt}次失败: {e}", flush=True)
            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP)
    raise last_err


def _module(detail: dict, module_type: str) -> dict:
    for m in detail.get("modules", []):
        if m.get("module_type") == module_type:
            return m
    return {}


def parse_detail(opus_id: str, detail: dict) -> dict:
    """detail → 扁平结构（标题/横幅/摘要/时间/正文段落）。"""
    title_mod = _module(detail, "MODULE_TYPE_TITLE").get("module_title") or {}
    author = _module(detail, "MODULE_TYPE_AUTHOR").get("module_author") or {}
    bottom = _module(detail, "MODULE_TYPE_BOTTOM").get("module_bottom") or {}
    top = _module(detail, "MODULE_TYPE_TOP").get("module_top") or {}
    content = _module(detail, "MODULE_TYPE_CONTENT").get("module_content") or {}

    banner = ""
    display = top.get("display") or {}
    pics = (display.get("album") or {}).get("pics") or []
    if pics:
        banner = pics[0].get("url", "")

    paras = []
    for p in content.get("paragraphs", []):
        if p.get("pic"):
            urls = [pp.get("url", "") for pp in p["pic"].get("pics", []) if pp.get("url")]
            if urls:
                paras.append({"type": "pic", "urls": urls})
        elif p.get("text"):
            txt = "".join(
                n.get("word", {}).get("words", "") for n in p["text"].get("nodes", []))
            if txt.strip():
                paras.append({"type": "text", "text": txt})

    summary = (bottom.get("share_info") or {}).get("summary", "") or ""
    summary = re.sub(r"\s*\[图片\]\s*", " ", summary).strip()

    return {
        "opus_id": opus_id,
        "title": (title_mod.get("text") or "").strip(),
        "banner": banner,
        "summary": summary,
        "pub_time": md_store.ts_to_cn(author.get("pub_ts", 0)) if author.get("pub_ts") else "",
        # 文件名用时间前缀（可排序）：YYYYMMDD_HHMM
        "date_key": datetime.fromtimestamp(int(author.get("pub_ts", 0)), TZ_CN).strftime("%Y%m%d_%H%M") if author.get("pub_ts") else opus_id,
        "author": author.get("name", ""),
        "paragraphs": paras,
    }


def safe_filename(title: str) -> str:
    """标题清理为文件名安全字符（Windows 非法字符 + 控制前后空格）。"""
    t = re.sub(r'[\\/:*?"<>|]', "", title)
    return t.strip().strip(".") or "untitled"


def render_md(info: dict) -> str:
    """解析结果 → md 文本：横幅图 → 摘要+发布时间 → 正文（含图片）。"""
    out = [f"# {md_store.esc_md(info['title'])}", ""]
    if info["banner"]:
        out += [f"![横幅]({info['banner']})", ""]
    # share_info.summary 实为全文压缩版，仅取前 120 字作摘要
    summary = info["summary"]
    if len(summary) > 120:
        summary = summary[:120].rstrip() + "……"
    out += [f"> **摘要**：{md_store.esc_md(summary)}", ""]
    out += [f"- 发布时间：{info['pub_time']}",
            f"- UP 主：{info['author']}",
            f"- 原文链接：https://www.bilibili.com/opus/{info['opus_id']}", ""]
    out += ["---", ""]
    for p in info["paragraphs"]:
        if p["type"] == "pic":
            for u in p["urls"]:
                out += [f"![]({u})", ""]
        else:
            # 段内换行处理：≥2 个 \n 视为段落边界（输出空行）；
            # 单个 \n 输出行尾两空格（md 硬换行），否则 GitHub 渲染会合并为空格
            for block in re.split(r"\n{2,}", p["text"]):
                lines = [md_store.esc_md(ln) for ln in block.split("\n")]
                if not any(ln.strip() for ln in lines):
                    continue
                for ln in lines[:-1]:
                    out.append(ln + "  ")
                out += [lines[-1], ""]
    return "\n".join(out).rstrip() + "\n"


def save_one(opus_id: str) -> str:
    detail = fetch_detail(opus_id)
    info = parse_detail(opus_id, detail)
    path = os.path.join(OUT_DIR, f"{info['date_key']}_{safe_filename(info['title'])}.md")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_md(info))
    print(f"[{opus_id}] 已保存: {os.path.relpath(path, BASE_DIR)} (正文段落 {len(info['paragraphs'])} 段)", flush=True)
    return path


def collect_existing_ids() -> set:
    """扫描详情目录，从各 md 的「原文链接」行提取已有详情的 opus_id。"""
    ids = set()
    if not os.path.isdir(OUT_DIR):
        return ids
    pat = re.compile(r"opus/(\d+)")
    for name in os.listdir(OUT_DIR):
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(OUT_DIR, name), encoding="utf-8") as f:
                m = pat.search(f.read())
            if m:
                ids.add(m.group(1))
        except OSError:
            continue
    return ids


def ids_from_table(path: str) -> list:
    """读专栏表格的 opus_id 列（保持表内顺序）。"""
    rows, _ = md_store.read_table(path)
    return [r.get("opus_id", "") for r in rows if r.get("opus_id")]


def sync_from_table(path: str = TABLE_FILE) -> int:
    """对照专栏表格补齐缺失详情，返回失败条数。"""
    all_ids = ids_from_table(path)
    existing = collect_existing_ids()
    todo = [i for i in all_ids if i not in existing]
    print(f"表格共 {len(all_ids)} 条，已有详情 {len(existing & set(all_ids))} 条，待抓取 {len(todo)} 条", flush=True)
    failed = 0
    for i, opus_id in enumerate(todo):
        try:
            save_one(opus_id)
        except Exception as e:
            print(f"[{opus_id}] 抓取失败: {e}", flush=True)
            failed += 1
        if i < len(todo) - 1:
            time.sleep(1)
    return failed


def main():
    args = sys.argv[1:]
    if args and args[0] == "--from-table":
        table = args[1] if len(args) > 1 else TABLE_FILE
        sys.exit(1 if sync_from_table(table) else 0)
    if not args:
        print(__doc__)
        sys.exit(1)
    failed = []
    for i, opus_id in enumerate(args):
        try:
            save_one(opus_id)
        except Exception as e:
            print(f"[{opus_id}] 抓取失败: {e}", flush=True)
            failed.append(opus_id)
        if i < len(args) - 1:
            time.sleep(1)
    if failed:
        print(f"失败 {len(failed)} 个: {', '.join(failed)}", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
