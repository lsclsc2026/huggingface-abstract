# 云端部署与自动更新

> 目标：单用户、常驻运行、每 15 分钟检查 HF Daily，16:00 生成一次今日导读。  
> 暂不包含多用户登录、配额或权限隔离。

## 1. 推荐部署方式

不要用密码直接登录服务器。使用 SSH key，并关闭密码登录：

```bash
ssh-keygen -t ed25519
ssh-copy-id -p <port> root@<host>
```

然后把项目放到 `/opt/paper-daily`：

```bash
rsync -av --exclude='.venv' --exclude='data' --exclude='backup' \
  ./ root@<host>:/opt/paper-daily/
ssh -p <port> root@<host>
cd /opt/paper-daily
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

首次部署建议先把 `config.yaml` 的模型测试好，再启动服务。

## 2. systemd 常驻

```bash
cp deploy/paper-daily.service /etc/systemd/system/paper-daily.service
systemctl daemon-reload
systemctl enable --now paper-daily
systemctl status paper-daily
journalctl -u paper-daily -f
```

如果服务器是 AutoDL 等容器环境，可能没有 systemd；用 `tmux`：

```bash
cd /opt/paper-daily
tmux new -s paper-daily
.venv/bin/python run.py --no-browser --host 127.0.0.1
# Ctrl-B D 退出但保持运行
```

## 3. 单用户访问

服务默认监听 `127.0.0.1`，通过 SSH 隧道访问：

项目根目录提供了一个入口脚本：

```bash
./connect-cloud.sh
```

等价命令：

```bash
ssh -N -p 13048 -L 8765:127.0.0.1:8765 root@region-9.autodl.pro
```

保持终端打开，然后浏览器访问 `http://127.0.0.1:8765`。

不要直接把 8765 暴露到公网：当前没有登录和权限系统，任何访问者都能读取论文/笔记、触发模型调用并下载 PDF。以后要多用户，至少需要：

- 反向代理 + HTTPS；
- 登录/会话；
- 每用户独立 data 目录和 API 配额；
- 不能共用同一个 SQLite 文件。

## 4. 自动更新行为

`config.yaml`：

```yaml
schedule:
  enabled: true
  check_interval_minutes: 15
  digest_time: "16:00"
  run_on_start: true
```

- 每 15 分钟检查一次 HF Daily 是否有新文章；
- 有新文章才启动分析任务；
- 只分析没有 `analysis` 的新文章，已有分析不重复调用模型；
- 今日导读每天只在 16:00 后缺失时生成一次；
- 16:00 之后新文章仍会分析，但不会刷新已生成的今日导读；
- 页面打开时会每 60 秒轻量刷新，出现新文章/导读后自动更新。

scheduler 运行在 FastAPI 进程内，因此：

- 必须保持 `run.py` 常驻；
- 只能单 worker；
- 重启会中断当前任务，已完成的论文和任务记录仍保存在 SQLite。

## 5. 数据与备份

- 数据库：`data/paper_daily.db`；
- PDF：`data/downloads/`；
- 配置：`config.yaml`（含密钥，权限设为 600，不要提交）；
- `data/` 与 `config.yaml` 已被 `.gitignore` 忽略。

建议定期备份：

```bash
tar -czf /opt/paper-daily-backup-$(date +%F).tar.gz \
  -C /opt/paper-daily config.yaml data
```

## 6. 安全提醒

你给过的 SSH 密码不要在聊天、代码或仓库中长期保留。部署前建议：

1. 修改服务器密码；
2. 配置 SSH key；
3. 禁止 root 密码登录；
4. 不把 8765 直接暴露公网。
