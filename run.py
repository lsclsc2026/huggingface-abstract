#!/usr/bin/env python3
"""Start the local Paper Daily server."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def open_browser_later(url: str, port: int, timeout: float = 15.0) -> None:
    def worker() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.3)
        try:
            if is_wsl():
                if _which("wslview"):
                    subprocess.Popen(["wslview", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(
                        ["cmd.exe", "/c", "start", "", url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                return
            webbrowser.open(url, new=2)
        except Exception:
            print(f"\n无法自动打开浏览器，请手动访问：{url}\n")

    threading.Thread(target=worker, daemon=True, name="open-browser").start()


def _which(command: str) -> bool:
    from shutil import which

    return which(command) is not None


def main() -> int:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    try:
        from app.config import ConfigError, load_settings
    except ImportError:
        print("缺少依赖。请先运行：python -m pip install -r requirements.txt")
        return 1

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"\n配置错误：{exc}\n")
        return 1

    parser = argparse.ArgumentParser(description="Paper Daily local server")
    parser.add_argument("--host", default=settings.server.host, help="监听地址")
    parser.add_argument("--port", type=int, default=settings.server.port, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    if args.host in {"0.0.0.0", "::"}:
        url = f"http://127.0.0.1:{args.port}"
    print("")
    print("  PAPER DAILY  ·  Hugging Face 论文晨报")
    print("  " + "─" * 45)
    print(f"  地址      {url}")
    print(f"  数据目录  {settings.storage.data_dir}")
    print(f"  配置文件  {settings.path}")
    print(f"  模型      {settings.llm.model if settings.llm.is_configured else '未配置（规则摘要）'}")
    print("  " + "─" * 45)
    if settings.schedule.enabled:
        print(f"  自动更新  每 {settings.schedule.check_interval_minutes} 分钟检查新文章；{settings.schedule.digest_time} 生成导读")
    else:
        print("  自动更新  未开启（schedule.enabled: false）")
    print("  按 Ctrl+C 停止。")
    print("", flush=True)

    if settings.server.open_browser and not args.no_browser:
        open_browser_later(url, args.port)

    try:
        import uvicorn

        from app.main import app

        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        print("\n已停止。")
        return 0
    except Exception as exc:
        print(f"\n启动失败：{exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
