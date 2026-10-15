# 追放B站动态自动抓取

自动抓取 B站「[少女前线2：追放](https://space.bilibili.com/697654195)」官方账号（UID：697654195）发布的动态、专栏与视频，以 Markdown 表格形式存档于本仓库，由 GitHub Actions 定时增量更新。

## 数据表

| 文件 | 内容 | 列 | 更新频率 |
| --- | --- | --- | --- |
| [data/动态.md](data/动态.md) | 图文动态（opus feed） | opus_id / 正文 / 链接 / 标签 / 动态类型 / 登记时间 | 每天 |
| [data/专栏.md](data/专栏.md) | 专栏文章（opus feed） | opus_id / 标题 / 链接 / 登记时间 | 每周三 |
| [data/视频.md](data/视频.md) | 投稿视频（投稿列表接口，WBI 签名） | BV号 / 标题 / 发布时间 / 视频链接 / 类型 / 登记时间 | 每天 |
| [data/专栏详情/](data/专栏详情) | 专栏正文全文详情（每个专栏一个 md 文件） | — | 随专栏更新 |

- 表内按发布先后倒序（最新在前）；「登记时间」为记录首次写入的北京时间。
- 正文/标题/标签为纯文本展示：对 `~`、`*`、`` ` ``、`<` 等会被 Markdown/HTML 渲染的符号做了转义，避免原文被渲染成删除线、强调或被当作 HTML 标签吞掉。

### 专栏详情

`data/专栏详情/` 下为每篇专栏的全文存档，文件名 `{发布时间YYYYMMDD_HHMM}_{标题}.md`，内容依次为：

1. 横幅图（页头 banner，无则省略）
2. 摘要（取接口分享摘要，超 120 字截断）+ 发布时间 / UP 主 / 原文链接
3. 正文全文（图片按原文位置以 `![](url)` 穿插，段内换行以 md 硬换行保留）

取数策略：优先 `opus/detail` 接口（无需登录/签名）；老专栏接口返回空时自动回退到 opus 页面内嵌的 `__INITIAL_STATE__` 数据（结构同接口），两路共用一套解析。专栏表每次更新后自动对照表格补抓缺失详情，无缺失时仅扫描本地文件、不发起网络请求。

## 更新机制

| Workflow | 计划 | 说明 |
| --- | --- | --- |
| [update-daily.yml](.github/workflows/update-daily.yml) | 北京时间每天 0:47 | 增量更新 动态 + 视频 |
| [update-article.yml](.github/workflows/update-article.yml) | 北京时间每周三 1:31 | 增量更新 专栏 |

两个 workflow 均支持 `workflow_dispatch` 手动触发；定时已错开整点，减少 GitHub 调度延迟。有数据变更时以 `github-actions[bot]` 自动提交推送，无变更则跳过提交。

### 增量规则

- 以 `opus_id` / `BV号` 为唯一标识去重：**已存在的记录永不修改**（含登记时间），只有新增记录才写入；
- 既有数据行在文件中字节级原样保留，即使接口返回内容发生变化也不会改写历史记录；
- 本次抓取无新增时不写盘，不产生提交；
- 抓取失败时跳过写盘，避免污染数据。

## 本地运行

```bash
pip install -r requirements.txt

# 增量更新动态与视频
python scripts/update.py dynamic video

# 增量更新专栏
python scripts/update.py article

# 全量模式：拉到接口末尾后补全所有缺失记录（既有记录仍保持原样）
python scripts/update.py dynamic article video --full

# 手动补抓专栏详情（读 data/专栏.md，自动跳过已有详情的记录）
python scripts/fetch_article_detail.py --from-table

# 抓取指定专栏详情
python scripts/fetch_article_detail.py 1258271941414879250
```

## 目录结构

```
├── data/                     # 数据表（动态 / 专栏 / 视频）+ 专栏详情全文（data/专栏详情/）
├── scripts/
│   ├── update.py             # 更新入口：抓取 + 增量合并 + 写盘（专栏更新后自动补抓详情）
│   ├── fetch_opus.py         # opus feed 拉取（动态/专栏）
│   ├── fetch_article_detail.py  # 专栏详情全文抓取（opus detail 接口 + 网页兜底）
│   ├── fetch_video.py        # 投稿视频列表拉取（WBI 签名）
│   ├── parsers.py            # 内容解析（头信息/正文/标签/类型识别）
│   └── md_store.py           # md 表格读写与转义
└── .github/workflows/        # 定时任务
```
