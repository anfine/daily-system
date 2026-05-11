# daily-system

一个用于记录、归档日记内容，并生成统计数据的本地小工具集合。包含：
- 日记归档脚本（按日期落盘）
- meta/字符数统计脚本
- 本地 Flask Agent，用于接收网页或其他客户端的日记文本并更新统计

## 功能概览
- 归档：从聊天或输入日志里提取每日内容，写入 `daily_logs/YYYY/MM/DD.md`
- 统计：生成 `daily_meta_map.json` 与 `daily_char_map.json`
- Agent：在本地保存日记、更新统计，并主动同步云端队列

## 目录结构
- `archive_daily.py`：解析日志片段并写入 `daily_logs`
- `build_daily_char_meta_map.py`：遍历 `daily_logs` 生成统计 JSON
- `cloud_api.py`：云端 Flask API，负责管理员登录、服务器队列、Agent last_seen、展示 JSON 上传
- `agent.py`：本地私有 Agent，提供本地接口，并可运行 `cloud-sync-loop` 主动拉取云端队列
- `daily_logs/`：按日期归档的日记内容
- `daily_meta_map.json`：每日 meta 结构化统计
- `daily_char_map.json`：每日字符数统计
- `agent_inbox/`：Agent 暂存文本
- `inbox/`：原始输入文件目录（可选）
- `daily_pad_with_meta_notes.html`：写日记的网页
- `web/site/data/`：云端展示 JSON 目录，供首页 Heatmap 读取

## 安装与运行
### 依赖
- Python 3
- Flask

### 启动 Agent
```bash
python -m venv .venv
.venv\Scripts\pip install flask
.venv\Scripts\python agent.py
```

Agent 默认监听 `http://127.0.0.1:8787`，可用 `GET /ping` 测试。

管理员登录与同步：
- Docker 模式下由 `cloud-api` 负责登录、限速和云端队列。
- 本地或 Docker 启动前设置 `ADMIN_PASSWORD`。
- Docker 可同时设置 `ADMIN_SESSION_SECRET`，用于签名登录 session。
- `POST /auth/login` 已按 IP 限速：1 分钟内失败 5 次后锁定 10 分钟。
- 生产环境可设置 `ALLOWED_ORIGINS` 收紧 CORS，例如 `https://example.com`。
- `AGENT_SYNC_TOKEN` 用于本地 Agent 调用云端 `/agent/sync/*`，必须和 cloud-api 配置一致。
- `CLOUD_API_URL` 是本地 Agent 能访问到的云端地址，例如 `https://example.com/api`。
- 真实云端部署时，云端不主动访问本地 `agent`；本地 `agent` 主动拉取云端队列并上传展示 JSON。

```bash
export ADMIN_PASSWORD="your-password"
export ADMIN_SESSION_SECRET="replace-with-a-long-random-string"
export ALLOWED_ORIGINS="https://example.com"
export AGENT_SYNC_TOKEN="replace-with-another-long-random-string"
docker compose up -d --build
```

本地开发验证：
```bash
docker compose up -d --build
docker compose stop agent
```

此时 Pad 应仍可登录，保存内容会进入云端队列 `server_queue/`。重新启动本地 agent 后，`agent.py cloud-sync-loop` 会主动拉取队列，写入本地 SQLite / Markdown / JSON，再上传 `daily_char_map.json` 和 `daily_meta_map.json` 到 `web/site/data/`。

真实云端同步模型：
- 多端 Pad 写入内容到云端 `cloud-api` 队列。
- 本地 `agent` 主动拉取云端队列。
- `agent` 写入本地 SQLite / Markdown / JSON。
- `agent` 上传最新展示 JSON 回云端，首页 Heatmap 读取云端 JSON。

本地 Agent 手动同步一次：
```bash
export CLOUD_API_URL="https://example.com/api"
export AGENT_SYNC_TOKEN="same-token-as-cloud-api"
python agent.py cloud-sync-once
```

持续同步：
```bash
python agent.py cloud-sync-loop
```

监控：
- 云端 Compose 内置 `prometheus`、`grafana`、`node-exporter`。
- 首页监控区嵌入 Grafana dashboard：`/grafana/d/daily-system-overview/daily-system-overview?orgId=1&kiosk&theme=dark`。
- Grafana 通过 nginx 子路径 `/grafana/` 访问，默认允许匿名只读和 iframe 嵌入。
- `cloud_api.py` 暴露 `/metrics`，Prometheus 会抓取队列数、agent 在线状态、last_seen 延迟和 meta 快照状态。
- `web` 端口绑定为 `127.0.0.1:8080:80`，生产环境建议只由服务器反代访问，不直接开放 8080。

启动云端网站和监控：
```bash
docker compose up -d --build web cloud-api prometheus grafana node-exporter
```

## 使用说明
### 1) 归档（archive_daily.py）
`archive_daily.py` 用于从聊天日志里按天切分，并落盘到 `daily_logs/YYYY/MM/DD.md`。
脚本里有示例路径变量 `path`，使用前请改成自己的输入文件路径。

### 2) 生成统计（build_daily_char_meta_map.py）
会遍历 `daily_logs` 下的 md 文件，输出：
- `daily_char_map.json`：去掉空白后的字符数
- `daily_meta_map.json`：meta 行与 notes

Meta 行格式示例：
```
睡眠: 51 天 +
运动: 20 天
---
这里开始是 notes
```
说明：
- 从第 3 行开始解析 meta
- 遇到 `---` 后面全部作为 notes
- `+` 表示当天完成

运行：
```bash
python build_daily_char_meta_map.py
```

### 3) Agent 接口（agent.py）

云端接口（cloud_api.py）：
- `POST /auth/login`：提交管理员密码，成功后写入 session cookie
- `POST /auth/logout`：退出登录
- `GET /auth/me`：检查当前登录状态
- `POST /save`：把完整文本写入云端待同步队列
- `GET /queue`：列出云端待同步队列
- `GET /queue/<id>`：读取队列项全文
- `DELETE /queue/<id>`：删除队列项
- `GET /agent/status`：查看本地 Agent 最近 check-in 和队列数量
- `GET /metas`：读取 Agent 主动上传的 meta 快照，供 Pad 初始化 meta 列表和完成次数
- `POST /agent/sync/checkin`：Agent token 接口，更新 last_seen
- `GET /agent/sync/queue`：Agent token 接口，拉取队列全文
- `DELETE /agent/sync/queue/<id>`：Agent token 接口，删除已处理队列项
- `POST /agent/sync/display-json`：Agent token 接口，上传展示 JSON
- `POST /agent/sync/metas`：Agent token 接口，上传 meta 快照

本地接口（agent.py）：

基础健康检查：
- `GET /ping`：检查 agent 是否存活，并返回数据库健康状态
- `GET /db_health`：检查 SQLite 连通性、`foreign_keys` 状态和当前业务表

本地读取接口：
- `GET /entry?date=YYYY-MM-DD`：按日期读取单天记录，返回当天正文、字符数、meta notes，以及当天各个 meta 的状态
- `GET /metas`：读取所有 meta 定义，适合本地调试或生成云端 meta 快照

云端 Pad 不读取历史 entry；历史记录保留在本地 agent / SQLite / Markdown 中查看。云端 Pad 只通过 cloud-api 读取 agent 主动上传的 `/metas` 快照。

写入接口：
- `POST /save`：保存文本并更新统计；参数 `text` 第一行是日期，格式 `YYYY-MM-DD`
- `POST /consume_inbox`：批量消费 `agent_inbox/*.txt` 并更新统计
- `POST /echo`：测试用，写入 `_agent_debug.json`

#### `GET /entry` 示例
请求：

```bash
curl "http://127.0.0.1:8787/entry?date=2026-04-11"
```

成功返回示例：

```json
{
  "ok": true,
  "entry": {
    "entry_date": "2026-04-11",
    "content": "今天的正文",
    "char_count": 360,
    "meta_notes": "nofap: 38.1 -> 39.5 -> 40.1",
    "created_at": "2026-04-11 14:30:32",
    "updated_at": "2026-04-11 14:30:32"
  },
  "metas": [
    {
      "meta_key": "reading",
      "label": "reading",
      "category": null,
      "unit": "天",
      "enabled": true,
      "sort_order": 0,
      "count": 115,
      "done": false
    }
  ]
}
```

常见错误：
- 缺少 `date` 参数：返回 `400`
- `date` 格式不合法：返回 `400`
- 该日期不存在记录：返回 `404`

#### `GET /metas` 示例
请求：

```bash
curl "http://127.0.0.1:8787/metas"
```

成功返回示例：

```json
{
  "ok": true,
  "metas": [
    {
      "meta_key": "reading",
      "label": "reading",
      "category": null,
      "unit": "天",
      "enabled": true,
      "sort_order": 0,
      "created_at": "2026-04-06 17:40:12"
    }
  ]
}
```

#### `POST /save` 示例
请求：

```bash
curl -X POST "http://127.0.0.1:8787/save" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "2026-04-11\n今天的正文\nreading: 115 天\n---\nnotes..."
  }'
```

成功返回示例：

```json
{
  "ok": true,
  "message": "saved locally",
  "date": "2026-04-11"
}
```

注意：旧的 scp 同步默认关闭。只有设置 `LEGACY_SCP_REMOTE` 时，`agent.py` 才会继续把 JSON scp 到旧服务器路径。

### 4) 网页说明
- `daily_pad_with_meta_notes.html`：写日记的网页页面。
- `heatmap/index.html`：日历热力图页面，需要 `daily_char_map.json` 和 `daily_meta_map.json` 两个数据文件。

## 常见问题
- 日记日期：默认从内容第一行读取，格式 `YYYY-MM-DD`
- 归档重复：如果内容一致则跳过写入

## 许可
自用脚本，按需修改即可。
