# Schema 设计

目标：
- SQLite 作为主数据源
- Markdown 和 JSON 作为导出产物
- 模型语义保持与当前页面导出格式一致：`Meta: N 天 +`

---

# daily_entries（每天的主记录）

```sql
CREATE TABLE daily_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    meta_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

字段 | 意义
--- | ---
id | 内部主键
entry_date | 日期，例如 `2026-04-05`
content | 当天正文内容
char_count | 当天字符数
meta_notes | 当前导出文本里 `---` 之后的整段 notes
created_at | 创建时间
updated_at | 更新时间

说明：
- `content` 只存正文，不强制保存整段导出文本
- `meta_notes` 属于“当天这篇 entry”，不属于某个单独 meta
- 如果后续需要保留完整原始导出文本，再额外加 `raw_text` 字段即可

---

# metas（meta 定义表）

```sql
CREATE TABLE metas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    category TEXT,
    unit TEXT NOT NULL DEFAULT '天',
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

字段 | 意义
--- | ---
id | 内部主键
meta_key | 内部 key，例如 `reading`
label | 前端显示名称，例如 `阅读`
category | 分类，例如 `study` / `exercise`
unit | 单位，默认 `天`
enabled | 是否启用
sort_order | 前端排序
created_at | 创建时间

说明：
- `meta_key` 用于数据库和接口内部引用
- `label` 才是页面展示和导出 Markdown/JSON 时显示的名字
- 当前项目不复杂，`category` 先保留为可选字段即可

示例数据：

meta_key | label | category
--- | --- | ---
english | 英语 | study
program | Program | study
sport | 运动 | exercise

---

# daily_meta_status（每日 meta 状态）

```sql
CREATE TABLE daily_meta_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    meta_key TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entry_date, meta_key),
    FOREIGN KEY (entry_date) REFERENCES daily_entries(entry_date),
    FOREIGN KEY (meta_key) REFERENCES metas(meta_key)
);
```

字段 | 意义
--- | ---
id | 内部主键
entry_date | 日期
meta_key | meta key
count | 截止当天的累计值，例如 `51`
done | 当天是否完成，`0/1`
created_at | 创建时间
updated_at | 更新时间

说明：
- `count` 对应当前页面中的 `N 天`
- `done` 对应当前页面中的 `+`
- `done` 是业务事实，应该直接入库，不建议只在导出 JSON 时临时计算
- 历史数据迁移时，可以按旧规则推断部分 `done`

为什么 `(entry_date, meta_key)` 要唯一：
- 某一天的某个 meta 只能有一条状态记录

示例：

entry_date | meta_key | count | done
--- | --- | --- | ---
2026-04-05 | english | 111 | 1
2026-04-05 | sport | 8 | 0
2026-04-05 | program | 18 | 1

---

# 数据对应关系

当前导出文本：

```text
2026-04-05
今天的正文……
英语: 111 天 +
Program: 18 天 +
运动: 8 天
---
nofap：14.5 -> 15.4 -> 16.3
```

入库后建议对应为：
- `daily_entries.entry_date = 2026-04-05`
- `daily_entries.content = 今天的正文……`
- `daily_entries.meta_notes = nofap：14.5 -> 15.4 -> 16.3`
- `daily_meta_status(english).count = 111, done = 1`
- `daily_meta_status(program).count = 18, done = 1`
- `daily_meta_status(sport).count = 8, done = 0`
