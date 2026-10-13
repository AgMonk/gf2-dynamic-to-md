#!/usr/bin/env python3
"""B站图文（动态/专栏）opus feed 拉取模块

接口：https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/feed/space
无需 WBI 签名，直接 GET。请求方式复刻自
gitee.com/AgMonk/gf2-dynamic-to-tencentdoc 的 fetch_opus/fetch_opus_page1.py，
并补充全量/增量分页控制：

- full=True   ：逐页拉取直到 has_more=false（完整数据）；
- full=False  ：增量模式，当某一页的全部 opus_id 均已知（stop_ids）时提前停止，
                适配定时任务的增量更新。
"""
import json
import os
import ssl
import time
import urllib.parse
import urllib.request

# UP 主 UID（少女前线2：追放官方，与原仓库一致）
HOST_MID = "697654195"
# 图文/动态 feed 接口
OPUS_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/feed/space"
# 防止 has_more 一直为 true 时的死循环兜底上限
HARD_PAGE_LIMIT = 200
# 翻页间隔（秒），避免触发风控
PAGE_SLEEP = float(os.environ.get("FETCH_SLEEP", "0.8"))
# 单页请求重试次数与间隔（代理/网络抖动时自动重试）
OPUS_RETRIES = int(os.environ.get("OPUS_RETRIES", "3"))
RETRY_SLEEP = float(os.environ.get("OPUS_RETRY_SLEEP", "5"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://space.bilibili.com/{HOST_MID}/",
}


def fetch_opus_page1(feed_type: str, offset: str = ""):
    """拉取 UP 主图文列表一页，返回 (items, page_info)。

    feed_type："dynamic"（动态）或 "article"（专栏）。
    offset：分页游标，"" 表示第一页。
    page_info = {"offset": 下一页游标, "has_more": 是否还有下一页, "total": 总条数}。
    请求失败或 code 非 0 时返回 ([], {})，不抛异常。
    """
    type_label = "动态" if feed_type == "dynamic" else "专栏"
    params = {
        "offset": offset, "host_mid": HOST_MID, "timezone_offset": "-480",
        "platform": "web", "features": "itemOpusStyle",
        "type": feed_type, "web_location": "333.1007",
    }
    url = OPUS_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        if data.get("code") != 0:
            print(f"[{type_label}] API错误: code={data.get('code')} {data.get('message')}", flush=True)
            return [], {}
        body = data.get("data", {}) or {}
        page_info = {
            "offset": body.get("offset", ""),
            "has_more": bool(body.get("has_more", False)),
            "total": body.get("total", 0),
        }
        result = []
        for item in body.get("items", []):
            opus_id = str(item.get("opus_id", "") or "")
            jump_url = item.get("jump_url", "") or ""
            if jump_url.startswith("//"):
                full_url = "https:" + jump_url
            elif jump_url.startswith("/"):
                full_url = "https://www.bilibili.com" + jump_url
            else:
                full_url = jump_url
            content_preview = item.get("content", "") or item.get("title", "") or ""
            like_count = item.get("stat", {}).get("like", 0)
            result.append({
                "opus_id": opus_id, "jump_url": full_url,
                "content_preview": content_preview, "like_count": like_count,
            })
        print(f"[{type_label}] 成功获取 {len(result)} 条, has_more={page_info['has_more']}", flush=True)
        return result, page_info
    except Exception as e:
        print(f"[{type_label}] 抓取失败: {e}", flush=True)
        return [], {}


def fetch_opus(feed_type: str, stop_ids=None, full: bool = False,
               max_pages: int = HARD_PAGE_LIMIT):
    """按分页策略拉取全部条目，返回 (items, complete)。

    complete=True 表示拉到了接口末尾（has_more=false）；
    增量模式（stop_ids 命中整页已知而提前停止）时为 False。
    """
    items = []
    offset = ""
    complete = False
    for page_no in range(1, max_pages + 1):
        page_items, info = [], {}
        for attempt in range(1, OPUS_RETRIES + 1):
            page_items, info = fetch_opus_page1(feed_type, offset=offset)
            if info:
                break
            if attempt < OPUS_RETRIES:
                print(f"[{'动态' if feed_type == 'dynamic' else '专栏'}] 第{page_no}页请求失败，{RETRY_SLEEP}s 后重试 ({attempt}/{OPUS_RETRIES})", flush=True)
                time.sleep(RETRY_SLEEP)
        if not info:
            break  # 重试后仍失败
        items.extend(page_items)
        if not info.get("has_more"):
            complete = True
            break
        offset = info.get("offset") or ""
        if not offset:
            break
        if stop_ids is not None and page_items and all(
                str(i.get("opus_id", "")) in stop_ids for i in page_items):
            print(f"[{'动态' if feed_type == 'dynamic' else '专栏'}] 第{page_no}页全部已知，增量提前结束", flush=True)
            break
        time.sleep(PAGE_SLEEP)
    return items, complete
