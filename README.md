# Campus Competition Agent

面向南京大学学生的竞赛与校园活动智能体。项目把多来源采集、赛事目录与届次建模、字段级证据、截止日期提醒、用户画像和对话查询组织在同一套服务中，并坚持“缺失信息不猜测、每条结果可追溯、过期数据不物理删除”的数据原则。

## 核心能力

| 模块 | 能力 | 当前行为 |
| --- | --- | --- |
| 对话 Agent | 竞赛列表、详情、DDL 提醒、通知解析预览、联网补充、个性化推荐 | 普通对话只暴露安全查询与当前身份画像工具 |
| 赛氪采集 | 抓取热门竞赛列表和详情字段，导出 Excel | 过滤保研/培训推广，同时按规范 URL 和标题去重 |
| 黑客松采集 | Devfolio、MLH、HackClub、Devpost 和通用搜索 | 全来源发现后统一去重、限额、时间筛选；dry-run 输出完整通过记录 |
| 微信公众号 | 维护南京大学相关公众号、搜索文章、抽取校内活动 | 内置 55 个核心账号并支持搜狗微信动态发现 |
| 数据同步 | 全量、增量、微信公众号和黑客松同步 | 截止活动保留并标记为 `已截止`，不执行物理删除 |
| 数据治理 | 稳定目录、赛事届次、字段级证据、跨来源校验 | 合并操作幂等，来源证据去重，时间线统一规范化 |

## 技术栈

- Python 3.12、FastAPI
- LangGraph、LangChain、OpenAI-compatible Chat API
- PostgreSQL / Supabase、SQLAlchemy
- Coze Workload Identity、Coze Knowledge Base
- pytest、uv

## 项目结构

```text
config/                 Agent 与模型配置
assets/data/            数据源配置和本地验证产物
docs/                   数据模型迁移说明
scripts/                服务启动、爬取、导入与维护脚本
src/agents/             LangGraph Agent
src/storage/            PostgreSQL、Supabase、MemorySaver、S3 适配
src/tools/              查询、同步、采集、解析和用户画像工具
tests/                  离线契约、生命周期、隔离和可选集成测试
```

## 安装与配置

```powershell
uv sync --dev
$env:COZE_WORKSPACE_PATH = (Get-Location).Path
```

按使用的能力配置环境变量：

| 环境变量 | 用途 | 未配置时 |
| --- | --- | --- |
| `COZE_WORKSPACE_PATH` | 定位配置、缓存和项目资源 | 本地运行应显式设置 |
| `COZE_WORKLOAD_IDENTITY_API_KEY` | 模型调用、Devpost 与通用网页搜索 | 相关能力关闭；平台直连采集仍可运行 |
| `COZE_INTEGRATION_MODEL_BASE_URL` | OpenAI-compatible 模型地址 | 无法真实调用 Agent 模型 |
| `PGDATABASE_URL` | PostgreSQL 目录、届次、画像和同步 | Agent 检查点退化为内存；无法真实入库 |
| `COZE_SUPABASE_URL` | Supabase 查询地址 | Supabase 查询不可用 |
| `COZE_SUPABASE_ANON_KEY` | Supabase 匿名访问密钥 | Supabase 查询不可用 |

## 启动 HTTP 服务

Windows PowerShell：

```powershell
$env:COZE_WORKSPACE_PATH = (Get-Location).Path
uv run python src/main.py -m http -p 5000
```

Linux / macOS：

```bash
bash scripts/http_run.sh -m http -p 5000
```

主要接口：

- `GET /health`：服务健康检查。
- `POST /run`、`POST /async_run`、`POST /stream_run`：Agent 执行接口。
- `GET /task/{task_id}`：异步任务状态。
- `POST /cancel/{run_id}`：取消运行。
- `POST /v1/chat/completions`：OpenAI Chat Completions 兼容入口。
- `GET /graph_parameter`：图输入输出参数定义。

## Agent 安全边界

对话 Agent 当前暴露：

- `query_events`、`query_event_detail`、`get_deadline_reminders`
- `parse_notification`、`web_search_events`
- `get_user_profile`、`update_user_profile`
- `add_focus_contest`、`remove_focus_contest`
- `get_personalized_recommendations`

全量同步、数据库清理和公共赛事写入工具不暴露给普通对话。`parse_notification` 只生成预览；用户画像 ID 只能从 Coze 运行上下文取得，不能由模型参数或用户输入指定。

## 数据采集

### 赛氪热门竞赛

```powershell
uv run python scripts/crawl_saikr_hot_contests.py `
  --limit 50 `
  --output assets/data/saikr_crawled_validation.xlsx
```

脚本会访问桌面端和移动端公开热门页，抓取详情字段并输出 Excel。`--limit` 是上限；过滤推广并去重后，实际条数可能小于该值。输出字段包括标题、详情 URL、主办方、报名时间、竞赛时间、参赛对象、费用/状态、摘要、正文、抓取时间和 HTTP 状态。

### 黑客松

建议先使用不会写数据库的 dry-run：

```powershell
uv run python scripts/search_hackathons.py `
  --dry-run `
  --source all `
  --limit 60 `
  --save-report assets/data/hackathon_dry_run_validation.json
```

处理流程：

1. 各平台独立发现活动，单一高产来源不会再被平均限额压缩。
2. 列表页共享 URL 的不同活动按平台、标题和届次保留。
3. MLH 直接解析 schema.org 活动卡片中的真实 URL 和 ISO 起止时间。
4. 全来源去重后按来源轮询应用全局限额。
5. 过滤已结束、关闭、日期冲突、超远期和无法验证的候选。
6. dry-run 报告在 `accepted_records` 中保存全部通过记录，同时保留前 20 条 `accepted_samples` 便于终端预览。

Devpost 和通用搜索依赖 `COZE_WORKLOAD_IDENTITY_API_KEY`；未配置时会记录一次明确警告，不影响 MLH、HackClub 和 Devfolio 直连采集。

### 微信公众号

微信公众号模块维护校级、院系和动态发现三类来源，可按时间窗口抓取文章并转换为标准事件结构。默认增量窗口为 6 小时。搜狗微信可能返回限流或验证码，因此动态发现为尽力而为；核心账号列表始终保留。

## 数据模型与生命周期

- `competition_catalog`：稳定竞赛目录，例如赛事名称、主办方和类别。
- `event_edition`：某一届赛事的报名截止、比赛时间、状态和来源。
- `field_evidence`：字段级来源 URL、证据文本、置信度和校验信息。

同步以规范标题、届次和来源构造稳定身份；重复运行更新已有届次而不是重复新增。报名截止已过的记录标记为 `已截止`。迁移步骤见 [docs/MIGRATION_v2.md](docs/MIGRATION_v2.md) 和 [docs/migrations/001_catalog_v2.sql](docs/migrations/001_catalog_v2.sql)。

## 定时同步

默认任务：

- 每天 02:00 执行全量同步。
- 每 6 小时执行微信公众号增量同步。
- 每 12 小时执行黑客松同步。

调度器启动是幂等的；重复调用不会重复注册同一组任务。

## 验证

本地验证命令：

```powershell
uv sync --dev
uv lock --check
uv run python -m compileall -q src tests
uv run pytest -q
```

2026-07-21 的真实联网验证基线：

| 检查项 | 结果 |
| --- | --- |
| 自动化测试 | `109 passed, 2 skipped`；跳过项为需要显式凭据的 PostgreSQL/Supabase 集成测试 |
| HTTP 健康检查 | `200`，`status=ok` |
| Agent 构建 | 成功生成 `model`、`tools` 状态图节点 |
| 赛氪 | 公开页有效竞赛 47 条；标题和详情 URL 均唯一；必填字段无空值；推广项 0；HTTP 200 共 47 条 |
| 黑客松 | 发现 122 条；全局限额 60；通过 32 条；完整记录 32 条；空标题/空 URL/空时间均为 0；来源错误 0 |
| 微信公众号 | 55 个核心账号；单页文章搜索返回 9 条；动态账号发现当次返回 0 条 |

真实 PostgreSQL 与 Supabase 集成测试默认跳过。需要使用独立测试库并显式设置 `RUN_SUPABASE_INTEGRATION_TESTS=1`、`TEST_PGDATABASE_URL` 或 `TEST_COZE_SUPABASE_*` 后运行，避免误写生产数据。

## 当前限制

- 未配置模型、PostgreSQL 和 Supabase 凭据时，无法验证真实模型回复、数据库写入及远端画像持久化；本地仍可验证 Agent 图构建、规则、采集和 dry-run。
- 黑客松 `--limit` 是全局处理上限，`truncated_by_limit` 表示因限额未进入详情验证的候选，不等同于重复或抓取失败。
- 搜狗微信和部分第三方活动站点可能限流、启用验证码或返回 JS 页面，采集器会跳过无法验证的数据，不会猜测缺失字段。
