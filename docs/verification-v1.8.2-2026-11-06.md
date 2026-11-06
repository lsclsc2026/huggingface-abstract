# 1.8.2 相邻句配色验收

2026-11-06 11:21（Asia/Shanghai）部署到现有AutoDL私有实例。

代码提交：224237ac18e7b315eb03e61632cab38c44f21b4e。部署源码归档SHA256：fede192a9b312298e23cbf69af2f3f0e80e7e2afc3fda5d39313450d00e2fa8e。

原因：旧颜色按照ID排序分配，与分组后的阅读顺序不同，导致邻句近似。现在按照实际显示顺序分配，色相连续间隔137.508度，HSL饱和度46%、亮度76%。编号按钮颜色和其他排版保持。

实际云端11月5日32篇Chromium验证（没有静态资源替换）：相邻CIE76 Lab色差最低32.07；两主题文本最低背景对比度7.72/7.43；32种颜色无重复，重载稳定，引用统一色，键盘打开对应详情，媒体仍在重点句与英文摘要之间，术语纵向排列；手机390px无横向溢出。无JS错误，无POST。JS语法、git diff --check和独立代码审查通过。没有后端逻辑改动，本轮未重复运行后端测试。

部署前备份：`/root/autodl-tmp/backups/paper-daily-pre-v1.8.2-20261106-111954/`，包括源码/配置、SQLite一致性快照及其他数据。部署后SQLite integrity_check通过；分析、摘要、历史导读、私人字段、调用记录及配置哈希不变。模型请求仍6次，本轮新增0次；自动提取暂停，无进行中任务。

回滚可用`v1.8.1`源码或上述备份的源码归档；先确认没有在途任务再重启，保留数据库和真实配置，不回写历史数据。

体验：运行`./connect-cloud.sh`后打开<http://127.0.0.1:8765>，选择11月5日。此地址依赖本机SSH隧道，不是公网网站。

源码：`/home/lsc/work/huggingface-abstract`；关键文件`app/static/app.js`、`app/static/index.html`。源码启动：`.venv/bin/python run.py --no-browser`。前端为原生JS/CSS，无构建步骤。私有GitHub仓库见README。
