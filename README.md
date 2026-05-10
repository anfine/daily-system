# daily-system

一个用于记录、归档日记内容，并生成统计数据的本地小工具集合。包含：
- 日记归档脚本（按日期落盘）
- meta/字符数统计脚本
- 本地 Flask Agent，用于接收网页或其他客户端的日记文本并更新统计

## 功能概览
- 归档：从聊天或输入日志里提取每日内容，写入 `daily_logs/YYYY/MM/DD.md`
- 统计：生成 `daily_meta_map.json` 与 `daily_char_map.json`
- Agent：提供 HTTP 接口保存日记、更新统计，并可自动 scp 到服务器

## 目录结构
- `archive_daily.py`：解析日志片段并写入 `daily_logs`
- `build_daily_char_meta_map.py`：遍历 `daily_logs` 生成统计 JSON
- `agent.py`：Flask 服务端，提供 `/ping` `/db_health` `/entry` `/metas` `/echo` `/save` `/consume_inbox`
- `daily_logs/`：按日期归档的日记内容
- `daily_meta_map.json`：每日 meta 结构化统计
- `daily_char_map.json`：每日字符数统计
- `agent_inbox/`：Agent 暂存文本
- `inbox/`：原始输入文件目录（可选）
- `daily_pad_with_meta_notes.html`：写日记的网页
- `heatmap/index.html`：简单日历展示页，依赖两个 JSON 数据文件

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

管理员登录：
- Docker 模式下由 `cloud-api` 负责登录、限速和云端队列。
- 本地或 Docker 启动前设置 `ADMIN_PASSWORD`。
- Docker 可同时设置 `ADMIN_SESSION_SECRET`，用于签名登录 session。
- `POST /auth/login` 已按 IP 限速：1 分钟内失败 5 次后锁定 10 分钟。
- Docker 模式下 `agent` 只通过 Docker 网络暴露给 `cloud-api`，不再把 `8787` 直接映射到宿主机。
- 生产环境可设置 `ALLOWED_ORIGINS` 收紧 CORS，例如 `https://example.com`。
- `LOCAL_AGENT_URL` 默认是 `http://agent:8787`；这是本地三容器开发用的临时转发地址。
- `INTERNAL_AGENT_TOKEN` 用于本地开发时 `cloud-api` 到 `agent` 的内部转发。
- 真实云端部署时，应改成本地 `agent` 主动访问云端 `cloud-api`，而不是云端主动访问本地 `agent`。

```bash
export ADMIN_PASSWORD="your-password"
export ADMIN_SESSION_SECRET="replace-with-a-long-random-string"
export ALLOWED_ORIGINS="https://example.com"
export INTERNAL_AGENT_TOKEN="replace-with-another-long-random-string"
docker compose up -d --build
```

本地开发验证：
```bash
docker compose up -d --build
docker compose stop agent
```

此时 Pad 应仍可登录，保存内容会进入云端队列 `server_queue/`。重新启动本地 agent 后，可继续验证队列发送到 agent 的本地开发链路。

下一阶段的真实云端同步模型：
- 多端 Pad 写入内容到云端 `cloud-api` 队列。
- 本地 `agent` 主动拉取云端队列。
- `agent` 写入本地 SQLite / Markdown / JSON。
- `agent` 上传最新展示 JSON 回云端，首页 Heatmap 读取云端 JSON。

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

认证接口：
- `POST /auth/login`：提交管理员密码，成功后写入 session cookie
- `POST /auth/logout`：退出登录
- `GET /auth/me`：检查当前登录状态

除 `/auth/*` 外，下面所有接口都需要管理员登录。

基础健康检查：
- `GET /ping`：检查 agent 是否存活，并返回数据库健康状态
- `GET /db_health`：检查 SQLite 连通性、`foreign_keys` 状态和当前业务表

读取接口：
- `GET /entry?date=YYYY-MM-DD`：按日期读取单天记录，返回当天正文、字符数、meta notes，以及当天各个 meta 的状态
- `GET /metas`：读取所有 meta 定义，适合前端初始化 meta 列表

写入接口：
- `POST /save`：保存文本并更新统计；参数 `text` 第一行是日期，格式 `YYYY-MM-DD`
- `POST /queue`：把完整文本保存到服务器待同步文件队列
- `GET /queue`：列出服务器待同步队列
- `GET /queue/<id>`：读取队列项全文
- `DELETE /queue/<id>`：删除队列项
- `POST /queue/<id>/save`：将队列项交给 `/save` 流程处理，成功后删除队列项
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

注意：`agent.py` 内置 `scp` 逻辑会把 JSON 同步到服务器，若不需要请自行注释或修改远端地址。

### 4) 网页说明
- `daily_pad_with_meta_notes.html`：写日记的网页页面。
- `heatmap/index.html`：日历热力图页面，需要 `daily_char_map.json` 和 `daily_meta_map.json` 两个数据文件。

## 常见问题
- 日记日期：默认从内容第一行读取，格式 `YYYY-MM-DD`
- 归档重复：如果内容一致则跳过写入

## 许可
自用脚本，按需修改即可。
