# 实施任务拆分

原则：
- 先让 SQLite 接入现有链路
- 先保留 Markdown 和 JSON 兼容输出
- 不一次性同时重写前端、后端、统计页

---

# Phase 1：确定主数据源与表结构

目标：
- 明确 SQLite 是主数据源
- 固定三张表：`daily_entries`、`metas`、`daily_meta_status`

任务：
- 敲定 `notes` 放在 `daily_entries.meta_notes`
- 敲定 `done` 放在 `daily_meta_status.done`
- 统一现有页面导出格式与数据库字段语义

完成标准：
- `schema.md` 可以直接指导建表

---

# Phase 2：初始化数据库

目标：
- 项目启动后可自动创建 SQLite 文件和表

任务：
- 新增数据库初始化模块
- 创建建表 SQL
- 补充基础索引和连接封装

完成标准：
- 本地首次运行 agent 后能得到一个可用的 `.db` 文件

---

# Phase 3：历史数据迁移

目标：
- 把现有 `daily_logs/*.md` 迁入 SQLite

任务：
- 复用已有解析逻辑读取历史 Markdown
- 写入 `daily_entries`
- 写入 `daily_meta_status`
- 必要时写入默认 `metas`
- 对历史数据按既有规则补推断 `done`

完成标准：
- 迁移后抽查数天数据，能和现有 Markdown / JSON 对上

---

# Phase 4：改造 `/save`

目标：
- 新提交的数据不仅写文件，也写入 SQLite

任务：
- 保留当前 `POST /save` 接口
- 保存当天正文、meta notes、meta 状态到 SQLite
- 继续导出 Markdown
- 继续刷新 `daily_meta_map.json` 和 `daily_char_map.json`

完成标准：
- 页面无需改动也能把新数据写入 SQLite

---

# Phase 5：补读取接口

目标：
- 让前端可以从后端回读数据，而不只会提交

任务：
- 增加 `GET /entry?date=YYYY-MM-DD`
- 增加 `GET /metas`
- 约定接口返回结构

完成标准：
- 后端已具备“读当天记录”和“读 meta 定义”的能力

---

# Phase 6：前端逐步脱离 localStorage

目标：
- 让页面以后端数据为准

任务：
- 页面启动时从接口拉取 metas
- 打开某一天时从接口拉取 entry
- `localStorage` 仅保留草稿缓存或临时状态

完成标准：
- 页面刷新后能从后端恢复数据，而不是依赖本地浏览器状态

---

# Phase 7：清理和收尾

目标：
- 减少重复数据源和后续维护成本

任务：
- 评估是否继续保留 `daily_logs` 作为可读归档
- 评估 heatmap 是否继续读 JSON，还是改为走接口
- 补 README 与迁移说明

完成标准：
- 数据源职责清晰
- 新人只看文档就知道系统真实数据流
