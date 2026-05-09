## web

这是站点静态前端骨架目录。

当前目标：
- 保留简洁主页和 Pad 工作区
- 首页展示监控占位和底部 Heatmap
- Pad 负责写入和管理状态检查
- 让 `web/` 尽量独立，后面可以直接作为静态站点打包部署

当前结构：

```text
web/
  README.md
  site/
    index.html
    assets/
      site.css
      site.js
    data/
      daily_char_map.json
      daily_meta_map.json
    pad/
      index.html
    journal/
      index.html
      heatmap/
        index.html
```

说明：
- `site/index.html`：站点首页，预留监控区域，并在底部展示 Heatmap
- `site/pad/index.html`：写入和管理工作区，包含 Pad、Agent 状态、数据库和 metas 检查
- `site/journal/heatmap/index.html`：热力图页面，直接读取 `site/data/*.json`
- `site/journal/index.html`：日志模块兼容入口
- `site/data/*.json`：给静态站点使用的数据文件副本

后续建议：
- 已补 `web/nginx/default.conf` 与 `docker-compose.yml` 中的 `web` 服务
- 本地可通过 `docker compose up --build` 启动
- 启动后静态站点默认从 `http://127.0.0.1:8080` 访问
- `nginx` 会把 `/api/*` 代理到 `agent:8787`
- 再之后让生成脚本或部署脚本同步更新 `site/data/*.json`
- 根目录的 `daily_pad_with_meta_notes.html` 可以继续保留，方便本地单独使用
