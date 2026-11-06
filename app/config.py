from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when config.yaml is missing or invalid."""


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _integer(value: Any, default: int, minimum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    return number


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.splitlines() if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _resolve_env(value: Any) -> Any:
    """Resolve ${ENV_VAR} values recursively, including inside dictionaries."""
    if isinstance(value, dict):
        return {key: _resolve_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env(item) for item in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value.strip())
        if match:
            return os.environ.get(match.group(1), "")
    return value


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    timezone: str = "Asia/Shanghai"
    title: str = "Paper Daily"


@dataclass(slots=True)
class StorageConfig:
    data_dir: Path = BASE_DIR / "data"
    cache_pdfs: bool = True

    @property
    def database_path(self) -> Path:
        return self.data_dir / "paper_daily.db"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"


@dataclass(slots=True)
class ProfileConfig:
    name: str = ""
    role: str = "AI 研究者 / 工程师"
    output_language: str = "简体中文"
    interests: list[str] = field(default_factory=list)
    favorite_topics: list[str] = field(default_factory=list)
    avoid_topics: list[str] = field(default_factory=list)
    notes: str = ""

    def interest_terms(self) -> list[str]:
        terms: list[str] = []
        for block in [self.interests, self.favorite_topics]:
            for item in block:
                terms.extend(_split_terms(item))
        return _unique_lower(terms)


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = True
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-v4.1-flash-1017"
    allow_no_api_key: bool = False
    temperature: float = 0.25
    max_tokens: int = 8000
    timeout_seconds: int = 240
    concurrency: int = 3
    max_retries: int = 1
    reasoning_effort: str = ""
    json_mode: str = "auto"
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    system_prompt_extra: str = ""
    proxy: str = ""

    @property
    def is_configured(self) -> bool:
        if not self.enabled or not self.base_url or not self.model:
            return False
        if self.api_key:
            return True
        return self.allow_no_api_key

    @property
    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def masked_key(self) -> str:
        if not self.api_key:
            return ""
        if len(self.api_key) <= 8:
            return "••••••••"
        return f"{self.api_key[:4]}••••{self.api_key[-4:]}"


@dataclass(slots=True)
class FetchConfig:
    timeout_seconds: int = 30
    hf_base_url: str = "https://huggingface.co"
    detail_pages: bool = True
    detail_concurrency: int = 5
    max_papers: int = 100
    user_agent: str = "PaperDaily/1.0 (local personal research reader)"
    proxy: str = ""


@dataclass(slots=True)
class DigestConfig:
    enabled: bool = True
    top_n: int = 18


@dataclass(slots=True)
class ScheduleConfig:
    enabled: bool = False
    check_interval_minutes: int = 15
    digest_time: str = "16:00"
    run_on_start: bool = True


@dataclass(slots=True)
class Settings:
    path: Path
    server: ServerConfig
    storage: StorageConfig
    profile: ProfileConfig
    llm: LLMConfig
    fetch: FetchConfig
    digest: DigestConfig
    schedule: ScheduleConfig

    def ensure_directories(self) -> None:
        self.storage.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage.downloads_dir.mkdir(parents=True, exist_ok=True)

    def timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.server.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(
                f"时区 {self.server.timezone!r} 不可用，请改成例如 Asia/Shanghai。"
            ) from exc

    def now(self) -> datetime:
        return datetime.now(self.timezone())

    def today(self) -> str:
        return self.now().date().isoformat()

    def public_settings(self) -> dict[str, Any]:
        return {
            "config_path": str(self.path),
            "data_dir": str(self.storage.data_dir),
            "downloads_dir": str(self.storage.downloads_dir),
            "timezone": self.server.timezone,
            "today": self.today(),
            "profile": {
                "name": self.profile.name,
                "role": self.profile.role,
                "output_language": self.profile.output_language,
                "interests": self.profile.interests,
                "favorite_topics": self.profile.favorite_topics,
                "avoid_topics": self.profile.avoid_topics,
                "notes": self.profile.notes,
            },
            "schedule": {
                "enabled": self.schedule.enabled,
                "check_interval_minutes": self.schedule.check_interval_minutes,
                "digest_time": self.schedule.digest_time,
            },
            "llm": {
                "enabled": self.llm.enabled,
                "configured": self.llm.is_configured,
                "base_url": self.llm.base_url,
                "model": self.llm.model,
                "api_key_set": bool(self.llm.api_key),
                "api_key_masked": self.llm.masked_key,
                "reasoning_effort": self.llm.reasoning_effort,
                "concurrency": self.llm.concurrency,
            },
        }


def config_path() -> Path:
    configured = os.environ.get("HF_PAPER_CONFIG", "").strip()
    return Path(configured).expanduser().resolve() if configured else BASE_DIR / "config.yaml"


def load_settings(path: Path | None = None) -> Settings:
    path = (path or config_path()).resolve()
    if not path.exists():
        raise ConfigError(
            f"找不到配置文件：{path}\n请复制 config.example.yaml 为 config.yaml 后再启动。"
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml 格式错误：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml 的顶层必须是一个映射（key: value）。")
    raw = _resolve_env(raw)

    app_raw = raw.get("app") or {}
    storage_raw = raw.get("storage") or {}
    profile_raw = raw.get("profile") or {}
    llm_raw = raw.get("llm") or {}
    fetch_raw = raw.get("fetch") or {}
    digest_raw = raw.get("digest") or {}
    schedule_raw = raw.get("schedule") or {}

    if not isinstance(app_raw, dict):
        app_raw = {}
    if not isinstance(storage_raw, dict):
        storage_raw = {}
    if not isinstance(profile_raw, dict):
        profile_raw = {}
    if not isinstance(llm_raw, dict):
        llm_raw = {}
    if not isinstance(fetch_raw, dict):
        fetch_raw = {}
    if not isinstance(digest_raw, dict):
        digest_raw = {}
    if not isinstance(schedule_raw, dict):
        schedule_raw = {}

    data_dir_raw = _text(storage_raw.get("data_dir"), "./data") or "./data"
    data_dir = Path(data_dir_raw).expanduser()
    if not data_dir.is_absolute():
        data_dir = (BASE_DIR / data_dir).resolve()

    env_key = os.environ.get("HF_PAPER_LLM_API_KEY", "").strip()
    api_key = env_key or _text(llm_raw.get("api_key"))

    extra_headers_raw = llm_raw.get("extra_headers") or {}
    extra_headers = (
        {str(k): str(v) for k, v in extra_headers_raw.items()}
        if isinstance(extra_headers_raw, dict)
        else {}
    )
    extra_body_raw = llm_raw.get("extra_body") or {}
    extra_body = extra_body_raw if isinstance(extra_body_raw, dict) else {}

    server = ServerConfig(
        host=_text(app_raw.get("host"), "127.0.0.1"),
        port=_integer(app_raw.get("port"), 8765, minimum=1),
        open_browser=_boolean(app_raw.get("open_browser"), True),
        timezone=_text(app_raw.get("timezone"), "Asia/Shanghai"),
        title=_text(app_raw.get("title"), "Paper Daily"),
    )
    if server.host not in {"127.0.0.1", "localhost", "::1"}:
        # It still works, but it is a privacy-sensitive default worth surfacing.
        pass
    if server.port > 65535:
        raise ConfigError("app.port 必须小于 65536。")

    storage = StorageConfig(
        data_dir=data_dir,
        cache_pdfs=_boolean(storage_raw.get("cache_pdfs"), True),
    )
    profile = ProfileConfig(
        name=_text(profile_raw.get("name")),
        role=_text(profile_raw.get("role"), "AI 研究者 / 工程师"),
        output_language=_text(profile_raw.get("output_language"), "简体中文"),
        interests=_string_list(profile_raw.get("interests")),
        favorite_topics=_string_list(profile_raw.get("favorite_topics")),
        avoid_topics=_string_list(profile_raw.get("avoid_topics")),
        notes=_text(profile_raw.get("notes")),
    )
    json_mode = _text(llm_raw.get("json_mode"), "auto").lower()
    if json_mode not in {"auto", "always", "never"}:
        json_mode = "auto"
    llm = LLMConfig(
        enabled=_boolean(llm_raw.get("enabled"), True),
        base_url=_text(llm_raw.get("base_url"), "https://api.deepseek.com/v1"),
        api_key=api_key,
        model=_text(llm_raw.get("model"), "deepseek-v4.1-flash-1017"),
        allow_no_api_key=_boolean(llm_raw.get("allow_no_api_key"), False),
        temperature=max(0.0, min(2.0, _number(llm_raw.get("temperature"), 0.25))),
        max_tokens=_integer(llm_raw.get("max_tokens"), 8000, minimum=256),
        timeout_seconds=_integer(llm_raw.get("timeout_seconds"), 240, minimum=10),
        concurrency=_integer(llm_raw.get("concurrency"), 3, minimum=1),
        max_retries=_integer(llm_raw.get("max_retries"), 1, minimum=0),
        reasoning_effort=_text(llm_raw.get("reasoning_effort")).lower(),
        json_mode=json_mode,
        extra_headers=extra_headers,
        extra_body=extra_body,
        system_prompt_extra=_text(llm_raw.get("system_prompt_extra")),
        proxy=_text(llm_raw.get("proxy")),
    )
    fetch = FetchConfig(
        timeout_seconds=_integer(fetch_raw.get("timeout_seconds"), 30, minimum=5),
        hf_base_url=_text(fetch_raw.get("hf_base_url"), "https://huggingface.co").rstrip("/"),
        detail_pages=_boolean(fetch_raw.get("detail_pages"), True),
        detail_concurrency=_integer(
            fetch_raw.get("detail_concurrency"), 5, minimum=1
        ),
        max_papers=_integer(fetch_raw.get("max_papers"), 100, minimum=1),
        user_agent=_text(
            fetch_raw.get("user_agent"),
            "PaperDaily/1.0 (local personal research reader)",
        ),
        proxy=_text(fetch_raw.get("proxy")),
    )
    digest = DigestConfig(
        enabled=_boolean(digest_raw.get("enabled"), True),
        top_n=_integer(digest_raw.get("top_n"), 18, minimum=1),
    )
    digest_time = _text(schedule_raw.get("digest_time"), "16:00")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", digest_time):
        raise ConfigError("schedule.digest_time 必须是 HH:MM，例如 16:00。")
    schedule = ScheduleConfig(
        enabled=_boolean(schedule_raw.get("enabled"), False),
        check_interval_minutes=_integer(
            schedule_raw.get("check_interval_minutes"), 15, minimum=1
        ),
        digest_time=digest_time,
        run_on_start=_boolean(schedule_raw.get("run_on_start"), True),
    )

    settings = Settings(
        path=path,
        server=server,
        storage=storage,
        profile=profile,
        llm=llm,
        fetch=fetch,
        digest=digest,
        schedule=schedule,
    )
    settings.ensure_directories()
    # Validate timezone during load rather than at the first request.
    settings.timezone()
    return settings


GENERIC_TERMS = {
    "ai",
    "a.i.",
    "llm",
    "llms",
    "模型",
    "大模型",
    "人工智能",
    "研究",
    "论文",
    "应用",
}


def _split_terms(text: str) -> list[str]:
    """Split a Chinese/English interest line into useful matching terms."""
    text = text.strip()
    if not text:
        return []
    # Remove parenthetical descriptions but keep both full line and inner terms.
    inner = re.findall(r"[（(]([^）)]+)[）)]", text)
    base = re.sub(r"[（(][^）)]*[）)]", " ", text)
    base = base.replace("以及", "、").replace("及其", "、")
    chunks = re.split(r"[\s,，、;；/|·]+|与|和|及", base)
    terms: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip(" -—_")
        if len(chunk) >= 2 and chunk.lower() not in GENERIC_TERMS:
            terms.append(chunk)
    for item in inner:
        for piece in re.split(r"[\s,，、;；/|·]+|与|和|及", item):
            piece = piece.strip()
            if len(piece) >= 2 and piece.lower() not in GENERIC_TERMS:
                terms.append(piece)
    return terms


def _unique_lower(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def date_for_filename(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ConfigError(f"日期格式应为 YYYY-MM-DD，收到：{value}") from exc
