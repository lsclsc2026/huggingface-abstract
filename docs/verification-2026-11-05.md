# Paper Daily 1.6.0 验收与部署记录

日期：2026-11-05，时区 Asia/Shanghai。

## 交付范围

完成用户确认的六项优先需求：日报同步完整性、每15分钟一轮检查、16:00后继续更新且成功导读固定、评论区占位、公共生成与个人相关度分离、Git/GitHub版本管理。多用户登录、真实评论和公网产品化暂缓。

用户已澄清，最初的“19”是点赞数，并非文章数。排查时实际源站从16篇增长到18篇，按当时真实列表验收。

## 自动化与审查

- 开发前基线：24 passed。
- 最终完整后端测试：**40 passed**（含新增16项用例）。
- `node --check app/static/app.js` 通过。
- `git diff --check` 通过。
- 独立只读代码审查无 Critical/Important 问题。修正两项 Minor：规则导读统一应用 top_n，以及移除共享导读中个人相关度门槛文案。另一项“仅元数据变化时不单独触发入库”记录为保留限制。
- 提交源码扫描未发现真实配置中的密钥或常见密钥格式；归档不包含 config.yaml、data/、backup/、conversation.md、.git/ 或临时验证文件。

主要回归：API非空但不完整、API被拒绝而网页可用、网页失败而API可用、来源全部失败、日期不符、镜像两个日期入口缓存不一致、不同画像下模型请求完全一致、忽略模型自评分、点赞和旧隐藏偏好不改变个人分数、个人设置更新不改公共分析/导读、15分钟边界、16:00前后行为、失败重试、禁用导读、中断后继续处理尚无分析的已入库论文。

## 真实浏览器

使用真实Chromium，未模拟页面/API。临时配置和数据库验证了1440×1000桌面及390×844移动视口：

- 列表与相关度正常展示；保存兴趣后立即重排。
- 改兴趣和风格备注未产生提取任务，公共分析及导读未改变。
- “评论区 / 评论功能暂未开放”正常，原私人笔记编辑器及保存入口消失，旧私人笔记没有显示为评论。
- 设置明确说明内容统一、兴趣仅影响相关度。
- 无横向溢出、无JavaScript运行错误。

随后经SSH隧道对**真实云端1.6.0**再次执行桌面/移动浏览器检查：18张论文卡片、评论占位、设置说明、移动端无横向溢出、无JS运行错误，全部通过。线上检查仅只读，没有修改用户设置或笔记。

## 云端部署

- 原服务的2篇新增论文分析完成后才切换版本，没有中断在途模型调用。
- 部署时间：**2026-11-05 14:43:16 +08:00**。
- 运行代码提交：`49752c0dd35cfb7b5c538f2ef4a11ee2587253d1`；后续发布提交只补充交接、开发约束和验收文档。
- 项目：`/root/autodl-tmp/paper-daily`，screen：`paper-daily`。
- 初次部署源码归档SHA256：`90c59314574cd7388a092cc5a58ded8cd0a8eb66862752853f702799d41d0d88`。
- 正式发布源码包基于 `v1.6.0`，附带最终文档；GitHub Release同时提供SHA256文件。
- 运行版本及发布元数据在云端 `.release.json` 中记录。

**部署前最新备份**：

```text
/root/autodl-tmp/backups/paper-daily-pre-v1.6.0-20261105-144314/
  source-and-config.tar.gz  # 旧源码与配置，权限受限，仅在云端
  paper_daily.db            # SQLite backup API生成的一致性快照，18篇
  metadata.json            # 内容/配置哈希，用于部署前后核对
```

14:44线上验收：

- `/api/health`：ok=true、version=1.6.0、llm_configured=true。
- 官方当天API/网页合并列表与线上18篇论文的**ID集合完全相等**。
- 数据库18篇 analysis_status 全部为 ok。
- scheduler enabled=true、check_interval_minutes=15、digest_time=16:00。
- 日志显示新调度器已启动，完成 `api=0 page=18 merged=18` 的真实镜像抓取。
- 分析正文、私人笔记和待读标记的原始数据库内容哈希与部署前备份完全相同；真实配置文件哈希也相同。
- 关键后端及前端文件SHA256与已验证本地提交一致。
- 检查时无进行中任务，digest_status=collecting（尚未到16:00）。

**16:00真实时钟到点行为尚未在本轮观察**，由受控时钟集成测试验证：前不生成、后生成一次、后续论文继续分析而已成功导读不变；生成失败15分钟后可重试。不得将受控时钟测试描述为已观察到真实线上16:00事件。

## GitHub 与回滚

私有仓库：<https://github.com/lsclsc2026/huggingface-abstract>。

- main：最新已验证版本；开发分支：fix/shared-content-and-sync。
- v1.5.0-baseline：开发前基线，保留回滚点。
- v1.6.0：本轮发布版本。源码下载：<https://github.com/lsclsc2026/huggingface-abstract/releases/tag/v1.6.0>。
- 后续开发必须按根目录 AGENTS.md 使用Git，不能直接改完云端代码后不留提交。

回滚源码可以用基线标签重新打包部署，也可以使用上述云端源码备份。执行前先等在途任务结束，再停止screen、解压旧源码、重启服务。例如在云端项目目录：

```bash
screen -S paper-daily -X quit
tar -xzf /root/autodl-tmp/backups/paper-daily-pre-v1.6.0-20261105-144314/source-and-config.tar.gz --exclude=config.yaml
screen -L -Logfile /root/autodl-tmp/paper-daily/paper-daily.log -dmS paper-daily .venv/bin/python run.py --no-browser
curl -fsS http://127.0.0.1:8765/api/health
```

本轮无schema变更。源码回滚不需要覆盖数据库；SQLite快照用于另行恢复数据，不应随源码回滚自动覆盖发布后的新增论文。

## 保留限制

- 当前相关度是固定关键词覆盖率和显式术语映射，**不是嵌入向量相似度**；不存在额外生成模型调用。
- 旧正文和已有导读可能含历史画像措辞；按约定保留，不自动重建、不重复花费模型费用。
- 只有点赞等元数据变化、没有新增/待分析文章时，scheduler不单独启动入库任务；元数据随下次提取刷新。
- 单用户、单进程、单worker；仍通过SSH隧道访问。登录、用户隔离、评论提交、容器重启后自动恢复暂未实现。
