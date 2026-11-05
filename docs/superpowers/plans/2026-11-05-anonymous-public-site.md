# Anonymous Public Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 免费匿名阅读与评论，去除个人功能，提供可公网部署的版本。
**Architecture:** 保留单进程FastAPI/SQLite；独立评论表；公开响应白名单；管理Bearer授权；浏览器零账户。
**Tech Stack:** Python/FastAPI/SQLite/原生JS；Docker Compose与HTTPS代理。
**Spec:** docs/superpowers/specs/2026-11-05-anonymous-public-site-design.md

## Global Constraints
- 不注册登录，不开发小程序；不删除旧个人数据。
- 公共模型生成无个人兴趣；每15分钟一轮检查，16:00后持续更新。
- 未获授权不购买服务器/域名，不公开AutoDL自用服务链接。
- 先写失败回归测试再实现行为；用临时配置和数据库。

## Review Focus
- 所有公开路径包含导出/历史/搜索均不能泄露私人笔记/配置（Task1）。
- 管理路由缺省拒绝且不允许伪造转发头拿到权限（Task1）。
- 限流并发、删除和重启后仍有效；SQLite外键与旧数据保留（Task2）。
- 切换论文时异步评论不能写到其他论文/丢失草稿（Task3）。
- 可信代理、持久化密钥、备份恢复和单worker配置（Task4）。

### Task1: 公开API与管理边界
Files: app/public.py, app/security.py, app/main.py, app/config.py, app/service.py, app/exporting.py, tests/test_public_api.py。
- [ ] 为公开元数据/论文/详情/搜索/历史/导出脱敏、管理拒绝、授权管理、无相关度写入写失败测试。
- [ ] 白名单序列化，隔离管理路由，移除运行和设置中评分调用；保留旧数据供管理。
- [ ] 运行相关及全套测试，提交。

### Task2: 匿名评论
Files: app/comments.py, app/db.py, app/main.py, tests/test_comments.py。
Interface: GET/POST /api/papers/{id}/comments，DELETE /api/comments/{id}（管理）。
- [ ] 先测试正文校验/分页/真实IP脱敏/HMAC色/速率/删除/旧库保留，观察失败。
- [ ] 新增表和SQLite原子限流，稳定密钥文件0600，公开评论序列化，失败不泄露IP。
- [ ] 相关测试通过，提交。

### Task3: 公共阅读前端
Files: app/static/index.html, app/static/app.js, app/static/styles.css, app/__init__.py。
- [ ] 移除待读、相关度、兴趣、模型设置、提取操作；保留阅读/搜索/导出/状态刷新。
- [ ] 评论列表、分页、纯文本发布、时间、头像、失败保留草稿/发送状态；异步切换防串。
- [ ] JS检查及真实浏览器桌面/移动验证，提交。

### Task4: 部署与交付
Files: Dockerfile, compose.yaml, .dockerignore, deploy/nginx.conf, deploy/README.md, deploy/backup.py, deploy/admin.py, HANDOFF.md, AGENTS.md, README.md。
- [ ] 令牌/身份密钥持久化、仅代理入站、自动恢复、HTTPS配置、管理及备份工具。
- [ ] 完整测试和独立只读审查；代码/归档扫描；推送GitHub。
- [ ] 现有私有云环境备份后更新验证，提供版本和备份证据；公网域名/服务器待用户决定，不能声称已公网交付。
