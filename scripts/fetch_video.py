#!/usr/bin/env python3
"""B站 UP 主投稿视频列表拉取模块

优先使用合集/关键词接口（无需 WBI 签名，数据中心 IP 风控概率低）：
  https://api.bilibili.com/x/series/recArchivesByKeywords?mid=&keywords=&ps=&pn=
  keywords 为空时返回全部投稿（按发布时间倒序）。

回退使用原仓库方式（WBI 签名，复刻自
gitee.com/AgMonk/gf2-dynamic-to-tencentdoc 的 fetch_video/fetch_video_list.py）：
  https://api.bilibili.com/x/space/wbi/arc/search（优先 curl_cffi，缺失时降级 urllib）

可通过环境变量调整：
  VIDEO_RETRIES      每页重试次数（默认 5）
  VIDEO_RETRY_SLEEP  重试间隔秒数（默认 15）
  FETCH_SLEEP        翻页间隔秒数（默认 0.8）
"""
import hashlib
import json
import os
import ssl
import time
import urllib.parse
import urllib.request

# UP 主 UID（少女前线2：追放官方，与原仓库一致）
HOST_MID = "697654195"
VIDEO_API = "https://api.bilibili.com/x/space/wbi/arc/search"
KEYWORDS_API = "https://api.bilibili.com/x/series/recArchivesByKeywords"
PS = 50              # WBI 接口每页大小
KEYWORDS_PS = 30     # keywords 接口每页大小（接口上限 30）
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
WBI_CACHE = os.path.join(WORK_DIR, ".bili_wbi.json")
PAGE_SLEEP = float(os.environ.get("FETCH_SLEEP", "0.8"))
RETRIES = int(os.environ.get("VIDEO_RETRIES", "5"))
RETRY_SLEEP = int(os.environ.get("VIDEO_RETRY_SLEEP", "15"))

MIXIN_KEY_ENC = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def mixin_key(orig: str) -> str:
    """根据 MIXIN_KEY_ENC 重排得到 32 位 mixin key。"""
    return "".join(orig[i] for i in MIXIN_KEY_ENC)[:32]


def sign(params: dict, img_key: str, sub_key: str) -> dict:
    """为请求参数添加 wts（时间戳）与 w_rid（MD5 签名）。"""
    params = dict(params)
    params["wts"] = int(time.time())
    mix = mixin_key(img_key + sub_key)
    q = urllib.parse.urlencode(sorted(params.items(), key=lambda x: x[0]))
    params["w_rid"] = hashlib.md5((q + mix).encode("utf-8")).hexdigest()
    return params


def _extract_wbi(nav: dict) -> tuple:
    wbi = nav["data"]["wbi_img"]
    img = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    return img, sub


def _save_cache(img: str, sub: str):
    try:
        with open(WBI_CACHE, "w", encoding="utf-8") as f:
            json.dump({"img": img, "sub": sub, "ts": int(time.time())}, f)
    except Exception:
        pass


def get_wbi_keys():
    """获取 WBI 签名密钥（img_key / sub_key），带 1 小时本地缓存。"""
    if os.path.exists(WBI_CACHE):
        try:
            with open(WBI_CACHE, "r", encoding="utf-8") as f:
                c = json.load(f)
            if time.time() - c.get("ts", 0) < 3600:
                return c["img"], c["sub"]
        except Exception:
            pass
    try:
        from curl_cffi import requests as curl_requests
        s = curl_requests.Session(impersonate="chrome124")
        s.get("https://www.bilibili.com/", timeout=30)
        r = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=30)
        img, sub = _extract_wbi(r.json())
        _save_cache(img, sub)
        return img, sub
    except ImportError:
        pass
    req = urllib.request.Request(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        nav = json.loads(resp.read().decode("utf-8"))
    img, sub = _extract_wbi(nav)
    _save_cache(img, sub)
    return img, sub


def _normalize_archive(a: dict) -> dict:
    bvid = a.get("bvid")
    return {
        "id": bvid, "bvid": bvid, "title": a.get("title", ""),
        "description": a.get("description", ""), "created": a.get("pubdate") or a.get("created"),
        "pic": a.get("pic"), "url": f"https://www.bilibili.com/video/{bvid}",
    }


def _fetch_keywords_page(pn: int, ps: int):
    """recArchivesByKeywords 拉取一页，成功返回 (items, page_info)，失败返回 ([], None)。"""
    for attempt in range(1, RETRIES + 1):
        try:
            from curl_cffi import requests as curl_requests
            s = curl_requests.Session(impersonate="chrome124")
            s.get("https://www.bilibili.com/", timeout=30)
            url = (f"{KEYWORDS_API}?mid={HOST_MID}&keywords=&ps={ps}&pn={pn}")
            r = s.get(url, timeout=30,
                      headers={"Referer": f"https://space.bilibili.com/{HOST_MID}/"})
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.json()
            if data.get("code") == 0:
                body = data.get("data") or {}
                items = [_normalize_archive(a) for a in (body.get("archives") or [])]
                total = (body.get("page") or {}).get("total") or 0
                print(f"[视频] keywords接口 第{pn}页 成功获取 {len(items)} 条", flush=True)
                return items, {"total": total, "num": pn, "size": ps}
            print(f"[视频] keywords接口 尝试 {attempt}/{RETRIES}: code={data.get('code')} {data.get('message')}", flush=True)
        except Exception as e:
            print(f"[视频] keywords接口 尝试 {attempt}/{RETRIES}: {e}", flush=True)
        time.sleep(RETRY_SLEEP)
    return [], None


def _parse_video_items(data: dict) -> list:
    vlist = data.get("data", {}).get("list", {}).get("vlist", [])
    items = []
    for v in vlist:
        bvid = v.get("bvid")
        items.append({
            "id": bvid, "bvid": bvid, "title": v.get("title", ""),
            "description": v.get("description", ""), "created": v.get("created"),
            "pic": v.get("pic"), "url": f"https://www.bilibili.com/video/{bvid}",
        })
    return items


def _parse_page_info(data: dict) -> dict:
    page = (data.get("data") or {}).get("page") or {}
    count = page.get("count")
    if count is None:
        count = (data.get("data") or {}).get("list", {}).get("count")
    return {
        "total": count if count is not None else 0,
        "num": page.get("num") or 1,
        "size": page.get("size") or PS,
    }


def _fetch_wbi_page(pn: int, ps: int):
    """WBI 签名的投稿列表接口拉取一页（原仓库方式），失败返回 ([], None)。"""
    try:
        from curl_cffi import requests as curl_requests
        s = curl_requests.Session(impersonate="chrome124")
        for url in ("https://www.bilibili.com/",
                    f"https://space.bilibili.com/{HOST_MID}/",
                    "https://api.bilibili.com/x/frontend/finger/spi"):
            try:
                s.get(url, timeout=30)
                time.sleep(0.5)
            except Exception:
                pass
        img, sub = get_wbi_keys()
        for attempt in range(1, RETRIES + 1):
            params = sign({"mid": HOST_MID, "pn": pn, "ps": ps}, img, sub)
            url = VIDEO_API + "?" + urllib.parse.urlencode(
                sorted(params.items(), key=lambda x: x[0]))
            try:
                r = s.get(url, timeout=30)
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                data = r.json()
                if data.get("code") == 0:
                    items, info = _parse_video_items(data), _parse_page_info(data)
                    print(f"[视频] WBI接口 第{pn}页 成功获取 {len(items)} 条", flush=True)
                    return items, info
                print(f"[视频] WBI接口 尝试 {attempt}/{RETRIES}: code={data.get('code')} {data.get('message')}", flush=True)
            except Exception as e:
                print(f"[视频] WBI接口 尝试 {attempt}/{RETRIES}: {e}", flush=True)
            time.sleep(RETRY_SLEEP)
        print("[视频] WBI接口 所有重试失败", flush=True)
        return [], None
    except ImportError:
        pass

    print("[视频] WBI接口 使用urllib模式...", flush=True)
    img, sub = get_wbi_keys()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for attempt in range(1, RETRIES + 1):
        params = sign({"mid": HOST_MID, "pn": pn, "ps": ps}, img, sub)
        url = VIDEO_API + "?" + urllib.parse.urlencode(
            sorted(params.items(), key=lambda x: x[0]))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://space.bilibili.com/{HOST_MID}/",
        })
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            if data.get("code") == 0:
                items, info = _parse_video_items(data), _parse_page_info(data)
                print(f"[视频] WBI接口 第{pn}页 成功获取 {len(items)} 条", flush=True)
                return items, info
            print(f"[视频] WBI接口 尝试 {attempt}/{RETRIES}: code={data.get('code')} {data.get('message')}", flush=True)
        except Exception as e:
            print(f"[视频] WBI接口 尝试 {attempt}/{RETRIES}: {e}", flush=True)
        time.sleep(RETRY_SLEEP)
    print("[视频] WBI接口 所有重试失败", flush=True)
    return [], None


def fetch_video_page(pn: int = 1, ps: int = KEYWORDS_PS):
    """拉取 UP 主投稿视频列表指定页，返回 (items, page_info)；失败返回 ([], None)。

    优先 recArchivesByKeywords（无需签名），失败时回退 WBI 签名接口。
    """
    items, info = _fetch_keywords_page(pn, ps)
    if info is not None:
        return items, info
    print("[视频] 回退到 WBI 签名接口...", flush=True)
    return _fetch_wbi_page(pn, ps if ps <= PS else PS)


def fetch_videos(stop_ids=None, full: bool = False, ps: int = KEYWORDS_PS,
                 max_pages: int = 100):
    """按分页策略拉取全部视频，返回 (items, complete)。

    依据接口返回的 total 判断是否拉完；增量模式下整页 BV号 均已知时提前停止。
    """
    items = []
    pn = 1
    complete = False
    while pn <= max_pages:
        page_items, info = fetch_video_page(pn=pn, ps=ps)
        if info is None:
            break  # 请求失败
        items.extend(page_items)
        total = info.get("total") or 0
        if total and len(items) >= total:
            complete = True
            break
        if not page_items:
            complete = True  # 空页视为到头
            break
        if stop_ids is not None and all(
                (i.get("bvid") or "") in stop_ids for i in page_items):
            print(f"[视频] 第{pn}页全部已知，增量提前结束", flush=True)
            break
        pn += 1
        time.sleep(PAGE_SLEEP)
    return items, complete
