# 同步与公共内容分离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成用户确认的六项优先需求并部署至现有云端、上传 GitHub。
**Architecture:** 保留现有单进程服务。按 ID 合并 API/网页；公共生成去个人画像，确定性相关度独立保存。
**Tech Stack:** Python / FastAPI / SQLite / 原生 JS / pytest / Git / GitHub CLI。
**Spec:** `docs/superpowers/specs/2026-11-05-sync-shared-content-design.md`

## Global Constraints
- 多用户认证与公网产品化暂缓；评论功能暂不开发。
- 每 15 分钟检查一次；16:00 后继续同步；成功导读固定。
- 不覆盖生产配置、数据库或下载文件；云端部署前备份。
- 所有代码由 Git 管理；不上传 conversation.md、配置密钥和用户数据。

## Review Focus
- API 非空但缺文章：合并网页并去重（Task 1）。
- 源站返回其他日期：拒绝错误日报（Task 1）。
- 热度或旧模型分数污染个人相关度：独立重算与展示（Task 2）。
- 用户偏好间接改变导读选文：公共排序和请求不变（Task 2）。
- 导读失败导致永久不再重试：按间隔重试且不阻塞更新（Task 3）。

### Task 1: 同步完整性
**Files:** `app/hf.py`, `tests/test_hf.py`
**Interfaces:** `HFClient.fetch_daily(day, enrich_details=None) -> list[dict]` 保持不变。
- [ ] 增加上述 HTTP 场景与日期归属回归测试，运行并观察现有实现失败。
- [ ] 合并来源并验证日期、统一 daily_date、修正镜像详情地址；运行 HF 测试。
- [ ] 提交 `fix: reconcile daily API and page paper lists`。

### Task 2: 公共生成与评分分离
**Files:** `app/prompts.py`, `app/llm.py`, `app/relevance.py`, `app/db.py`, `app/service.py`, `app/main.py`, `tests/test_llm.py`, `tests/test_api.py`, `tests/test_service.py`
**Interfaces:** `score_relevance(paper, profile) -> tuple[int,list[str]]` 固定规则；现有导入保持兼容。分析不含 relevance_score/highlight；评分仅存 papers.relevance。
- [ ] 写不同画像下提示词、公共输出一致测试，模型自评分被忽略、点赞不影响个人评分、关键词修改不改正文与导读测试；先观察失败。
- [ ] 移除画像输入，采用 shared-v5 版本；分离评分、固定导读排序，清除 API 中旧分析个人分数字段；修复前端分数来源。
- [ ] 运行 LLM/API/service 测试和完整套件；提交 `fix: separate shared analysis from personal relevance`。

### Task 3: 调度边界与失败重试
**Files:** `app/main.py`, `app/service.py`, `tests/test_scheduler.py`, `tests/test_service.py`
**Interfaces:** `Runtime._scheduler_tick()`、`ExtractionService.run(...)` 保持不变。
- [ ] 在临时配置/数据库验证 14:59 不检查、15:00 检查（相对间隔）、16:00 前不生成导读、后续新增分析而导读不变；导读失败后隔 15 分钟重试；保留已有分析。
- [ ] 用最近导读尝试时间取代当天永久阻断；禁用 digest 时不触发额外任务。任务成功文案准确说明实际导读状态。
- [ ] 完整测试通过后提交 `fix: retry digest generation without stopping paper updates`。

### Task 4: 前端与使用文档
**Files:** `app/static/app.js`, `README.md`, `HANDOFF.md`, `docs/prompt-v5.md`, `app/__init__.py`
**Interfaces:** API 路径不变；评论区无提交操作，旧笔记不公开。notes 仅保存为个人备注、不参与评分或生成。
- [ ] 替换评论区占位，设置页明确兴趣只影响相关度，风格不生效；更新版本为 1.6.0 与文档。
- [ ] `node --check app/static/app.js`，临时服务验证设置、列表和详情交互；完整 pytest。
- [ ] 提交 `feat: clarify shared content and reserve comments section`。

### Task 5: 发布、云端验证与 GitHub
**Files:** 部署文档和验收记录、源码归档（忽略、不入库）。
**Interfaces:** 已授权 SSH 部署与 lsclsc2026 GitHub 上传。
- [ ] 复查完整 diff 和提交文件密钥扫描；修复实际问题并重跑相关测试。
- [ ] 创建私有 GitHub 仓库 `huggingface-abstract`（若同名已存在则检查后复用，不覆盖远端历史），推送基线及开发分支和标签。
- [ ] 云端备份代码及 SQLite 一致性快照；等待现有任务结束，部署受版本管理的代码，保留配置及数据。
- [ ] 重启后核对版本、scheduler、增量文章及导读状态；记录未完成的 19 篇来源核对。
- [ ] 更新交接文档、推送最终提交并提供仓库链接和回滚版本。
