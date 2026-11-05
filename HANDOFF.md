# Paper Daily — 项目交接文档

> 面向：接手本项目的开发者 / coding agent  
> 版本：`1.5.0`  
> 最后更新：2026-11-05 13:37（Asia/Shanghai）  
> 工作目录：`/home/lsc/work/huggingface-abstract`  
> 产品说明：[README.md](README.md)  
> 后端测试：**24 passed**

---

## 0. 先读这一段

### 0.1 项目是什么

Paper Daily 是一个个人使用的 Hugging Face Daily Papers 阅读器：

- 抓取指定日期的 HF Daily Papers；
- 用 OpenAI Chat Completions 兼容模型生成中文、面向非专家的论文解读；
- 按网页设置的关键词计算相关度并排序；
- 支持待读、笔记、PDF 下载、Markdown/JSON 导出；
- 展示 HF 提供的缩略图或视频；
- 可手动刷新，也可由后台 scheduler 每 15 分钟检查新文章；
- 今日导读每天只在 **16:00 后**生成一次，16:00 前显示「正在收集文章」。

### 0.2 当前状态

- 本地旧服务已停止，本地 `8765` 已释放；
- 云端服务在 `screen` 会话 `paper-daily` 中运行；
- 云端使用 `hf-mirror.com` 绕过 huggingface.co 直连超时；
- 云端已完成 12 篇论文的首轮分析（8 篇带视频/媒体）；
- 云端当前处于收集窗口，16:00 会生成一次导读；
- 云端 v1.4.0 备份在 `/root/autodl-tmp/backups/`。

> 安全：SSH 密码曾在对话中出现过，交接后应立即更换密码、配置 SSH key，并保持 8765 只监听 `127.0.0.1`。当前没有登录系统，不要直接暴露公网。

### 0.3 最重要的约束

1. **抓取由策略控制**：手动点击和 `schedule.enabled=true` 的后台调度都可以触发抓取；后台只检查新文章，已有分析不重复调用模型。不要把调度器扩成多用户任务队列。
2. **数据本地优先**：论文、笔记、待读、关键词、PDF 都在 `data/`；API Key 只存在于 `config.yaml` / 环境变量，绝不能进入前端或日志。
3. **模型是可选增强**：没有模型也要能抓取、阅读、待读、导出。规则摘要 (`heuristic_analysis`) 是正式降级路径，不是临时占位。
4. **单进程、单任务**：同一时间只允许一个提取任务；任务通过 `asyncio.create_task` 运行在 FastAPI 进程内，不是持久队列。
5. **不要覆盖用户数据**：当前工作区可能已有用户实例和 `data/paper_daily.db`。开发调试请用临时配置和临时数据目录。

---

## 1. 产品行为

### 1.1 每日流程

```text
HF Daily API / 页面
        ↓
抓取当天论文（标题、摘要、作者、HF/GitHub/项目/PDF 链接、媒体）
        ↓
写入 SQLite
        ↓
只挑“没有 analysis”的新论文
        ↓
逐篇调用模型（后台单篇；手动可并发）
        ↓
关键词相关度排序
        ↓
16:00 后生成一次今日导读
```

### 1.2 手动刷新

网页按钮「刷新今日 / 今日提取」调用：

```json
POST /api/runs
{"date": "2026-11-05", "refresh": false}
```

行为：

- `refresh=false` 是当前 UI 唯一用法；
- 只分析没有 `analysis` 的论文；
- 已有分析不重复调用模型；
- 没有新文章时不产生模型费用；
- 不会弹出“重新总结全部论文”的确认框；
- `refresh=true` 仍保留在后端，用于显式全量重建，但 UI 不提供入口。

### 1.3 自动更新

`config.yaml`：

```yaml
schedule:
  enabled: true
  check_interval_minutes: 15
  digest_time: "16:00"
  run_on_start: true
```

- scheduler 在 FastAPI 进程内运行；
- 每 15 分钟做一次轻量 HF 检查（不抓详情页）；
- 发现新 id 才启动任务；
- scheduler 启动的任务使用 `sequential=True`，论文一篇一篇处理；
- 任务进行中不会启动第二个任务；
- 16:00 前不生成导读；
- 16:00 后如果今日没有导读，scheduler 启动一次 digest run；
- 已有导读不会随后续新文章重写；
- 16:00 后新增文章仍分析，但不再更新导读；
- 页面每 60 秒轻量轮询当天数据，出现新文章/导读后刷新界面。

### 1.4 导读状态

`GET /api/days/{day}` 返回：

- `digest_status = ready`：已有导读；
- `digest_status = collecting`：还没到 16:00，显示收集卡片；
- `digest_status = pending`：已到 16:00 但还没有导读，显示生成中；
- `digest_status = empty`：这一天没有论文。

16:00 前手动提取也不会生成导读，这是产品决策，不是 bug。

---

## 2. 技术架构

### 2.1 运行形态

- Python 3.10+（云端使用 Miniconda Python 3.12）；
- FastAPI + Uvicorn；
- 单进程、单 worker；
- SQLite WAL；
- 原生 HTML/CSS/JS，无构建步骤；
- 服务启动时创建 scheduler task；
- 静态文件由 FastAPI 直接提供。

### 2.2 关键约束

- 全局同时只允许一个 extraction run；
- 不能开多 worker，否则 scheduler 会重复触发、SQLite 也会出现写竞争；
- 进程退出会中断 in-flight run，数据库会把 run 标记为 `interrupted`；
- 云端 AutoDL 容器没有可用 systemd，当前用 `screen` 保活。

### 2.3 任务链路

```text
Runtime.start()
  → Database.create_run()
  → asyncio.create_task(ExtractionService.run)
  → HFClient.fetch_daily()
  → Database.upsert_papers()
  → 只选 analysis 为空的新论文
  → LLMClient.summarize_paper() / heuristic_analysis()
  → Database.update_paper_analysis()
  → 16:00 窗口内生成 digest（仅一次）
  → Database.update_run(status=completed)
```

前端轮询：

```text
GET /api/runs/{id}  → 状态、进度、消息
GET /api/days/{day} → papers、digest、digest_status、active_run
```

---

## 3. 目录与文件

```text
.
├── run.py                  # 启动入口；--no-browser 供云端使用
├── start.sh                # 本地启动脚本
├── connect-cloud.sh        # 本地 SSH 隧道入口（新增）
├── config.yaml             # 真实配置；含密钥，已被 gitignore
├── config.example.yaml     # 可提交模板
├── README.md               # 用户文档
├── HANDOFF.md              # 本文件
├── requirements.txt
├── app/
│   ├── __init__.py         # __version__ = 1.5.0
│   ├── config.py           # YAML、profile、schedule、public_settings
│   ├── db.py               # SQLite schema 与查询
│   ├── hf.py               # HF API/页面、镜像、媒体、PDF
│   ├── llm.py              # 模型调用、JSON 解析、降级、相关度
│   ├── prompts.py          # beginner-v4 / digest-v4
│   ├── service.py          # 抓取 → 增量分析 → 导读
│   ├── main.py             # FastAPI、Runtime、scheduler、API
│   ├── net.py              # 代理/NO_PROXY 处理
│   ├── exporting.py        # Markdown/JSON 导出
│   └── static/
│       ├── index.html
│       ├── app.js
│       ├── styles.css
│       └── favicon.svg
├── tests/                  # 24 项
├── docs/
│   ├── prompt-v4.md
│   ├── model-selection.md
│   └── prompt-v2.md / prompt-v3.md（历史）
├── deploy/
│   ├── paper-daily.service
│   └── README.md
└── backup/
    └── paper-daily-v1.4.0-20261105-115011.tar.gz
```

---

## 4. 配置

### 4.1 配置来源

1. `config.yaml`（或 `HF_PAPER_CONFIG` 指向的文件）；
2. `app_settings.profile` 中的网页设置覆盖 `profile.interests` / `profile.notes`；
3. 环境变量形式 `${ENV_VAR}` 可在 YAML 中解析。

`config.yaml` 权限应为 `600`，不要提交。`public_settings` 会返回路径、目录和 key 掩码，但设置界面已不再展示这些内容。

### 4.2 关键配置

```yaml
app:
  host: "127.0.0.1"
  port: 8765
  open_browser: false
  timezone: "Asia/Shanghai"

storage:
  data_dir: "./data"
  cache_pdfs: true

profile:
  interests:
    - "Agent、工具调用"
  notes: "先讲结论..."

llm:
  enabled: true
  base_url: "https://api.uiuihao.com/v1"
  api_key: "${HF_PAPER_LLM_API_KEY}"  # 也可以直接写；不要泄露
  model: "qwen3.7-plus"
  concurrency: 3
  timeout_seconds: 360
  max_tokens: 8000

fetch:
  hf_base_url: "https://huggingface.co"   # 云端改为 https://hf-mirror.com
  detail_pages: true
  max_papers: 100

digest:
  enabled: true
  top_n: 18

schedule:
  enabled: true
  check_interval_minutes: 15
  digest_time: "16:00"
  run_on_start: true
```

### 4.3 网页关键词设置

- 最多 12 个关键词；
- 每个 2–30 字；
- 阅读偏好最多 300 字；
- 保存后立即重算所有已存论文的 `relevance`；
- 不会重写旧分析正文/导读文字；
- 存储键：`app_settings["profile"]`，JSON：
  `{"interests": [...], "notes": "..."}`
- 有 active run 时保存返回 409。

注意：`profile.favorite_topics` 仍参与 `ProfileConfig.interest_terms()` 和相关度加分，但 UI 不编辑它。想让网页关键词完全接管排序，需要进一步统一（见 §13）。

---

## 5. 数据模型

数据库：`data/paper_daily.db`，WAL 模式。

### 5.1 `papers`

关键列：

- 标识：`id`（arXiv id）、`daily_date`；
- 内容：`title`、`abstract`、`ai_summary`、`keywords`、`authors`、`organization`；
- HF 元数据：`thumbnail`、`hf_url`、`arxiv_url`、`pdf_url`、`github_url`、`project_url`；
- 原始数据：`raw_json`，包含 `media_urls`、`video_url`、`discussionId` 等；
- 分析：`analysis`（JSON）、`analysis_status`、`analysis_source`、`analysis_error`；
- 排序：`relevance`；
- 用户数据：`bookmarked`（UI 显示为「待读」）、`note`、`pdf_path`；
- 旧列：`read` 仍存在但已不再读写，不要恢复旧逻辑。

### 5.2 `runs`

- `id`、`date`、`status`、`stage`；
- `total` / `processed` / `failed`；
- `message`；
- `digest`（JSON，导读）；
- `llm_enabled`；
- 时间戳。

状态：`queued → running → completed|failed|interrupted`。  
stage：`queued / fetching / analyzing / digest / done / failed / stopped`。

### 5.3 `app_settings`

- `key` / `value` / `updated_at`；
- 目前使用 `profile` 键；
- 没有 schema migration 框架。

### 5.4 分析 JSON

来源：`beginner-v4` 或 `heuristic-v4`。主要字段：

`title_zh`, `tldr`, `problem`, `summary`, `analogy`, `how_it_works`, `contrast`, `example`, `misconceptions`, `jargon`, `key_points`, `method`, `findings`, `why_it_matters`, `limitations`, `takeaway`, `relevance_score`, `tags`, `difficulty`, `read_minutes`, `paper_type`, `prompt_version`。

没有 `critical_view`，也没有自动生成的追问。`misconceptions` 是“减少追问”的替代机制。

### 5.5 导读 JSON

来源：`digest-v4` 或 `digest-heuristic-v4`：

- `headline`
- `overview`
- `primer`
- `threads`（`problem/approach/evidence/boundary/paper_ids`）
- `reading_path`
- `top_picks`
- `skip_hint`

没有 `questions`。

---

## 6. 后端模块

### 6.1 `app/config.py`

- `Settings`：server/storage/profile/llm/fetch/digest/schedule；
- `FetchConfig.hf_base_url`：支持 HF 镜像；默认 `https://huggingface.co`；
- `ScheduleConfig`：enabled / interval / digest_time / run_on_start；
- `ProfileConfig.interest_terms()`：拆分兴趣词并去重；
- `public_settings()`：给前端的安全视图（仍包含路径字段，UI 已隐藏）；
- 配置错误抛 `ConfigError`。

### 6.2 `app/hf.py`

- `HFClient.fetch_daily(day, enrich_details=None)`；
- `_find_latest_available_date()`：HF 当天尚未发布时向后探测；
- `parse_daily_page()`：API 返回空时的 HTML 兜底；
- `_enrich_details()`：补 GitHub/项目链接；
- `parse_detail_media()`：`og:image` / `og:video` / `<video>` / `<source>`；
- `normalize_daily_item()`：规范化 API 数据；
- `_media_urls()`：保存 HF `mediaUrls`（最多 4 个）；
- `download_pdf()`：下载到 `data/downloads/<date>/`；
- 代理通过 `app/net.py` 的 `proxy_for()`，`trust_env=False`。

镜像模式：

```yaml
fetch:
  hf_base_url: "https://hf-mirror.com"
```

API 和论文详情页走镜像；存储的 `hf_url` 仍指向 huggingface.co，避免改变用户看到的原文入口。

### 6.3 `app/service.py`

- `digest_due(settings, day)`：当天 >= digest_time，或历史日期；
- `ExtractionService.run()`：
  - 抓取；
  - upsert；
  - 只处理 `analysis` 为空的新论文；
  - 手动模式按 `llm.concurrency` 并发；scheduler 模式 `sequential=True`；
  - 模型失败写 fallback，并记录 failed；
  - `_Breaker` 连续失败后跳过后续模型调用；
  - 导读只在 due 且没有 existing digest 时生成一次。
- 无模型时：heuristic analysis；如果要生成导读，使用 heuristic digest。

> 当前 `_completion_message` 在 pre-16 会把“与今日导读”替换掉，再追加“今日导读将在 16:00 生成”。

### 6.4 `app/main.py`

- `Runtime`：配置、DB、service、tasks、scheduler；
- `start()`：检查 active run，创建 run + asyncio task；
- `start_scheduler()` / `_scheduler_tick()` / `_scheduler_loop()`；
- 每 30 秒 tick；实际 HF 检查按 15 分钟；
- 16:00 缺导读时启动一次 digest run，并用 `_digest_triggered_date` 防止同一天重复触发；
- `lifespan` 启动 scheduler，shutdown 时取消任务；
- 安全中间件设置 `X-Content-Type-Options`、`Referrer-Policy`、`X-Frame-Options` 等；
- `create_app()` 注册 API 和静态文件。

### 6.5 `app/db.py`

主要方法：

- `init()` / `connect()`；
- `upsert_papers()` / `get_papers_for_date()` / `all_papers()`；
- `update_paper_analysis()` / `update_relevance()`；
- `update_paper_flags()`（只接受 bookmarked/note）；
- `search()` / `list_days()` / `day_summary()`；
- `create_run()` / `active_run()` / `latest_run()` / `update_run()`；
- `set_digest()` / `digest_for_day()`；
- `get_setting()` / `set_setting()`。

保留旧 `read` 列只是因为旧库存在；新代码不返回、不写入、不展示。

### 6.6 `app/llm.py` / `app/prompts.py`

- OpenAI Chat Completions 兼容；
- 支持 JSON mode 自动降级；
- `normalize_analysis()` / `normalize_digest()`；
- 失败时 `heuristic_analysis()` / `heuristic_digest()`；
- `score_relevance()`：关键词、标题、摘要、标签；
- `prompt_version`：`beginner-v4` / `digest-v4`。

### 6.7 `app/exporting.py`

- Markdown / JSON 导出；
- 输出 v4 的 primer/threads/reading_path、misconceptions；
- 不再输出 critical/questions。

---

## 7. API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 健康检查，版本、今天、模型是否配置 |
| GET | `/api/meta` | 版本、today、settings、active_run |
| GET | `/api/settings` | 配置的公开视图 |
| POST | `/api/settings/reload` | 重新加载 YAML；有 active run 时 409 |
| POST | `/api/settings/test` | 测试模型连接；会发一个小请求 |
| PUT | `/api/profile` | 保存关键词/偏好并重算相关度 |
| GET | `/api/history` | 有数据的日期 |
| GET | `/api/search?q=` | 跨日期搜索 |
| GET | `/api/days/{day}` | papers + digest + `digest_status` + runs |
| POST | `/api/runs` | 启动任务 |
| GET | `/api/runs/active` | 当前任务 |
| GET | `/api/runs/{id}` | 任务进度 |
| GET | `/api/papers/{id}` | 论文详情 |
| PATCH | `/api/papers/{id}` | 更新 `bookmarked` / `note` |
| GET | `/api/papers/{id}/pdf` | 下载/缓存 PDF |
| GET | `/api/days/{day}/export.md` | Markdown |
| GET | `/api/days/{day}/export.json` | JSON |
| GET | `/` | 前端 |

`PUT /api/profile` 限制：1–12 个关键词，每个 2–30 字，notes <= 300；active run 时 409。

---

## 8. 前端

### 8.1 文件

- `app/static/index.html`：shell、日期栏、筛选、运行按钮、设置容器；
- `app/static/app.js`：状态、API、渲染、轮询、媒体；
- `app/static/styles.css`：主题、布局、卡片、导读、响应式。

### 8.2 当前 UI

- 顶部只有品牌、主题切换、设置；
- 左侧：日期卡、刷新按钮、全部论文/待读、往期；
- 右侧：标题、统计、导读或收集卡、搜索/排序、论文卡片；
- 论文卡：编号、类型、相关度、中文标题、英文标题、摘要、标签、作者/机构/时长/难度/upvotes、HF/arXiv/PDF/待读；
- 有媒体时右侧显示 HF 缩略图或 `mediaUrls` 视频（VIDEO 标记）；
- 详情：TLDR、媒体、概念小抄、正文、误解、关键点、方法/结果/局限、原始摘要、笔记；
- 设置：模型状态/测试连接、关键词 chips、阅读偏好、「配置生效」；
- 设置页不显示 `config.yaml`、数据目录、API Key、Endpoint、LOCAL FILES、HOW TO CONFIGURE、本地文件；
- 页脚、顶部本地标识、侧栏隐私说明已移除；
- 布局：shell 全宽，正文最大 `1120px`，body 16px；
- 移动端 720px 以下卡片媒体变为整行。

### 8.3 媒体规则

`mediaOf(paper)`：

1. 优先 `raw.media_urls` / `raw.video_url` 中的视频；
2. 没有视频时用 `thumbnail` / `raw.image_url`；
3. URL 经过 `safeUrl()`；
4. 卡片视频用 `muted loop playsinline preload="metadata"`，不自动播放；
5. 详情视频 `controls`；
6. 图片加载失败隐藏视觉块。

HF API 的 `thumbnail` 是 social thumbnail；真正的动态卡片通常来自 `mediaUrls` 的 mp4。当前云端 12 篇中 8 篇有视频媒体。

### 8.4 自动轮询

- scheduler 启用且查看今天时，每 60 秒 `GET /api/days/{day}`；
- 发现论文数、latest_run、digest 变化才重渲染；
- 有详情弹层/设置弹层时暂缓；
- 后台 run 出现后自动接管 run 轮询。

---

## 9. 提示词与模型

### 9.1 版本

- 论文：`beginner-v4`
- 导读：`digest-v4`
- 文档：`docs/prompt-v4.md`

### 9.2 设计目标

- 先解释问题，再解释方法；
- 定义术语，不假设读者懂；
- 用类比/例子建立直觉；
- 对照旧做法；
- 主动澄清常见误解；
- 不写审稿式评分，不生成追问；
- 让读者读完不需要继续追问。

### 9.3 模型

当前云端/本地配置：`qwen3.7-plus`，OpenAI 兼容网关。  
云端已验证 12 篇论文调用成功。

预算参考见 `docs/model-selection.md`：

- `qwen3.7-plus` 约 $0.15–0.22 / 35 篇；
- `qwen3.7-max` / `qwen3.8-max` 约 $0.65–0.95；
- `qwen3.8-flash` 约 $0.05–0.08，质量风险更高。

---

## 10. 云端部署（当前实际状态）

### 10.1 位置与进程

```text
主机：region-9.autodl.pro
端口：13048
项目：/root/autodl-tmp/paper-daily
Python：/root/miniconda3/bin/python (3.12.3)
虚拟环境：/root/autodl-tmp/paper-daily/.venv
运行：screen 会话 paper-daily
日志：/root/autodl-tmp/paper-daily/paper-daily.log
监听：127.0.0.1:8765
```

云端没有可用的 systemd（容器 init 是 bash），所以用 `screen`：

```bash
screen -list
screen -r paper-daily
# Ctrl-A D 退出但保持运行
```

### 10.2 云端镜像

云端无法直连 huggingface.co，已配置：

```yaml
fetch:
  hf_base_url: "https://hf-mirror.com"
```

已验证：

- `hf-mirror.com/api/daily_papers` 可访问；
- `hf-mirror.com/papers/...` 可访问；
- `cdn-thumbnails.hf-mirror.com` 可访问；
- arxiv.org 可访问；
- 模型 API 可访问；
- 偶尔出现 429，scheduler 15 分钟后会重试。

### 10.3 当前数据

- 已抓取 12 篇 2026-11-05 论文；
- 8 篇有 `mediaUrls` 视频；
- 已完成模型分析；
- `digest_status = collecting`；
- 16:00 后 scheduler 生成一次导读。

### 10.4 启动/重启命令

```bash
cd /root/autodl-tmp/paper-daily
screen -S paper-daily -X quit || true
screen -L -Logfile /root/autodl-tmp/paper-daily/paper-daily.log \
  -dmS paper-daily bash -lc \
  'cd /root/autodl-tmp/paper-daily && exec .venv/bin/python run.py --no-browser'
curl -s http://127.0.0.1:8765/api/health
```

容器重启后 screen 不会自动恢复，需要重新执行上面的命令。  
`deploy/paper-daily.service` 已提供 systemd 模板，但 AutoDL 当前未使用。

### 10.5 入口

本地项目根目录：

```bash
./connect-cloud.sh
```

脚本内容：

```bash
ssh -N -p 13048 -L 8765:127.0.0.1:8765 root@region-9.autodl.pro
```

保持终端打开，浏览器访问：

```text
http://127.0.0.1:8765
```

如果本地 8765 被占用：

```bash
ssh -N -p 13048 -L 8766:127.0.0.1:8765 root@region-9.autodl.pro
# http://127.0.0.1:8766
```

### 10.6 安全

- 当前只有单用户，无登录、无 CSRF、无权限隔离；
- 不能把 8765 暴露公网；
- SSH 密码已在对话中出现，应立即修改；
- 建议 SSH key + 禁止 root 密码登录；
- 多用户前必须增加认证、每用户独立数据目录和配额。

---

## 11. 备份与回滚

### 11.1 本地

```text
backup/paper-daily-v1.4.0-20261105-115011.tar.gz
backup/paper-daily-v1.4.0-20261105-115011.sha256
```

包含源码、配置、SQLite、已下载 PDF；权限 600；已加入 `.gitignore`。

### 11.2 云端

```text
/root/autodl-tmp/backups/paper-daily-v1.4.0-20261105-115011.tar.gz
/root/autodl-tmp/backups/paper-daily-v1.4.0-20261105-115011.sha256
```

### 11.3 回滚

```bash
cd /home/lsc/work/huggingface-abstract
tar -xzf backup/paper-daily-v1.4.0-20261105-115011.tar.gz
```

云端回滚前先停止 screen，再解压覆盖，重新启动。  
备份包含 `config.yaml` 密钥和 `data/`，不要发给他人或提交到仓库。

---

## 12. 测试与验收

### 12.1 自动化测试

```bash
cd /home/lsc/work/huggingface-abstract
. .venv/bin/activate
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q -p pytest_asyncio.plugin
```

当前：**24 passed**。

覆盖：

- `tests/test_hf.py`（8）：日期边界、HTML 兜底、链接、媒体；
- `tests/test_llm.py`（11）：JSON 降级、鉴权、规则摘要、导读结构；
- `tests/test_db.py`（3）：待读/笔记、关键词重算、run；
- `tests/test_api.py`（2）：API、导出、digest 状态。

没有 service 编排的完整自动化测试；scheduler 和真实模型链路以手工验收为主。

### 12.2 已完成验收

- 本地 1844×968、1440×1000、1152×900 页面检查；
- 云端 SSH 隧道实际打开页面；
- 云端媒体：4 张图 + 8 个视频，视频 readyState=4；
- 云端 12 篇模型摘要完成；
- 自动 scheduler 启动并调用 HF 镜像；
- 24 项测试通过。

### 12.3 未完成验收

- 云端 16:00 导读生成尚未在真实时钟到达时观察（临时环境已验证 due 逻辑）；
- 容器重启后的自动恢复未配置；
- 多用户/公网访问未实现。

---

## 13. 已知问题与风险

1. **scheduler 在应用进程内**
   - 进程停止/重启会中断；
   - 必须单 worker；
   - screen 不是 systemd，容器重启后要手动起。

2. **HF 镜像可能 429**
   - 云端首次检查遇到过；
   - 下次 15 分钟 tick 会重试；
   - 如果频繁 429，需要加退避或换镜像/代理。

3. **导读只生成一次**
   - 16:00 后没有导读才生成；
   - 已有导读不会更新；
   - 生成失败后用 `_digest_triggered_date` 阻止当天自动重试，只能手动触发或等重启。

4. **视频/图片是热链**
   - 不下载到本地；
   - 云端只存 URL；
   - HF 删除资源/防盗链时图片会隐藏；
   - 视频来自 `cdn-uploads.huggingface.co`，浏览器需能访问。

5. **媒体匹配**
   - 优先 `mediaUrls` 视频，其次 social thumbnail；
   - 不是所有论文都有动态媒体；
   - 没有解析 arXiv 正文图。

6. **增量依赖 analysis 是否存在**
   - 已有分析不重跑，即使旧摘要质量差；
   - 强制重建需要 API `refresh: true`，UI 没有入口。

7. **关键词覆盖**
   - `app_settings.profile` 覆盖 YAML 的 interests/notes；
   - `favorite_topics` 仍参与相关度，但 UI 不可编辑；
   - 保存关键词只重算分数，不重写旧分析文字/导读。

8. **没有 DB migration**
   - 新列在旧数据库上不会自动出现；
   - `read` 是遗留列，不要恢复使用。

9. **没有认证**
   - 不能公网暴露；
   - 多用户需要单独设计。

10. **云端入口依赖 SSH 隧道**
    - 没有公网 URL；
    - 本地需要保持 SSH 连接。

---

## 14. 下一步建议

### P0

1. 修改云端 SSH 密码，配置 key；
2. 观察 16:00 的真实导读生成，确认 `digest_status=ready`；
3. 给 scheduler 增加失败重试/退避和运行日志轮转；
4. 增加 service 编排测试（mock HF/LLM）。

### P1

5. 将 `favorite_topics` 与网页关键词统一；
6. 旧分析强制重建入口（显式按钮 + 二次确认）；
7. DB schema version / migration；
8. 媒体本地缓存/代理（避免 HF CDN 不稳定）；
9. 云端自动恢复（systemd 或容器启动脚本）。

### P2

10. 多用户认证、配额、独立 data 目录；
11. 全文 PDF 分析（默认关闭）；
12. 周报、主题订阅、跨日期搜索 UI。

---

## 15. 常用命令

### 本地

```bash
cd /home/lsc/work/huggingface-abstract
. .venv/bin/activate
python run.py --no-browser
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q -p pytest_asyncio.plugin
```

### 云端

```bash
ssh -p 13048 root@region-9.autodl.pro
screen -r paper-daily
curl -s http://127.0.0.1:8765/api/health
tail -f /root/autodl-tmp/paper-daily/paper-daily.log
```

### 入口

```bash
./connect-cloud.sh
# http://127.0.0.1:8765
```

---

## 16. 接手检查清单

- [ ] 确认云端 screen 仍在运行；
- [ ] `/api/health` 版本为 1.5.0；
- [ ] `fetch.hf_base_url` 为 hf-mirror（云端）；
- [ ] `schedule.enabled=true`；
- [ ] 16:00 后检查 digest；
- [ ] 修改 SSH 密码/配置 key；
- [ ] 不把 8765 暴露公网；
- [ ] 不要把 `config.yaml`、`data/`、`backup/` 提交；
- [ ] 修改提示词字段时同步 `prompts.py`、`llm.py`、`exporting.py`、前端和 `docs/prompt-v4.md`；
- [ ] 修改 schema 时先加 migration，并更新测试。
