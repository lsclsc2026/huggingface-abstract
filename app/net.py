from __future__ import annotations

import os
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _no_proxy_entries() -> list[str]:
    raw = ""
    for key in ("NO_PROXY", "no_proxy"):
        if os.environ.get(key):
            raw = os.environ[key]
            break
    entries: list[str] = []
    for item in raw.split(","):
        value = item.strip().lower()
        if value:
            entries.append(value)
    return entries


def _entry_host(entry: str) -> str:
    if entry.startswith("["):
        end = entry.find("]")
        return entry[1:end] if end != -1 else entry.strip("[]")
    if "://" in entry:
        return (urlparse(entry).hostname or "").lower()
    # A bare IPv6 address contains multiple colons and no port separator.
    if entry.count(":") > 1:
        return entry
    return entry.split(":", 1)[0]


def should_bypass_proxy(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host or host in LOCAL_HOSTS or host.startswith("127."):
        return True
    for entry in _no_proxy_entries():
        if entry == "*":
            return True
        candidate = _entry_host(entry).lstrip(".")
        if not candidate:
            continue
        if host == candidate or host.endswith("." + candidate):
            return True
    return False


def proxy_for(url: str, explicit: str = "") -> str | None:
    """Resolve a proxy without asking httpx to parse the process NO_PROXY string.

    Some environments provide bracketed IPv6 values such as ``[::1]`` in
    NO_PROXY. Older httpx releases parse that entry as a URL and crash with
    ``Invalid port``. We handle the bypass list ourselves and keep httpx on an
    explicit proxy.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    if should_bypass_proxy(url):
        return None
    scheme = (urlparse(url).scheme or "https").lower()
    if scheme == "https":
        candidates = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
    else:
        candidates = ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
    for key in candidates:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None
