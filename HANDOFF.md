# Paper Daily — 项目交接

版本：**1.6.0**。更新时间：2026-11-05（Asia/Shanghai）。
目录：`/home/lsc/work/huggingface-abstract`。

## 当前优先级

用户已确认优先完成：同步完整性、15 分钟检查语义、16:00 导读固定但继续更新论文、评论区占位、公共生成与个人评分分离、Git/GitHub 管理。
多用户登录、账户数据库、真实评论功能和公网产品化**暂缓**。当前仍是单用户服务，经 SSH 隧道访问。

用户已澄清：HF 页面看到的“19”是点赞数，不是论文编号或篇数。排查时官方 API、官方日期页与镜像网页均返回 16 篇，云端当时有 15 篇；最终以源站当天列表为准。

## 本轮行为

- 每 15 分钟检查一次日报列表。不是每 15 分钟分析一篇。
- API、`/papers/date/YYYY-MM-DD` 和 `/papers?date=YYYY-MM-DD` 参与核对，兼容镜像不同入口缓存滞后；按 ID 合并去重、验证网页日期。任一路正常可保留结果，全部失败明确报错。
- 日报归属使用请求日期，原始发布时间另存。镜像详情抓取也使用镜像地址，展示链接仍指向 HF 官方。
- 只分析没有 analysis 的论文，已完成内容不重复调用模型。重启中断后，已入库但尚未分析的论文会在后续周期继续处理。
- 16:00 前收集论文；16:00 后生成一次导读。失败按检查间隔重试，成功后不再随新增论文改写。
- 16:00 后照常同步和分析新论文。
- 前端每 60 秒轮询当天状态。
- 评论区显示“评论功能暂未开放”，无发布按钮或输入框。旧私人笔记保留在数据库及原导出接口中，不会转为评论。

## 公共内容与个人评分

**公共内容**：统一简体中文科普风格，不传入用户身份、兴趣、备注或语言偏好。单篇分析不存个人 relevance_score/highlight。导读按公共社区热度、ID 排序选最多 digest.top_n 篇（默认 18），不使用个人相关度。

**个人分数**：`app/relevance.py` 的固定规则：`round(100 × 匹配兴趣词数 / 兴趣词总数)`。只匹配标题、摘要、HF 摘要与关键词；包含版本化中英文术语映射。点赞、模型自评分、旧 favorite_topics/avoid_topics、风格备注不参与计算。分数是文本覆盖率，不是论文质量或概率。暂未引入嵌入模型/向量数据库。

**改设置**：只更新数据库 relevance 列及列表排序，不改公共分析和导读，不触发模型。notes 改为“个人备注”，仅保存。旧 profile 字段继续兼容读取，但不送入生成提示词。

**历史内容**：保留旧正文和已有导读，不自动重建、不自动重复花费模型费用。旧分析里的个人分数字段在 API 读取时隐藏。新增内容提示词版本：beginner-shared-v5 / digest-shared-v5 / heuristic-shared-v5 / digest-heuristic-shared-v5。

## 架构与文件

Python 3.10+、FastAPI、Uvicorn、SQLite WAL、原生 HTML/CSS/JS。单进程、单 worker、同一时刻一个提取任务。无新依赖，无 schema 改动。

- `app/hf.py`：API/网页核对、日期校验、详情和媒体。
- `app/relevance.py`：独立固定评分及术语映射。
- `app/prompts.py`：共享提示词及 public_paper_order。
- `app/llm.py`：模型请求、归一化、规则降级；兼容导出 score_relevance。
- `app/db.py`：分析与 relevance 分开写入，读取旧分析时去个人分数字段。
- `app/service.py`：增量分析、共享选文、一次导读。
- `app/main.py`：调度、失败重试、启动/改设置时固定规则重算。
- `app/static/app.js`：相关度从 paper.relevance 读取，评论占位和设置说明。
- `docs/prompt-v5.md`：公共内容契约。
- `docs/superpowers/specs/2026-11-05-sync-shared-content-design.md`：本轮设计。
- `docs/superpowers/plans/2026-11-05-sync-shared-content.md`：实施计划。

数据库仍是 papers / runs / app_settings。论文、分析、相关度、旧笔记、待读和时间戳在 papers；导读及任务时间戳在 runs；个人兴趣在 app_settings.profile。API 路径保持原样，详见 `/api/docs`。

## Git 与 GitHub

仓库：<https://github.com/lsclsc2026/huggingface-abstract>（私有）。

- `main`：经过验证的最新版本；发布时从开发分支快进合并。
- `v1.5.0-baseline`：开发前基线标签。
- `fix/shared-content-and-sync`：本轮开发分支。
- 每个后续改动都必须使用 Git；先确认状态，在分支上开发，验证后提交，及时推送。
- `.gitignore` 排除真实 config.yaml、data/、backup/、conversation.md、日志、构建归档和开发临时文件。
- 不要上传含密钥配置、数据库或旧聊天。代码回滚用 Git；生产数据单独备份，不能靠源码标签恢复。

```bash
git status --short --branch
git log --oneline --decorate -10
git diff v1.5.0-baseline..fix/shared-content-and-sync
```

## 云端

SSH：`ssh -p 13048 root@region-9.autodl.pro`。
项目：`/root/autodl-tmp/paper-daily`；venv：`.venv`；screen 会话：`paper-daily`。
日志：`paper-daily.log`；仅监听 `127.0.0.1:8765`。
云端用 `fetch.hf_base_url: https://hf-mirror.com`；schedule.enabled=true；间隔 15；导读时间 16:00。

本地 `./connect-cloud.sh`，保持终端连接，再访问 <http://127.0.0.1:8765>。

```bash
cd /root/autodl-tmp/paper-daily
screen -list
curl -fsS http://127.0.0.1:8765/api/health
```

部署前需备份源码、配置和 SQLite 一致性快照；只部署受 Git 管理的源码，不覆盖 config.yaml、data/、下载或旧备份。准确的部署提交、备份位置与线上验收结果见本轮 `docs/verification-2026-11-05.md`（发布时生成）。

## 验证

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -p pytest_asyncio.plugin
node --check app/static/app.js
```

原基线 24 项测试通过。本轮新增 HTTP 合并、单源失败、日期、公共请求与输出隔离、个人设置只改分数、调度间隔、16:00 边界、导读失败重试和中断恢复测试。最终数量及真实浏览器检查见发布验收记录。

## 保留限制与后续事项

- 暂无认证和用户隔离，不能作为公开多用户站点使用。
- scheduler 依赖进程常驻；AutoDL 容器无 systemd，screen 不保证宿主/容器重启后自恢复。
- 关键词匹配有语义覆盖局限，后续可替换为缓存的论文向量与每用户向量比较。
- digest.top_n=18 是共享导读选文上限，不是抓取上限；抓取默认 max_papers=100。
- 历史旧正文可能含旧画像措辞，保留原样；若需重建，应另行明确范围和模型费用。
- 无全文 PDF 分析；模型或网络失败保留规则摘要降级路径。
- 定时检查若只有点赞等元数据变化、没有新增或待分析文章，不单独启动入库任务；元数据将在后续提取任务中刷新。
- 后续多用户需把兴趣、分数、待读与评论按 user_id 隔离，公共论文/分析/导读继续共享；不能恢复将个人分数写入公共分析的旧做法。

## 2026-11-05 公开版开发中的最新范围

当前开发分支 `feat/anonymous-public-site`；尚未作为正式版本部署，云端仍为 v1.6.0。分支已实现公共响应过滤、隐藏待读/兴趣/相关度、IP匿名评论与部署模板。此次用户明确将身份核验、登录及用户权限体系延期：本版评论不需要短信、人工核验凭证或身份会话；仅用来源IP区分访客、生成头像色，显示脱敏IP和时间，保留限流与管理员删除保护。尚未发布的身份核验代码已撤回。

暂无域名及普通云服务器；AutoDL保持私有验证入口，不向第三方公开其服务链接。部署模板评论开关默认关闭，国内公开启用仍需落实适用的身份认证等要求，详见 `deploy/README.md`。本次功能决定不代表已公开上线。当前分支完整发布、独立审查与云端部署验收尚待完成。

此次调整验证：65项后端测试通过，`node --check app/static/app.js`与`git diff --check`通过；临时配置禁用模型/调度的Chromium真实交互通过免登录POST 201、限流429保留草稿、纯文本XSS、IP脱敏、重载头像色稳定、论文间评论隔离和移动无横向溢出。完整回归发现并修复此前清理导入时遗漏`HFClient`导致定时检查NameError的问题。以上不是公网、真实HTTPS或Docker镜像构建验收。
