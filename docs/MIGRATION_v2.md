# 数据模型 v2 迁移说明

PostgreSQL 建表和索引脚本位于 `docs/migrations/001_catalog_v2.sql`。执行前请备份数据库；脚本不会覆盖已经存在的 `event_info` 关系。

## 变更原因

旧模型使用单表 `event_info` 同时存储：
1. 教育部竞赛目录（稳定信息，不是具体届次）
2. 赛氪/公众号抓取的具体届次赛事（动态信息）

导致 82 条教育部目录记录被错误地标记为"报名中"，严重误导用户。

## 新模型

拆分为三张核心表：

### competition_catalog — 竞赛目录

稳定属性：
- `normalized_title`：归一化名称，用于去重
- `original_title`：原始标题
- `organizer`：主办方
- `category`、`contest_level`、`authority_level`、`policy_tags`
- `scope_type`：校外竞赛/校内竞赛/校内活动
- `is_ministry_approved`：是否教育部认可目录
- `source_name`、`source_url`

### event_edition — 具体届次

动态属性：
- `catalog_id`：关联目录
- `title`：届次标题
- `edition_year`：年份
- `signup_deadline`：报名截止
- `event_time`：比赛时间
- `status`：报名中/即将截止/已截止/暂无本届信息/待确认
- `summary`、`tags`、`target_major`、`target_grade`
- `extraction_method`、`confidence`、`verification_status`

### field_evidence — 字段证据

每条字段值的来源：
- `edition_id`、`field_name`、`field_value`
- `source_url`、`fetched_at`
- `extraction_method`、`confidence`、`verification_status`

## 视图兼容

保留 `event_info` 作为视图，向后兼容旧查询：

```sql
CREATE OR REPLACE VIEW event_info AS
SELECT
    e.edition_id AS event_id,
    e.title,
    c.scope_type,
    c.category,
    e.summary,
    e.signup_deadline,
    e.event_time,
    e.target_major,
    e.target_grade,
    c.contest_level,
    e.tags,
    e.policy_tags,
    e.source_name,
    e.source_url,
    c.authority_level,
    e.status,
    c.organizer,
    e.created_at AS update_time
FROM event_edition e
JOIN competition_catalog c ON e.catalog_id = c.catalog_id;
```

## 数据迁移结果

- 旧 `event_info`：135 条
- 新 `competition_catalog`：131 条
- 新 `event_edition`：54 条（仅含具体届次/活动）
- 新 `field_evidence`：168 条
- 视图 `event_info`：54 条

## 状态语义

| 状态 | 含义 |
|------|------|
| 报名中 | 有未过期报名截止时间 |
| 即将截止 | 3 天内截止 |
| 已截止 | 报名截止时间已过 |
| 暂无本届信息 | 目录认可但无本届通知/DDL |
| 待确认 | 有数据但无法判断状态 |

## 后续待完成

1. event_enrichment.py 写入 evidence 记录
2. 批量 upsert 改造
3. 主动提醒表设计
4. 推荐算法硬过滤改造
5. main.py 拆分
