# 1.7.0 阅读版发布验收（2026-11-05）

用户要求撤下评论，其他公开阅读改动部署至现有云端。不是新增公网入口。

## 本地与审查

- 完整后端59项测试通过，`node --check app/static/app.js`与`git diff --check`通过；本地真实Chromium桌面/手机检查通过。
- 新评论撤下回归先失败后通过：GET/POST评论均404，元数据/前端无评论，旧笔记和待读数据保留。
- 独立只读审查发现旧analysis隐私字段可影响搜索命中，真实数据库回归复现后改为公开字段白名单检索；最终回归包含该修复。
- 桌面与390×844浏览器实测列表/详情无待读、个人设置、相关度、评论区或评论请求；详情正常、无横向溢出或JS错误。未调用模型。

## 云端

部署前备份 `/root/autodl-tmp/backups/paper-daily-pre-v1.7.0-20261105-162622/`：旧源码及配置、SQLite一致性快照、其他私有数据、内容哈希metadata。最终备份22篇，10个completed任务，无在途任务。

部署于2026-11-05 16:26:24（北京时间），代码提交`315c47f673dfa27d612302a89bdfd3a008066280`，应用版本1.7.0。运行归档SHA256 `1dce1401d3b89459144034816aad5a0e006203d3f1f6653a82613f39c8d55afa`。后续仅文档提交不改变运行代码；最终Git标签v1.7.0。

- 健康接口ok=true，version=1.7.0；22篇analysis_status均为ok，digest_status=ready。
- 线上18个app文件逐一与已验证Git归档SHA256相等。
- SQLite integrity_check=ok；原始analysis、note、bookmarked、relevance、已有digest及真实config.yaml哈希与最终备份完全相等。
- 线上公开元数据和论文无私人字段；无令牌的settings、API文档、OpenAPI均403；通过服务器受保护令牌文件可以访问管理配置，令牌未输出。
- 评论API404。真实云端通过SSH隧道进入Chromium：22篇列表正常，打开详情正常；列表和详情无待读/个人设置/相关度/评论，未请求评论API；390×844布局无横向溢出，无JS异常。
- 调度配置enabled=true、间隔15分钟、digest_time=16:00；新进程日志确认调度启动，单worker、127.0.0.1:8765监听。

第一次切换因部署脚本从项目父目录运行、缺少项目模块路径而自动回滚，旧服务恢复。修复脚本后遇到旧服务启动自动更新，等待1篇新增分析结束，重新取得22篇一致性备份再切换。未中断该分析任务，21篇与22篇备份中的已有导读哈希一致。这是16:00以后新增同步而保留既有导读的实际观察，不声称观察到16:00整点触发。

## 回滚

保留v1.6.0标签和上述服务器私有备份。源码回滚先等在途任务结束、备份当前数据，再停止screen，恢复旧源码与其启动方式。回滚不覆盖config.yaml或data；不能直接用旧数据库覆盖当前数据库。

```bash
cd /root/autodl-tmp/paper-daily
# 先通过受保护的管理工具确认无进行中任务并备份。
.venv/bin/python deploy/admin.py --token-file data/admin.token status
screen -S paper-daily -X quit
tar -xzf /root/autodl-tmp/backups/paper-daily-pre-v1.7.0-20261105-162622/source-and-config.tar.gz --exclude=config.yaml
screen -L -Logfile /root/autodl-tmp/paper-daily/paper-daily.log -dmS paper-daily .venv/bin/python run.py --no-browser
curl -fsS http://127.0.0.1:8765/api/health
```

本次数据库仅新增兼容表，不删除原字段；旧版可读取。原始备份和admin.token均不上传GitHub。

## 限制

AutoDL仍为私有SSH隧道入口；没有购买域名/服务器，没有公网DNS/HTTPS验收，也没有Docker镜像构建验收。screen不能保证容器重启后自恢复。受控时钟测试不能代替真实时钟观察。
