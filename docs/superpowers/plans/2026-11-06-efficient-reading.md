# Efficient Live Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver approved detailed article format and short dynamic digest with fewer redundant model/network calls, then update the existing private cloud site while extraction remains paused.
**Architecture:** Keep FastAPI/SQLite/plainJS, add digest composition, bounded arXiv sources and persistent model-result/usage storage. Whole-day digest entries are deterministic; only overview invokes model. No historical batch rebuild.
**Tech Stack:** Python3.12, httpx, BeautifulSoup, SQLite, existing frontend.
**Spec:** docs/superpowers/specs/2026-11-06-efficient-reading-design.md

## Global Constraints
- No personal prompts, no comments, preserve private legacy data. All development in Git.
- Production extraction remains disabled. Temporary DB and fake HTTP/model for tests; no unattended paid model loop.
- Never invent venues/rankings, body mechanisms or metric meanings. Display original abstract verbatim from stored source.
- Preserve old analyses and digests; no automatic historical regeneration.

## Review Focus
- Same semantic input with different upvotes/order must not incur a second model call.
- Invalid JSON/HTTP failures cannot enter successful cache; retries bounded and measured, unknown usage explicit.
- Missing/malformed/arbitrary-ID sources fail safely; HTML unavailable clearly marked, no arbitrary remote URL fetch.
- New papers appear in all digest entries immediately even when short overview fails; outdated overview identified.
- Sidebar works desktop/mobile/keyboard, links open real details; page refresh/read never calls model.

### Task1: Sources, prompt schema and model/cache use
Files: app/paper_sources.py (new), app/model_store.py (new), app/prompts.py, app/llm.py, app/db.py, app/public.py; tests/test_efficient_reading.py (new), tests/test_sources.py (new), tests/test_llm.py.
- [x] Write source and new output/prompt/cache/usage failing tests; run red.
- [x] Implement bounded parsed sources, compact prompts, successful caching + single-flight + attempt ledger; normalize approved fields and preserve legacy compatibility.
- [x] Run relevant tests, commit.

### Task2: Dynamic digest and single-fetch scheduler
Files: app/briefing.py (new), app/service.py, app/main.py, app/db.py, app/exporting.py; tests/test_scheduler.py, tests/test_api.py.
- [x] Write tests for pre16 generation, new-content update, same-content reuse, one fetch/tick, fallback recovery and no-data behavior; observe red.
- [x] Implement stable content fingerprint and deterministic full-day groups; compact overview refresh, persistent cache, bounded retry. Update API/export/admin usage.
- [x] Run full tests; commit.

### Task3: Approved UI and collapse control
Files: app/static/app.js, app/static/index.html, app/static/styles.css.
- [x] Implement short digest/full article rows with open-paper buttons; exact detail order and metadata/evidence states, legacy content support; desktop collapse/persist, mobile drawer.
- [x] JS check and real Chromium test long papers, malicious text, all links, mobile/sidebar and no-model GET behavior; commit.

### Task4: Cloud delivery
Files: config.example.yaml, app/__init__.py, README.md, HANDOFF.md, AGENTS.md, docs/verification-v1.8.0-2026-11-06.md.
- [x] Set actual local/cloud config exact discovered model name and disabled thinking, keep schedule disabled, backup secrets privately.
- [x] Independent read-only review; fix major issues; full tests and browser verification.
- [x] Cloud backup/no-active guard, deploy, compare old data and config intent, actual browser + source match, document URL/source/start/build and limitations; push tested Git release.

## 交付结果

云端v1.8.0于2026-11-06 10:47部署并验收，78测试、独立复审、真实网关有限样本及真实云端浏览器通过；自动提取保持暂停，32篇中只重做H2RB，旧数据保留。详细证据及实际token用量见 docs/verification-v1.8.0-2026-11-06.md。
