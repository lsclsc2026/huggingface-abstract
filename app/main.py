from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import __version__
from .config import BASE_DIR, ConfigError, Settings, load_settings
from .db import Database
from .exporting import build_json, build_markdown
from .hf import HFClient, HFError, paper_download_name
from .llm import LLMClient, LLMError, score_relevance
from .service import ExtractionService, digest_due

LOGGER = logging.getLogger(__name__)
STATIC_DIR = BASE_DIR / "app" / "static"
PROFILE_SETTING_KEY = "profile"
MAX_KEYWORDS = 12
MIN_KEYWORD_LENGTH = 2
MAX_KEYWORD_LENGTH = 30
MAX_PROFILE_NOTES = 300


class Runtime:
    def __init__(self) -> None:
        self.started_at = datetime.now().astimezone()
        self.settings: Settings
        self.db: Database
        self.service: ExtractionService
        self.tasks: set[asyncio.Task[Any]] = set()
        self.scheduler_task: asyncio.Task[Any] | None = None
        self._last_check: datetime | None = None
        self._last_digest_attempt: datetime | None = None
        self.reload()

    def reload(self) -> None:
        active = self.db.active_run() if hasattr(self, "db") else None
        if active:
            raise RuntimeError("有提取任务正在进行，完成后再重新加载配置。")
        settings = load_settings()
        db = Database(settings.storage.database_path)
        db.init()
        self._apply_saved_profile(settings, db)
        self.settings = settings
        self.db = db
        self.service = ExtractionService(settings, db)
        # Re-score legacy records using the current fixed rule, without touching
        # shared analysis or issuing model requests.
        for paper in db.all_papers():
            db.update_relevance(paper["id"], score_relevance(paper, settings.profile)[0])

    @staticmethod
    def _apply_saved_profile(settings: Settings, db: Database) -> None:
        raw = db.get_setting(PROFILE_SETTING_KEY)
        if not raw:
            return
        try:
            saved = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        interests = saved.get("interests") if isinstance(saved, dict) else None
        notes = saved.get("notes") if isinstance(saved, dict) else None
        if isinstance(interests, list):
            cleaned = [str(item).strip() for item in interests if str(item).strip()]
            if cleaned:
                settings.profile.interests = cleaned
        if isinstance(notes, str):
            settings.profile.notes = notes

    def apply_profile(self, interests: list[str], notes: str) -> int:
        active = self.db.active_run()
        if active:
            raise RuntimeError("有提取任务正在进行，请完成后再修改关键词。")
        self.settings.profile.interests = interests
        self.settings.profile.notes = notes
        self.db.set_setting(
            PROFILE_SETTING_KEY,
            json.dumps({"interests": interests, "notes": notes}, ensure_ascii=False),
        )
        rescored = 0
        for paper in self.db.all_papers():
            score, _ = score_relevance(paper, self.settings.profile)
            self.db.update_relevance(paper["id"], score)
            rescored += 1
        return rescored

    def start(
        self, day: str, refresh: bool, *, sequential: bool = False
    ) -> tuple[dict[str, Any], bool]:
        active = self.db.active_run(day)
        if active:
            return active, True
        other = self.db.active_run()
        if other:
            raise HTTPException(
                status_code=409,
                detail=f"已有 {other['date']} 的提取任务正在进行，请等待完成。",
            )
        run = self.db.create_run(day, self.settings.llm.is_configured)
        task = asyncio.create_task(
            self.service.run(run["id"], day, refresh, sequential=sequential),
            name=f"paper-daily-{day}-{run['id'][:8]}",
        )
        self.tasks.add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            self.tasks.discard(completed)
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc:
                LOGGER.error("Background extraction crashed", exc_info=exc)

        task.add_done_callback(done)
        return run, False

    def start_scheduler(self) -> None:
        if not self.settings.schedule.enabled:
            LOGGER.info("Scheduler disabled by config")
            return
        if self.scheduler_task and not self.scheduler_task.done():
            return
        self._last_check = None if self.settings.schedule.run_on_start else self.settings.now()
        self.scheduler_task = asyncio.create_task(
            self._scheduler_loop(), name="paper-daily-scheduler"
        )

    async def _has_new_papers(self, day: str) -> bool:
        hf = HFClient(self.settings.fetch)
        try:
            papers = await hf.fetch_daily(day, enrich_details=False)
        except Exception as exc:
            LOGGER.warning("Scheduled HF check failed: %s", exc)
            return False
        existing = self.db.get_papers_for_date(day)
        existing_ids = {paper["id"] for paper in existing}
        return (
            any(not paper.get("analysis") for paper in existing)
            or any(paper["id"] not in existing_ids for paper in papers)
        )

    async def _scheduler_tick(self) -> None:
        if not self.settings.schedule.enabled:
            return
        now = self.settings.now()
        today = now.date().isoformat()
        interval = self.settings.schedule.check_interval_minutes * 60
        digest_window = digest_due(self.settings, today)
        digest_missing = not self.db.digest_for_day(today)
        active = self.db.active_run()
        retry_due = (
            self._last_digest_attempt is None
            or self._last_digest_attempt.date() != now.date()
            or (now - self._last_digest_attempt).total_seconds() >= interval
        )

        if (
            digest_window
            and digest_missing
            and retry_due
            and not active
        ):
            self._last_digest_attempt = now
            try:
                self.start(today, False, sequential=True)
                # A digest run also fetches the current daily list.
                self._last_check = now
                LOGGER.info("Scheduler started missing-digest run for %s", today)
            except Exception as exc:
                LOGGER.warning("Scheduler could not start digest run: %s", exc)
            return

        due = self._last_check is None or (now - self._last_check).total_seconds() >= interval
        if due and not active:
            self._last_check = now
            if await self._has_new_papers(today):
                try:
                    self.start(today, False, sequential=True)
                    LOGGER.info("Scheduler found new papers for %s", today)
                except Exception as exc:
                    LOGGER.warning("Scheduler could not start new-paper run: %s", exc)

    async def _scheduler_loop(self) -> None:
        LOGGER.info(
            "Scheduler started: every %s min, digest at %s",
            self.settings.schedule.check_interval_minutes,
            self.settings.schedule.digest_time,
        )
        await asyncio.sleep(5)
        try:
            while True:
                try:
                    await self._scheduler_tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Scheduler tick failed; retrying in 30s")
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            LOGGER.info("Scheduler stopped")
            raise

    async def shutdown(self) -> None:
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


def _runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="服务正在初始化，请稍后再试。")
    return runtime


def _valid_day(value: str) -> str:
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD。") from exc


def _content_disposition(filename: str) -> str:
    from urllib.parse import quote

    return f"attachment; filename*=UTF-8''{quote(filename)}"


class RunRequest(BaseModel):
    date: str | None = None
    refresh: bool = False


class PaperPatch(BaseModel):
    bookmarked: bool | None = None
    note: str | None = None


class ProfileUpdate(BaseModel):
    interests: list[str]
    notes: str | None = None


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        try:
            runtime = Runtime()
        except ConfigError as exc:
            LOGGER.error("Configuration error: %s", exc)
            raise
        app.state.runtime = runtime
        runtime.start_scheduler()
        LOGGER.info("Paper Daily ready · data=%s", runtime.settings.storage.data_dir)
        yield
        await runtime.shutdown()

    app = FastAPI(
        title="Paper Daily",
        description="Local Hugging Face Daily Papers reader with private-model summaries",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.middleware("http")
    async def headers_middleware(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        return {
            "ok": True,
            "version": __version__,
            "today": runtime.settings.today(),
            "llm_configured": runtime.settings.llm.is_configured,
        }

    @app.get("/api/meta")
    async def meta(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        active = runtime.db.active_run()
        return {
            "version": __version__,
            "today": runtime.settings.today(),
            "now": runtime.settings.now().isoformat(timespec="seconds"),
            "started_at": runtime.started_at.isoformat(timespec="seconds"),
            "settings": runtime.settings.public_settings(),
            "active_run": active,
        }

    @app.get("/api/settings")
    async def settings_api(request: Request) -> dict[str, Any]:
        return _runtime(request).settings.public_settings()

    @app.post("/api/settings/reload")
    async def reload_settings(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        try:
            runtime.reload()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": True,
            "message": "配置已重新加载。host/port 的修改需要重启程序。",
            "settings": runtime.settings.public_settings(),
        }

    @app.post("/api/settings/test")
    async def test_model(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        if not runtime.settings.llm.is_configured:
            raise HTTPException(
                status_code=400,
                detail="模型未配置完整：请填写 llm.api_key，或为本地模型设置 allow_no_api_key: true。",
            )
        started = time.monotonic()
        try:
            async with LLMClient(
                runtime.settings.llm, runtime.settings.profile
            ) as llm:
                reply = await llm.test_connection()
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "ok": True,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "model": runtime.settings.llm.model,
            "reply": reply,
        }

    @app.put("/api/profile")
    async def update_profile(request: Request, body: ProfileUpdate) -> dict[str, Any]:
        runtime = _runtime(request)
        interests: list[str] = []
        seen: set[str] = set()
        for raw in body.interests:
            item = str(raw).strip()
            if not item:
                continue
            if len(item) < MIN_KEYWORD_LENGTH or len(item) > MAX_KEYWORD_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"每个关键词需为 {MIN_KEYWORD_LENGTH}-{MAX_KEYWORD_LENGTH} 个字。",
                )
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            interests.append(item)
        if not interests:
            raise HTTPException(status_code=400, detail="请至少保留一个关注关键词。")
        if len(interests) > MAX_KEYWORDS:
            raise HTTPException(
                status_code=400, detail=f"最多只能填写 {MAX_KEYWORDS} 个关键词。"
            )
        notes = (body.notes or "").strip()
        if len(notes) > MAX_PROFILE_NOTES:
            raise HTTPException(
                status_code=400, detail=f"阅读偏好不能超过 {MAX_PROFILE_NOTES} 个字。"
            )
        try:
            rescored = runtime.apply_profile(interests, notes)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": True,
            "rescored": rescored,
            "settings": runtime.settings.public_settings(),
        }

    @app.get("/api/history")
    async def history(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        return {"days": runtime.db.list_days()}

    @app.get("/api/search")
    async def search(request: Request, q: str = "", limit: int = 80) -> dict[str, Any]:
        runtime = _runtime(request)
        query = q.strip()
        if len(query) < 2:
            return {"papers": [], "query": query}
        return {"papers": runtime.db.search(query, limit), "query": query}

    @app.get("/api/days/{day}")
    async def get_day(request: Request, day: str) -> dict[str, Any]:
        runtime = _runtime(request)
        day = _valid_day(day)
        papers = runtime.db.get_papers_for_date(day)
        digest = runtime.db.digest_for_day(day)
        due = digest_due(runtime.settings, day)
        if digest:
            digest_status = "ready"
        elif not papers:
            digest_status = "empty"
        elif due:
            digest_status = "pending"
        else:
            digest_status = "collecting"
        return {
            "date": day,
            "papers": papers,
            "digest": digest,
            "digest_status": digest_status,
            "digest_time": runtime.settings.schedule.digest_time,
            "summary": runtime.db.day_summary(day),
            "latest_run": runtime.db.latest_run(day),
            "active_run": runtime.db.active_run(day),
        }

    @app.post("/api/runs", status_code=202)
    async def create_run(request: Request, body: RunRequest) -> JSONResponse:
        runtime = _runtime(request)
        day = _valid_day(body.date or runtime.settings.today())
        if day > runtime.settings.today():
            raise HTTPException(status_code=400, detail="不能提取未来日期的论文。")
        try:
            run, reused = runtime.start(day, body.refresh)
        except HTTPException:
            raise
        except Exception as exc:
            LOGGER.exception("Could not start run")
            raise HTTPException(status_code=500, detail=f"无法启动任务：{exc}") from exc
        return JSONResponse(
            status_code=200 if reused else 202,
            content={"run": run, "reused": reused},
        )

    @app.get("/api/runs/active")
    async def active_run(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        return {"run": runtime.db.active_run()}

    @app.get("/api/runs/{run_id}")
    async def get_run(request: Request, run_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        run = runtime.db.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="任务不存在。")
        return {"run": run}

    @app.get("/api/papers/{paper_id}")
    async def get_paper(request: Request, paper_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        paper = runtime.db.get_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="论文不存在。")
        return {"paper": paper}

    @app.patch("/api/papers/{paper_id}")
    async def patch_paper(
        request: Request, paper_id: str, body: PaperPatch
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        if body.note is not None and len(body.note) > 10000:
            raise HTTPException(status_code=400, detail="笔记不能超过 10000 字。")
        paper = runtime.db.update_paper_flags(
            paper_id,
            bookmarked=body.bookmarked,
            note=body.note,
        )
        if not paper:
            raise HTTPException(status_code=404, detail="论文不存在。")
        return {"paper": paper}

    @app.get("/api/papers/{paper_id}/pdf")
    async def download_pdf(request: Request, paper_id: str) -> FileResponse:
        runtime = _runtime(request)
        paper = runtime.db.get_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="论文不存在。")
        cached = Path(paper.get("pdf_path") or "")
        if cached.is_file() and cached.stat().st_size > 1000:
            return FileResponse(
                cached,
                media_type="application/pdf",
                filename=paper_download_name(paper),
                content_disposition_type="attachment",
            )
        client = HFClient(runtime.settings.fetch)
        try:
            if runtime.settings.storage.cache_pdfs:
                target = await client.download_pdf(
                    paper, runtime.settings.storage.downloads_dir / paper["daily_date"]
                )
                runtime.db.set_pdf_path(paper_id, str(target))
                return FileResponse(
                    target,
                    media_type="application/pdf",
                    filename=paper_download_name(paper),
                    content_disposition_type="attachment",
                )
            temp_dir = runtime.settings.storage.data_dir / "tmp"
            target = await client.download_pdf(paper, temp_dir)
            return FileResponse(
                target,
                media_type="application/pdf",
                filename=paper_download_name(paper),
                content_disposition_type="attachment",
                background=BackgroundTask(_safe_unlink, target),
            )
        except HFError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/days/{day}/export.md")
    async def export_markdown(request: Request, day: str) -> Response:
        runtime = _runtime(request)
        day = _valid_day(day)
        papers = runtime.db.get_papers_for_date(day)
        if not papers:
            raise HTTPException(status_code=404, detail="这一天还没有可导出的论文。")
        digest = runtime.db.digest_for_day(day)
        content = build_markdown(day, papers, digest, runtime.settings)
        filename = f"hf-papers-{day}.md"
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    @app.get("/api/days/{day}/export.json")
    async def export_json(request: Request, day: str) -> Response:
        runtime = _runtime(request)
        day = _valid_day(day)
        papers = runtime.db.get_papers_for_date(day)
        if not papers:
            raise HTTPException(status_code=404, detail="这一天还没有可导出的论文。")
        digest = runtime.db.digest_for_day(day)
        content = build_json(day, papers, digest)
        filename = f"hf-papers-{day}.json"
        return Response(
            content=content.encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Any) -> JSONResponse:
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=404, content={"detail": "接口不存在。"})
        return JSONResponse(status_code=404, content={"detail": "页面不存在。"})

    return app


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


app = create_app()
