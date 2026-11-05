#!/usr/bin/env bash
# 在本地电脑运行；保持此终端不要关闭。
# 然后把浏览器打开 http://127.0.0.1:8765
set -euo pipefail
ssh -N \
  -o ServerAliveInterval=30 \
  -p 13048 \
  -L 8765:127.0.0.1:8765 \
  root@region-9.autodl.pro
