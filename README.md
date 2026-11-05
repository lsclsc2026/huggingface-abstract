# Paper Daily · HF论文晨报

从Hugging Face Daily Papers同步每日论文，生成统一的中文单篇分析与今日导读，提供网页阅读、搜索、往期回顾、Markdown/JSON导出和PDF入口。当前版本 **1.7.0**。

访客无需登录。当前界面不提供待读、个人关键词/兴趣设置、相关度或评论。旧私人数据留在数据库，不进入公开输出，也不用于搜索或排序。

## 同步与内容

- 每15分钟检查一次当天列表，合并API和网页来源；只分析新增/未完成的论文。
- 16:00后继续更新论文；今日导读成功后固定，失败可重试。
- 单篇分析与导读不接收个人兴趣、风格或身份；所有读者看到相同的内容。
- 页面每60秒刷新当天状态；访客刷新不会直接触发模型重建。
- 历史正文保留，不自动重建或重复产生模型费用。

## 开发运行

Python 3.11以上，推荐3.12：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.example.yaml config.yaml
.venv/bin/python run.py --no-browser
```

在真实`config.yaml`中设置外部模型API和抓取来源；不要提交密钥。默认SQLite和下载位于`data/`，访客无需安装Python或运行代码。

## 当前云端访问

当前AutoDL仅提供私有验证入口。具备服务器SSH凭据的电脑执行：

```bash
ssh -N -o ServerAliveInterval=30 -p 13048 -L 8765:127.0.0.1:8765 root@region-9.autodl.pro
```

保持终端连接，浏览器访问 <http://127.0.0.1:8765>。仓库中的`./connect-cloud.sh`执行同样操作。网页无需登录，SSH认证用于连接私有服务器。刷新旧页面可使用Ctrl+F5（macOS使用Command+Shift+R）。

云端项目目录`/root/autodl-tmp/paper-daily`，screen会话`paper-daily`，仅监听本机8765。screen不能保证容器重启后自恢复。准确部署提交及备份见 [HANDOFF.md](HANDOFF.md) 与 [发布验收](docs/verification-v1.7.0-2026-11-05.md)。

## 公网部署与管理

Docker Compose+Caddy、原生systemd、HTTPS、持久化、健康检查和备份说明见 [deploy/README.md](deploy/README.md)。当前没有域名或普通云服务器，不能把开发模板视为已公开上线。

管理令牌通过`HF_PAPER_ADMIN_TOKEN_FILE`或`HF_PAPER_ADMIN_TOKEN`提供；缺少有效令牌时，配置、模型测试、手动提取、旧私人字段修改、任务详情和API文档均拒绝访问。令牌不进入前端、不放URL、不提交Git。示例：

```bash
python3 deploy/admin.py --token-file data/admin.token init-token
python3 deploy/admin.py --token-file data/admin.token status
python3 deploy/backup.py --data-dir data --config config.yaml --output /安全目录/paper-daily-backup.tar.gz
```

备份含私有数据及密钥，禁止公开上传；代码回滚和数据恢复分别处理。

## 验证与版本管理

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -p pytest_asyncio.plugin
node --check app/static/app.js
git diff --check
```

所有修改通过Git分支、提交和GitHub保存，保留发布标签供回滚。`config.yaml`、`.env`、`data/`、日志、备份和聊天记录不纳入源码仓库。
