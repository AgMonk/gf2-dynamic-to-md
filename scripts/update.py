#!/usr/bin/env python3
"""更新入口：抓取 B站数据并增量合并到 data/ 下的 md 表格文件

用法：
  python scripts/update.py dynamic video          # 增量更新动态与视频（默认）
  python scripts/update.py article --full         # 全量重建专栏表
  python scripts/update.py dynamic article video --full

目标表与字段（参考 gitee.com/AgMonk/gf2-dynamic-to-tencentdoc 的 3 张表，
并增加「登记时间」字段 = 记录首次写入 md 的时间，北京时间）：
  动态：opus_id / 正文 / 链接 / 标签 / 动态类型 / 登记时间
  专栏：opus_id / 标题 / 链接 / 登记时间
  视频：BV号 / 标题 / 发布时间 / 视频链接 / 类型 / 登记时间

行为：
  - 增量模式按 opus_id / BV号 去重，已有记录保持原样（含登记时间），
    既有行字节级原样回写（不重新序列化、不因接口内容漂移而改写）；
  - 全量模式（--full）拉到接口末尾后整表重建，成功校验（拉完）才写盘；
  - 表内按发布先后倒序（最新在前）。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_article_detail
import fetch_opus
import fetch_video
import md_store
import parsers

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

TZ_CN = timezone(timedelta(hours=8))
HOST_MID = fetch_opus.HOST_MID
SPACE_URL = f"https://space.bilibili.com/{HOST_MID}"

TARGETS = {
    "dynamic": {
        "label": "动态",
        "file": os.path.join(DATA_DIR, "动态.md"),
        "title": "追放B站动态自动抓取 · 动态",
        "columns": ["opus_id", "正文", "链接", "标签", "动态类型", "登记时间"],
        "key": "opus_id",
        "link_label": "打开动态",
        "api_note": "opus feed（type=dynamic）",
    },
    "article": {
        "label": "专栏",
        "file": os.path.join(DATA_DIR, "专栏.md"),
        "title": "追放B站动态自动抓取 · 专栏",
        "columns": ["opus_id", "标题", "链接", "登记时间"],
        "key": "opus_id",
        "link_label": "打开专栏",
        "api_note": "opus feed（type=article）",
    },
    "video": {
        "label": "视频",
        "file": os.path.join(DATA_DIR, "视频.md"),
        "title": "追放B站动态自动抓取 · 视频",
        "columns": ["BV号", "标题", "发布时间", "视频链接", "类型", "登记时间"],
        "key": "BV号",
        "link_label": "打开视频",
        "api_note": "投稿视频列表（recArchivesByKeywords，失败回退 WBI 签名接口）",
    },
}


def parse_rows(target: str, items: list) -> list:
    """接口条目 → md 行（仅数据列，不含登记时间）。"""
    cfg = TARGETS[target]
    rows = []
    if target == "dynamic":
        for it in items:
            p = parsers.parse_dynamic_item(it)
            rows.append({
                "opus_id": p["opus_id"],
                "正文": p["正文"],
                "链接": md_link(cfg["link_label"], p["链接"]),
                "标签": "、".join(p["标签"]),
                "动态类型": p["动态类型"],
            })
    elif target == "article":
        for it in items:
            p = parsers.parse_article_item(it)
            rows.append({
                "opus_id": p["opus_id"],
                "标题": p["标题"],
                "链接": md_link(cfg["link_label"], p["链接"]),
            })
    else:  # video
        for it in items:
            p = parsers.parse_video_item(it)
            rows.append({
                "BV号": p["BV号"],
                "标题": p["标题"],
                "发布时间": md_store.ts_to_cn(p["created"]) if p.get("created") else "",
                "视频链接": md_link(cfg["link_label"], p["视频链接"]),
                "类型": p["类型"],
            })
    return rows


def md_link(label: str, url: str) -> str:
    return f"[{label}]({url})" if url else ""


def sort_key(target: str, row: dict):
    """排序键：opus 表按 opus_id 数值倒序；视频按发布时间倒序。"""
    if target == "video":
        try:
            return datetime.strptime(row.get("发布时间", ""), "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CN).timestamp()
        except ValueError:
            return 0.0
    try:
        return int(row.get("opus_id", "") or 0)
    except ValueError:
        return 0


def fetch_target(target: str, existing_ids: set, full: bool):
    """按目标抓取条目，返回 (items, complete)。"""
    if target == "video":
        return fetch_video.fetch_videos(stop_ids=None if full else existing_ids, full=full)
    feed_type = target  # dynamic / article
    return fetch_opus.fetch_opus(feed_type, stop_ids=None if full else existing_ids, full=full)


def update_target(target: str, full: bool) -> bool:
    cfg = TARGETS[target]
    path = cfg["file"]
    rows, _, raw_lines = md_store.read_table_raw(path)
    existing_ids = {r.get(cfg["key"], "") for r in rows if r.get(cfg["key"])}
    print(f"== [{cfg['label']}] 已有 {len(rows)} 条，开始抓取（{'全量' if full else '增量'}） ==", flush=True)

    items, complete = fetch_target(target, existing_ids, full)
    if not items and not complete:
        print(f"[{cfg['label']}] 抓取失败，跳过写盘", flush=True)
        return False
    if full and not complete:
        print(f"[{cfg['label']}] 全量拉取未到接口末尾，为避免覆盖完整数据，跳过写盘", flush=True)
        return False

    parsed = parse_rows(target, items)
    # 批内去重 + 与已有记录去重（保持原顺序）
    new_rows = []
    seen = set(existing_ids)
    for r in parsed:
        k = r.get(cfg["key"], "")
        if not k or k in seen:
            continue
        seen.add(k)
        r["登记时间"] = md_store.now_cn()
        new_rows.append(r)

    if not new_rows:
        print(f"[{cfg['label']}] 本次拉取 {len(parsed)} 条，无新增，不写盘", flush=True)
        return True

    # 合并排序：既有行原样保留原始文本（字节级不变），仅新增行序列化。
    # 稳定排序保证同键时既有行相对顺序不变。
    entries = [(sort_key(target, r), raw_lines[i]) for i, r in enumerate(rows)]
    entries += [(sort_key(target, r), md_store.render_row(cfg["columns"], r)) for r in new_rows]
    entries.sort(key=lambda e: e[0], reverse=True)
    merged = rows + new_rows
    lines = [e[1] for e in entries]
    meta = [
        f"数据源：[少女前线2：追放的B站空间]({SPACE_URL})（UID：{HOST_MID}）",
        f"数据接口：{cfg['api_note']}",
        f"最近更新：{md_store.now_cn()}（UTC+8）",
        f"记录数：{len(merged)}",
    ]
    md_store.write_table_lines(path, cfg["title"], meta, cfg["columns"], lines)
    print(f"[{cfg['label']}] 新增 {len(new_rows)} 条，表内共 {len(merged)} 条 → {os.path.relpath(path, BASE_DIR)}", flush=True)
    return True


def main():
    ap = argparse.ArgumentParser(description="B站动态/专栏/视频 → md 表格增量更新")
    ap.add_argument("targets", nargs="+", choices=sorted(TARGETS), help="要更新的目标表")
    ap.add_argument("--full", action="store_true", help="全量拉取（拉到接口末尾）")
    args = ap.parse_args()

    results = {t: update_target(t, args.full) for t in args.targets}
    for t, ok in results.items():
        print(f"结果 {TARGETS[t]['label']}: {'成功' if ok else '失败'}", flush=True)
    # 专栏更新成功后补抓缺失详情（无缺失时仅扫描本地文件，不发起网络请求）
    if results.get("article"):
        try:
            failed = fetch_article_detail.sync_from_table()
            if failed:
                print(f"专栏详情: {failed} 条抓取失败", flush=True)
        except Exception as e:
            print(f"专栏详情补抓异常: {e}", flush=True)
    # 全部失败才以非 0 退出（CI 中据此报警）
    sys.exit(0 if any(results.values()) else 1)


if __name__ == "__main__":
    main()
