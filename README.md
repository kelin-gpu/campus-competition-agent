# campus-competition-agent

本校竞赛与活动信息服务 AI 助手项目。当前阶段先建设竞赛数据底座，后续再接入扣子（Coze）智能体、数据库和知识库，用于查询、推荐和 DDL 提醒。

## 当前模块

已实现赛氪热门竞赛字段抓取模块：

- 抓取来源：`https://www.saikr.com/index/hot/contest`
- 输出文件：`dataset/saikr_hot_contests_top50.xlsx`
- 输出形式：只保留一个 Sheet，名称为 `原始数据库`
- 数据原则：先抓热门页竞赛入口，再进入每个竞赛详情页二次抓取字段和详情页可见全文；不保存网页 HTML 文件，不做 AI 摘要、推荐判断、政策适配或日期标准化

如果赛氪桌面热门页不足 50 个竞赛详情链接，脚本会继续尝试移动端热门页补齐。不会把 `/u/...` 用户或机构主页当作竞赛补入。部分桌面详情页会返回“赛氪 - 全国大学生竞赛活动平台”通用壳页面，脚本会检测这类结果并自动尝试移动端详情页兜底。

## 仓库结构

```text
.
├── README.md
├── .gitignore
├── dataset/
│   └── saikr_hot_contests_top50.xlsx
├── scripts/
│   ├── crawl_saikr_hot_contests.py
│   └── build_saikr_hot_contests_xlsx.mjs
├── 竞赛数据/
│   └── 2025年教育部认可的全国大学生学科竞赛目录清单.pdf
└── 竞品分析与执行技术文档/
    ├── 本校竞赛与活动信息服务AI助手_竞品分析报告.docx
    ├── 本校竞赛与活动信息服务AI助手_可执行技术文档.docx
    └── 赛事安排.docx
```

## 运行方式

在 Codex 环境中建议使用：

```powershell
uv run python scripts\crawl_saikr_hot_contests.py --output dataset\saikr_hot_contests_top50.xlsx
```

可选参数：

```powershell
uv run python scripts\crawl_saikr_hot_contests.py --limit 20 --sleep 1 --output dataset\saikr_hot_contests_top20.xlsx
```

参数说明：

- `--url`：主要热门竞赛列表页，默认是赛氪桌面热门页。
- `--limit`：最多抓取数量，范围 1-50。
- `--sleep`：访问详情页之间的等待秒数，默认 0.6。
- `--output`：Excel 输出路径。

## 代码运行流程

当前模块由用户手动触发，不会自动定时运行。

1. 用户执行 `uv run python scripts\crawl_saikr_hot_contests.py ...`。
2. Python 请求赛氪热门竞赛列表页。
3. 脚本机械提取竞赛标题和详情页链接。
4. 如果桌面页不足 50 条，脚本尝试移动端热门页补齐。
5. 脚本逐条访问竞赛详情页，从详情页 HTML 和 meta 标签中机械提取核心字段，并把详情页全部可见文本写入 `detail_text`。
6. Python 把字段记录写入临时 JSON。
7. Node builder 生成单 Sheet Excel。
8. Excel 写入 `dataset/saikr_hot_contests_top50.xlsx`。

## Excel 字段

`原始数据库` Sheet 包含以下列：

| 字段 | 含义 |
| --- | --- |
| `rank` | 热门榜顺序。 |
| `title` | 竞赛标题，优先详情页标题，兜底列表页标题。 |
| `detail_url` | 竞赛详情页链接。 |
| `organizer` | 主办方、主办单位或组织单位。 |
| `category` | 竞赛类别。 |
| `registration_time` | 报名时间、报名截止或参赛报名时间。 |
| `contest_time` | 竞赛时间、比赛时间、活动时间或作品提交时间。 |
| `participant_scope` | 参赛对象、参赛资格或面向人群。 |
| `fee_or_status` | 报名费、参赛费用、费用或报名状态。 |
| `summary` | 详情页简介，优先从详情页 meta description 或可见简介文本中提取。 |
| `detail_text` | 详情页全部可见文本，来自二次进入 `detail_url` 后的页面，不包含 HTML 标签、CSS、JS 或隐藏脚本。 |
| `source_url` | 来源热门页。 |
| `fetched_at` | 本次详情页抓取时间。 |
| `http_status` | 详情页请求状态，成功时通常为 `200`。 |

## 网络环境说明

在 Codex 受管环境中，普通 bundled Python 直接联网可能失败，典型错误是：

```text
PermissionError: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。
```

这不是赛氪网站不可用。此前已验证同一 URL 通过 `uv run` 执行路径可以返回 `HTTP 200`。因此在 Codex 里运行抓取时优先使用 `uv run python ...`。

## 后续方向

1. 对字段抽取结果做人工抽样核对。
2. 需要更多字段时，在详情页机械抽取规则中扩展。
3. 增加教育部/高教学会竞赛目录 PDF 抓取或解析模块。
4. 增加学校、学院、书院官网通知抓取模块。
5. 将稳定后的数据导入扣子数据库和知识库。
