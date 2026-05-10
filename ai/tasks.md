# 实施任务拆分

原则：
- 先让 SQLite 接入现有链路
- 先保留 Markdown 和 JSON 兼容输出
- 不一次性同时重写前端、后端、统计页

---

# 第一阶段：数据库接管主数据源

状态：已基本完成

说明：
- 这一阶段的目标是让 SQLite 成为真实业务数据源
- Markdown 和 JSON 继续作为导出产物与展示兼容层

---

# ~~Phase 1：确定主数据源与表结构~~

状态：已完成

目标：
- ~~明确 SQLite 是主数据源~~
- ~~固定三张表：`daily_entries`、`metas`、`daily_meta_status`~~

任务：
- ~~敲定 `notes` 放在 `daily_entries.meta_notes`~~
- ~~敲定 `done` 放在 `daily_meta_status.done`~~
- ~~统一现有页面导出格式与数据库字段语义~~

完成标准：
- ~~`schema.md` 可以直接指导建表~~

---

# ~~Phase 2：初始化数据库~~

状态：已完成

目标：
- ~~项目启动后可自动创建 SQLite 文件和表~~

任务：
- ~~新增数据库初始化模块~~
- ~~创建建表 SQL~~
- ~~补充基础索引和连接封装~~

完成标准：
- ~~本地首次运行 agent 后能得到一个可用的 `.db` 文件~~

---

# ~~Phase 3：历史数据迁移~~

状态：已完成

目标：
- ~~把现有 `daily_logs/*.md` 迁入 SQLite~~

任务：
- ~~复用已有解析逻辑读取历史 Markdown~~
- ~~写入 `daily_entries`~~
- ~~写入 `daily_meta_status`~~
- ~~必要时写入默认 `metas`~~
- ~~对历史数据按既有规则补推断 `done`~~

完成标准：
- ~~迁移后抽查数天数据，能和现有 Markdown / JSON 对上~~

---

# ~~Phase 4：改造 `/save`~~

状态：已完成

目标：
- ~~新提交的数据不仅写文件，也写入 SQLite~~

任务：
- ~~保留当前 `POST /save` 接口~~
- ~~保存当天正文、meta notes、meta 状态到 SQLite~~
- ~~继续导出 Markdown~~
- ~~继续刷新 `daily_meta_map.json` 和 `daily_char_map.json`~~

完成标准：
- ~~页面无需改动也能把新数据写入 SQLite~~

---

# 第二阶段：个人网页与远程使用

状态：进行中

当前真实结构：
- 首页是公开展示页：监控占位 + 底部 Heatmap
- Pad 是管理员工作区：登录后写入、读取 entry/metas、检查 Agent 状态
- Heatmap 暂时继续读导出的 JSON
- 已新增 `cloud_api.py`，本地开发环境可以拆成 `web` / `cloud-api` / `agent`
- 当前 `cloud_api.py -> agent` 的 Docker 内网转发只适合本地三容器测试，不是最终云端方案

目标结构：
- `web`：静态页面，公开首页和 Pad UI
- `cloud_api.py`：云端常驻服务，负责登录、服务器文件队列、agent 状态、展示 JSON
- `agent.py`：本地私有服务，唯一可以读写 SQLite / Markdown / JSON 的组件，并主动同步云端队列

职责边界：
- cloud_api 可以做：登录、限速、CORS、队列文本保存/查看/删除、保存展示用 JSON、记录 agent last_seen
- cloud_api 不做：解析 meta、写 `daily_system.db`、写 `daily_logs`、生成统计 JSON
- agent 必须保留：读取本地数据、保存文本、生成统计 JSON、主动拉取 cloud_api 队列、上传展示 JSON
- Pad 只请求 cloud_api；真实数据同步由本地 agent 主动发起

已经完成：
- SQLite 读取接口：`GET /entry`、`GET /metas`
- `GET /metas` 已返回最近完成次数 `count`
- 首页静态站点结构
- Pad 从 `/journal/pad/` 移到 `/pad/`
- 管理员最小登录：`/auth/login`、`/auth/logout`、`/auth/me`
- cloud-api 登录、限速、CORS
- 服务器端文件队列：`POST /queue`、`GET /queue`、`DELETE /queue/<id>`、`POST /queue/<id>/save`
- Docker Web + cloud-api + Agent 本地开发编排
- agent 离线时，Pad 仍可登录并把写入内容保存到 `server_queue/`

剩余核心问题：
- 最终云端部署不能依赖 `cloud_api -> agent` 内网转发
- 需要改成本地 agent 主动拉取 cloud_api 队列
- agent 处理完成后需要把最新 heatmap JSON 上传回 cloud_api / web 数据目录
- meta 管理接口还没做
- README / tasks 还要持续跟真实数据流同步

原则：
- 首页只展示，不直接暴露写入能力
- 所有读取真实记录和写入操作都要登录
- 浏览器本地 `localStorage` 只能作为本设备草稿保险，不能作为正式多端队列
- 多端写入要走 cloud_api
- 云端不能假设可以主动访问本地 agent
- 本地 agent 应主动访问 cloud_api：拉取队列、处理文本、上传展示 JSON、删除已完成队列项
- 恢复连接后，待同步内容必须可审查，不自动静默污染本地数据库
- 敏感配置通过环境变量管理，不把管理员凭据或部署密钥写死在仓库里

---

# Phase 4.5：拆分 cloud_api 与本地 agent

状态：本地开发测试点已完成（保留为临时方案）

目标：
- 让云服务器在本地 agent 离线时仍可登录和缓存文本
- 让真实数据读写仍只发生在本地 agent
- 先在单机 Docker 环境验证 cloud-api 独立登录和队列能力

任务：
- ~~新增 `cloud_api.py`~~
- ~~把 `/auth/login`、`/auth/logout`、`/auth/me` 从 `agent.py` 迁移到 `cloud_api.py`~~
- ~~把 `/queue`、`/queue/<id>`、`/queue/<id>/save` 从 `agent.py` 迁移到 `cloud_api.py`~~
- ~~把登录限速和 CORS 配置迁移到 `cloud_api.py`~~
- ~~`cloud_api.py` 新增 `LOCAL_AGENT_URL` 配置，用于转发到本地 agent~~
- ~~`cloud_api.py` 暴露 `/save`：agent 在线则转发，agent 离线则写入队列~~
- ~~`cloud_api.py` 暴露 `/entry`、`/metas`、`/db_health`、`/ping`：只做转发，agent 离线时返回明确错误~~
- ~~nginx 增加 Docker DNS 动态解析，避免 cloud-api 重建后必须重启 web~~
- `agent.py` 移除云端登录和队列职责，只保留本地数据读写
- ~~nginx `/api/*` 改为代理 `cloud_api`~~
- ~~Docker Compose 增加 `cloud-api` 服务，并挂载 `server_queue/`~~

完成标准：
- ~~关闭本地 agent 后，仍能登录 Pad~~
- ~~关闭本地 agent 后，点击发送会把文本存入 `server_queue/`~~
- ~~本地 agent 恢复后，可以从 Pad 队列逐条发送到 agent~~
- ~~cloud_api 不读写 `daily_system.db`、`daily_logs`、统计 JSON~~

备注：
- 当前 `LOCAL_AGENT_URL=http://agent:8787` 和 `INTERNAL_AGENT_TOKEN` 只适合同一个 Docker Compose 网络下的本地开发。
- 真实部署时，云服务器不能直接访问家里/本地电脑上的 agent，最终要进入 Phase 4.6。

---

# Phase 4.6：真实云端同步模型

状态：下一步

目标：
- cloud-api 只负责云端登录、队列和 agent 状态
- 本地 agent 主动拉取 cloud-api 队列并写入本地数据
- agent 处理完成后上传最新展示 JSON，供首页 Heatmap 使用

任务：
- cloud-api 增加 agent sync token 校验
- cloud-api 增加 agent check-in / last_seen
- cloud-api 增加展示 JSON 上传接口，供 agent 更新 `daily_char_map.json` / `daily_meta_map.json`
- agent.py 增加 cloud sync 命令或后台循环
- agent.py 主动拉取云端 queue 并逐条保存到本地 DB / daily_logs / JSON
- 保存成功后删除云端 queue item
- agent.py 上传最新展示 JSON 回云端
- Pad 状态从“直连 agent”改成“查看 cloud-api last_seen”

预期数据流：
- 多端 Pad 写记录
- cloud-api 保存到云端队列
- 本地 agent 主动拉取队列
- agent 保存到本地数据库和日志
- agent 生成统计 JSON
- agent 上传 JSON 回云端
- 首页 Heatmap 读取云端最新 JSON

---

# ~~Phase 5：补读取接口~~

状态：已完成

目标：
- 让前端可以从后端回读数据，而不只会提交

任务：
- 增加 `GET /entry?date=YYYY-MM-DD`
- 增加 `GET /metas`
- 评估是否增加 `GET /heatmap`，或暂时继续使用导出的 JSON
- 约定接口返回结构

完成标准：
- 后端已具备“读当天记录”和“读 meta 定义”的能力

---

# Phase 6：前端逐步脱离 localStorage

状态：部分完成

目标：
- 让页面以后端数据为准

任务：
- ~~页面启动时从接口拉取 metas~~
- ~~打开某一天时从接口拉取 entry~~
- `localStorage` 仅保留本设备草稿缓存或临时状态
- 正式待同步队列已迁移到服务器文件队列，但仍与当前 `agent.py` 进程耦合
- 明确哪些内容不再保存在浏览器本地

完成标准：
- 页面刷新后优先从后端恢复数据，而不是依赖本地浏览器状态
- 页面在离线或无法连接本地 agent 时，仍可保留待同步内容，但不会误判为已正式保存
- 页面恢复连接后，用户可以逐条查看待同步内容并决定是否正式提交

---

# ~~Phase 7：个人网页首页~~

状态：已完成

目标：
- 做一个个人网页首页，整合已有展示内容

任务：
- 设计首页结构：heatmap、当天摘要、最近几天概览
- 将 `heatmap` 接入首页，作为每日 meta 的主要展示组件
- 明确首页哪些内容是公开展示，哪些内容只对管理员显示
- 保持移动端和桌面端都能正常查看

完成标准：
- 网站首页可访问，并能稳定展示 heatmap 和每日 meta 摘要

---

# Phase 8：远程写字板改造

状态：部分完成

目标：
- 把 `daily_pad_with_meta_notes.html` 放到服务器网页上使用
- 不再依赖本地浏览器 `localStorage` 保存正式数据
- 正文仍只通过 agent 保存到本地

任务：
- ~~拆分“页面状态”和“正式保存”的数据流~~
- ~~页面加载时从后端读取当天 entry 和 metas~~
- ~~提交时让网页请求受控接口，再由 agent 落本地 Markdown / SQLite~~
- ~~设计服务器端待同步队列~~
- ~~Agent 在线：保存到 Agent~~
- Agent 写入失败：保存到当前服务器队列
- ~~Pad 显示服务器队列，而不是只显示本设备 localStorage 队列~~
- ~~恢复连接后，用户逐条确认再同步到 Agent~~
- 明确失败场景：agent 不在线、网络失败、重复提交、覆盖提交
- 明确缓存内容的最小元数据：日期、正文、meta 状态、来源设备、最后编辑时间、同步状态
- 拆分“云端队列 API”和“本地写入 agent”，让队列在本地 agent 离线时仍可用

完成标准：
- 网页可在服务器上使用
- 不依赖本地 `localStorage` 恢复正式数据
- 本地写入 agent 离线时，多端提交能进入云端待同步队列
- Agent 在线时，正式写入 Agent
- 即使存在待同步缓存，也不会在恢复连接后自动静默污染本地数据库

---

# Phase 9：管理员登录与安全加固

目标：
- 只有管理员可以访问 cloud_api 业务接口
- 登录机制足够支撑公网部署

任务：
- ~~增加最小可用的管理员登录机制~~
- cloud_api 业务接口要求登录
- agent 不直接暴露公网，只接受 cloud_api 或本地访问
- 登录失败限速，防止暴力破解
- 生产环境收紧 CORS，只允许站点自己的 Origin
- 生产环境不要直接暴露 `agent:8787` 到公网
- 管理员可新增、修改、启用/停用、排序 meta
- 记录关键管理操作，方便排查误操作

完成标准：
- 未登录用户只能看公开首页和 Heatmap 静态展示
- 管理员登录 cloud_api 后才能读取真实 entry/metas 和执行写入/队列操作
- `/auth/login` 有限速
- Agent 端口不裸露公网

---

# Phase 10：部署与收尾

目标：
- 减少重复数据源和后续维护成本

任务：
- 评估是否继续保留 `daily_logs` 作为可读归档
- 确认 heatmap 最终是继续读 JSON，还是改为走接口
- 补 README、部署说明、管理员使用说明、安全配置说明
- 画清楚最终数据流：公开网页 / 管理后台 / 本地 agent / SQLite / Markdown
- 确认 `docker-compose.yml` 中 agent 端口暴露策略
- 确认服务器持久化目录：SQLite、`daily_logs`、导出 JSON、待同步队列

完成标准：
- 数据源职责清晰
- 新人只看文档就知道系统真实数据流
