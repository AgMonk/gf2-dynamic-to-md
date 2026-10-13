#!/usr/bin/env python3
"""内容解析模块（识别逻辑复刻自 gitee.com/AgMonk/gf2-dynamic-to-tencentdoc）

- 动态：从 content 解析 头信息 / 正文 / 标签 / 动态类型（原 save_dynamic.py 规则）
  1. 头信息：content 中第一个 "\\n\\n" 之前的部分；正文移除头信息；
  2. 正文：删除末尾两行【全 '=' 行】【※《少女前线2：追放》游戏下载：网页链接】（若按序存在）；
  3. 标签：头信息中所有 #...# 之间 strip 后非空的字符串，保序去重，
     并从每个标签中删除关键词 "少前2追放"、"少前2"，删除后为空的标签被丢弃；
  4. 动态类型：头信息中两个 ✦...✦ 之间的字符串；若含 "·" 或 "|"，
     取首个分隔符之前的部分并清除空格。
- 专栏：标题取 content_preview（接口侧 content 为空时已回退 title）。
- 视频：按标题关键字识别类型（原 save_video.py 规则）。
"""
import html
import re

# 从标签中删除的关键词（顺序敏感："少前2追放" 先于 "少前2"）
TAGS_TO_STRIP = ("少前2追放", "少前2")

# 视频「类型」字段：标题关键字 -> 类型（按顺序匹配，命中即取第一条）
VIDEO_TYPE_RULES = [
    ("北兰岛", "北兰岛"),
    ("角色PV", "角色PV"),
    ("介绍PV", "活动PV"),
    ("外观上新", "外观上新"),
    ("前瞻特别节目录像", "直播录像"),
]


def normalize_link(jump_url: str) -> str:
    """jump_url 按前缀补全为完整 https 地址。"""
    jump = (jump_url or "").strip()
    if not jump:
        return ""
    if jump.startswith("//"):
        return "https:" + jump
    if jump.startswith("/"):
        return "https://www.bilibili.com" + jump
    if jump.startswith("http://") or jump.startswith("https://"):
        return jump
    return "https:" + jump


def split_head_body(content: str) -> tuple:
    """返回 (头信息, 正文)。头信息取第一个 "\\n\\n" 之前的部分；
    存在 "\\n\\n" 时正文为其之后的内容（去掉前导换行），否则正文为原文。"""
    idx = content.find("\n\n")
    if idx == -1:
        return content, content
    return content[:idx], content[idx + 2:].lstrip("\n")


def strip_trailing_footer(text: str) -> str:
    """若正文末尾两行按序为【全 '=' 行】【※《少女前线2：追放》游戏下载：网页链接】，
    则一并删除；否则原样返回。"""
    t = text.rstrip("\n")
    lines = t.split("\n")
    if len(lines) >= 2:
        last = lines[-1]
        prev = lines[-2]
        if last == "※《少女前线2：追放》游戏下载：网页链接" and prev.strip() \
                and all(c == "=" for c in prev.strip()):
            lines = lines[:-2]
            t = "\n".join(lines).rstrip("\n")
    return t if t else text


def extract_tags(head: str) -> list:
    """头信息 → 标签列表（删除 少前2追放/少前2、丢弃空标签、保序去重）。"""
    tags = []
    seen = set()
    for m in re.finditer(r"#([^#]*)#", head):
        s = m.group(1).strip()
        for kw in TAGS_TO_STRIP:
            s = s.replace(kw, "")
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            tags.append(s)
    return tags


def extract_dynamic_type(head: str) -> str:
    """头信息 → 动态类型（两个 ✦ 之间；含 · 或 | 时取首个分隔符前并清除空格）。"""
    m = re.search(r"✦(.+?)✦", head)
    if not m:
        return ""
    t = m.group(1)
    for sep in ("\u00b7", "|"):
        if sep in t:
            t = t.split(sep, 1)[0]
            break
    return t.strip()


def parse_dynamic_item(item: dict) -> dict:
    """把 opus feed(type=dynamic) 的条目解析为 md 行数据。"""
    content = item.get("content_preview", "") or ""
    head, body = split_head_body(content)
    body = strip_trailing_footer(body)
    return {
        "opus_id": str(item.get("opus_id", "") or ""),
        "正文": body,
        "链接": normalize_link(item.get("jump_url", "")),
        "标签": extract_tags(head),
        "动态类型": extract_dynamic_type(head),
    }


def parse_article_item(item: dict) -> dict:
    """把 opus feed(type=article) 的条目解析为 md 行数据。"""
    return {
        "opus_id": str(item.get("opus_id", "") or ""),
        "标题": item.get("content_preview", "") or item.get("content", "") or "",
        "链接": normalize_link(item.get("jump_url", "")),
    }


def classify_video_type(title: str) -> str:
    """依据标题关键字匹配视频类型，未命中返回空串。"""
    if not title:
        return ""
    for keyword, typ in VIDEO_TYPE_RULES:
        if keyword in title:
            return typ
    return ""


def parse_video_item(item: dict) -> dict:
    """把投稿视频列表的条目解析为 md 行数据（created 为秒级时间戳）。"""
    bvid = item.get("bvid") or item.get("id") or ""
    title = html.unescape(item.get("title", "") or "")
    return {
        "BV号": str(bvid),
        "标题": title,
        "created": item.get("created"),
        "视频链接": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "类型": classify_video_type(title),
    }
