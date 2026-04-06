# 系统架构

## 目标

在保留现有本地网页写作体验的前提下，引入 SQLite 作为主数据源，并继续产出 Markdown 与 JSON 兼容现有流程。

---

## 组件

### Frontend

- `daily_pad_with_meta_notes.html`

职责：
- 提供每日记录编辑界面
- 展示 meta 列表、累计值和当天完成状态
- 生成当前兼容格式的导出文本
- 调用后端接口提交和读取数据

### Backend

- Flask
- `agent.py`

职责：
- 提供 HTTP 接口
- 校验和解析前端提交的数据
- 写入 SQLite
- 导出 Markdown
- 更新 `daily_meta_map.json` 和 `daily_char_map.json`

### Storage

- SQLite：主数据源
- `daily_logs/*.md`：可读归档
- `daily_meta_map.json` / `daily_char_map.json`：兼容 heatmap 的导出文件

---

## 数据源策略

以后的职责划分：

- SQLite 负责存储真实业务数据
- Markdown 负责保留人类可读归档
- JSON 负责提供给现有统计展示页使用

这意味着：
- 新增、修改、查询都应以 SQLite 为准
- Markdown 和 JSON 不再作为主事实来源
- 历史数据迁移阶段可以继续读取旧 Markdown

---

## 核心数据流

当前推荐链路：

`html -> POST /save -> agent.py -> SQLite -> 导出 Markdown / JSON`

展开后：

```text
daily_pad_with_meta_notes.html
        │
        ▼
    POST /save
        │
        ▼
      agent.py
        │
        ├─ 解析 entry_date / content / meta_notes
        ├─ 解析 meta count / done
        ├─ 写 daily_entries
        ├─ 写 daily_meta_status
        ├─ 导出 daily_logs/YYYY/MM/DD.md
        └─ 更新 daily_meta_map.json / daily_char_map.json
```

---

## 读取链路

为了让前端后续脱离 `localStorage`，后端应逐步补齐读取接口：

- `GET /entry?date=YYYY-MM-DD`
- `GET /metas`

推荐读取流程：

```text
html -> GET /entry -> agent.py -> SQLite
html -> GET /metas -> agent.py -> SQLite
```

---

## 迁移原则

- 第一步只是在后端接入 SQLite，不重写前端交互
- 页面现有 `POST /save` 先保持兼容
- 历史数据优先从 `daily_logs/*.md` 迁移
- heatmap 先继续读 JSON，不急着改为直连接口

---

## 当前边界

本项目当前不处理：

- 多用户
- 登录和权限系统
- 远程数据库
- 复杂实时协作
