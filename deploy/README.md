# 公开阅读站部署（1.7.0）

目标：朋友打开HTTPS网址即可阅读，不在访客电脑运行程序；不注册、不登录。按用户最新决定，本版评论仅按IP区分访客，不接入短信、人工凭证或身份会话，用户身份及权限体系后续开发。评论功能代码已实现，默认关闭发布。无GPU需求，模型通过外部API调用。

## 当前资源状态

用户暂无域名/普通云服务器，本仓库未替用户购买资源。AutoDL继续用于个人验证，经SSH隧道访问；其[自定义服务协议](https://api.autodl.com/docs/service_agreement/)限定科研自用且不得向第三方转发链接，不把该入口作为公开网站。

主要读者在国内。可考虑普通CPU云服务器；2026-11-05核对的[阿里云官方99计划](https://www.aliyun.com/activity/ecs/99program)为2核2G、3M固定带宽、40G盘、99元/年（活动资格与最终价格以结算为准），域名另计；不需要GPU实例、托管数据库或付费SSL证书。模型API费用仍单独发生。这里只记录选型信息，不代购、不承诺库存/资格或永久免费资源。

使用中国内地服务器对外提供网站服务，需按接入商流程完成ICP备案，详情见[阿里云官方说明](https://www.alibabacloud.com/help/zh/icp-filing/basic-icp-service/product-overview/icp-filing-requirements-for-a-regular-website)。匿名阅读与匿名评论是两件事：国内公开跟帖评论需核对并满足后台身份认证等要求，IP/头像不能替代，见[网信办规定第四条](https://www.cac.gov.cn/2022-11/16/c_1670253725725039.htm)。`HF_PAPER_COMMENTS_ENABLED=false`是公开部署默认值，不等同于已完成评论上线资格确认。

## Docker Compose + Caddy（新服务器推荐配置）

前提：服务器已安装Docker Engine/Compose，域名解析指向服务器，80/443入站可用；国内公开服务相关准备已完成。

```bash
git clone https://github.com/lsclsc2026/huggingface-abstract.git
cd huggingface-abstract
cp config.example.yaml config.yaml
cp .env.example .env
mkdir -p data
python3 deploy/admin.py --token-file data/admin.token init-token
chmod 600 config.yaml .env
```

编辑config.yaml中的模型配置和抓取源；`storage.data_dir`保持`./data`，schedule.enabled=true、间隔15、digest_time=16:00。编辑`.env`的`PAPER_DAILY_DOMAIN`为真实域名，默认关闭评论。

容器应用以UID/GID 10001运行。在新部署目录由具备权限的管理员设置数据/配置权限：

```bash
sudo chown -R 10001:10001 data
sudo chown 10001:10001 config.yaml
sudo chmod 700 data
sudo chmod 600 config.yaml data/admin.token
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS https://你的域名/api/health
```

- app容器只在专用网络暴露8765，没有宿主机公网端口映射；Caddy暴露80/443，自动申请和续期HTTPS证书。
- 配置只读挂载，SQLite/评论/身份密钥/管理令牌持久化在data/；Caddy证书持久化在独立卷。
- 仅一个应用worker，禁止`--scale app=2`；该版本调度器仍在进程内。
- `restart: unless-stopped`用于机器/容器异常后的进程恢复；健康检查用于就绪状态和依赖启动，不代表Docker会自动重启一切“unhealthy”状态。
- 评论IP来自request.client；Caddy覆盖X-Forwarded-For，uvicorn只信任固定代理IP172.31.250.2。不能把可信代理设为`*`，不能给app再开放公网映射。修改网络时，`.env`中的代理IP与子网必须一致。
- 不直接套用其他CDN/代理提供的访客头。如果以后增加外层代理，需单独核对可信边界与真实来源IP。
- 全局请求体上限16KB，评论内容1–1000字。常规访问日志未启用，避免额外记录完整来源IP；排障日志仍应按实际配置管理。

## 原生服务（已有systemd服务器）

安装requirements.txt，设置好config.yaml与data/权限；生成管理令牌。复制`deploy/paper-daily.service`至systemd并按实际项目目录修改路径。服务默认监听127.0.0.1，代理使用`deploy/Caddyfile.native`，设置真实域名。应用仅信任本机代理。

```bash
sudo cp deploy/paper-daily.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now paper-daily
sudo systemctl status paper-daily
```

当前AutoDL容器没有systemd，不能把以上命令描述为已在AutoDL实现宿主机重启恢复。私有验证环境仍通过screen运行。

## 管理与评论开关

公开页面没有管理登录框或管理令牌。管理令牌是独立服务端秘密，不进入前端、Git、日志和命令行参数。

```bash
# 原生服务/SSH隧道入口
python3 deploy/admin.py --token-file data/admin.token status
python3 deploy/admin.py --token-file data/admin.token run --date 2026-11-05
python3 deploy/admin.py --token-file data/admin.token delete-comment 评论ID

# Docker公开HTTPS入口，管理者在服务器上读取受限令牌文件
sudo python3 deploy/admin.py --url https://你的域名 --token-file data/admin.token status
```

`run --refresh`会显式重建所有单篇分析，产生模型费用，普通访客无权限触发。`GET /api/settings`和模型测试/重载、提取、旧私人字段修改、任务详情、API文档与删除评论均要求Bearer管理令牌。管理令牌未配置时拒绝管理操作，不因为请求来自本机或声称X-Forwarded-For=127.0.0.1就放行。

`HF_PAPER_ADMIN_TOKEN_FILE`或`HF_PAPER_ADMIN_TOKEN`提供令牌，优先环境变量。轮换文件内容后新请求即使用新令牌，注意同步管理员的受限令牌文件。不要把令牌写进URL。

在隔离测试环境可以设置`HF_PAPER_COMMENTS_ENABLED=true`验证匿名评论；公开发布前先落实适用的评论身份核验方案，不能以该开关替代核验。配置更改后重建/重启应用进程。阅读一直无需登录。删除评论不重置发送限流；每来源IP15秒间隔、每小时20条。代理来源/NAT会使多用户共享限额。

## 一致性备份与恢复

```bash
sudo python3 deploy/backup.py --data-dir data --config config.yaml --output backup/paper-daily-20261105-153000.tar.gz
```

脚本使用SQLite backup API取得一致性快照，包含评论、限流和旧私人数据；同时备份稳定的`comment_identity.key`、管理令牌及PDF等数据，不复制活动WAL/SHM。归档权限600，禁止上传源码仓库或公开下载，目标已存在时拒绝覆盖。定期执行并把备份保存到另一台机器，保留多版本。

恢复时先停止应用，解压到全新的暂存目录检查`PRAGMA integrity_check`；确认后整体替换数据目录，保留恢复出的身份密钥，按实际运行UID恢复所有权，再启动应用。不要只覆盖主db而留下旧db-wal/db-shm；不要在运行中的数据目录直接解压。恢复数据库会回退之后的评论/任务，应与用户明确恢复点。代码回滚和数据恢复分开进行。

## 验收边界

交付前验证：公开API/导出无私人字段，管理请求未授权403，评论/XSS/分页/限流/删除测试、真实浏览器桌面与手机检查、备份恢复、单worker/持久化/代理边界。

本地未安装Docker的环境只能报告源码/YAML检查与原生应用验证；不得声称Docker镜像已构建或真实域名HTTPS已验收。服务器、域名和备案未落实前，公网网址及国内网络实测仍是待完成事项。
