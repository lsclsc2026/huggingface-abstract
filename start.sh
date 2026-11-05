#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3.10 或更高版本。"
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "需要 Python 3.10 或更高版本。当前版本：$("$PYTHON_BIN" --version 2>&1)"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "首次运行：正在创建虚拟环境…"
  "$PYTHON_BIN" -m venv .venv
fi

. .venv/bin/activate
if command -v sha256sum >/dev/null 2>&1; then
  REQ_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
else
  REQ_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
fi
MARKER=".venv/.requirements.sha256"
if [ ! -f "$MARKER" ] || [ "$(cat "$MARKER")" != "$REQ_HASH" ]; then
  echo "正在安装依赖…"
  python -m pip install --disable-pip-version-check -q -r requirements.txt
  printf '%s' "$REQ_HASH" > "$MARKER"
fi

exec python run.py "$@"
