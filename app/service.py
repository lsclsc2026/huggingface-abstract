from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import Settings
from .db import Database, utc_now
from .hf import HFClient, HFError
from .llm import LLMClient, LLMError, heuristic_analysis, heuristic_digest
from .prompts import public_paper_order
from .relevance import score_relevance

LOGGER = logging.getLogger(__name__)


def digest_due(settings: Settings, day: str) -> bool:
    if not settings.digest.enabled:
        return False
    today = settings.today()
    if day < today:
        return True
    if day > today:
        return False
    hh, mm = (int(part) for part in settings.schedule.digest_time.split(":"))
    now = settings.now()
    return (now.hour, now.minute) >= (hh, mm)


class ExtractionService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._progress_lock = asyncio.Lock()

    async def run(
        self,
        run_id: str,
        day: str,
        refresh: bool = False,
        *,
        sequential: bool = False,
    ) -> None:
        run = self.db.get_run(run_id)
        if not run:
            LOGGER.error("Run %s disappeared", run_id)
            return
        llm_configured = self.settings.llm.is_configured
        breaker = _Breaker()
        papers: list[dict[str, Any]] = []
        try:
            self.db.update_run(
                run_id,
                status="running",
                stage="fetching",
                message="正在连接 Hugging Face，读取 Daily Papers…",
            )
            hf = HFClient(self.settings.fetch)
            papers = await hf.fetch_daily(day)
            if not papers:
                self.db.update_run(
                    run_id,
                    status="completed",
                    stage="done",
                    total=0,
                    processed=0,
                    failed=0,
                    message="这一天 Hugging Face Daily Papers 没有收录论文（可能是周末或节假日）。",
                    finished_at=utc_now(),
                )
                return

            self.db.upsert_papers(papers)
            all_papers = self.db.get_papers_for_date(day)
            for paper in all_papers:
                self.db.update_relevance(paper["id"], score_relevance(paper, self.settings.profile)[0])
            # Incremental by default: only papers without an analysis are sent
            # to the model. refresh=True is kept for explicit full rebuilds.
            to_process = [
                paper for paper in all_papers
                if refresh or not paper.get("analysis")
            ]
            total = len(to_process)
            self.db.update_run(
                run_id,
                total=total,
                processed=0,
                failed=0,
                stage="analyzing" if total else "digest",
                message=(
                    f"发现 {total} 篇新文章，开始逐篇分析…"
                    if total
                    else "没有发现需要分析的新文章。"
                ),
            )
            existing_digest = self.db.digest_for_day(day)
            digest_due = self._digest_due(day)

            processed = 0
            failed = 0
            changed = 0
            lock = asyncio.Lock()

            async def process(paper: dict[str, Any]) -> None:
                nonlocal processed, failed, changed
                existing = paper.get("analysis")
                error = ""
                if existing and not refresh:
                    source = existing.get("source", "existing")
                elif llm_configured and not breaker.disabled:
                    try:
                        analysis = await llm.summarize_paper(paper)
                        async with lock:
                            breaker.reset()
                        self.db.update_paper_analysis(
                            paper["id"], analysis, "ok", "llm"
                        )
                        source = "llm"
                        async with lock:
                            changed += 1
                    except LLMError as exc:
                        error = str(exc)
                        LOGGER.warning("LLM failed for %s: %s", paper["id"], error)
                        async with lock:
                            breaker.record_failure(error)
                        fallback = heuristic_analysis(paper, self.settings.profile)
                        self.db.update_paper_analysis(
                            paper["id"],
                            fallback,
                            "failed",
                            "fallback",
                            error,
                        )
                        source = "fallback"
                        async with lock:
                            failed += 1
                else:
                    fallback = heuristic_analysis(paper, self.settings.profile)
                    reason = (
                        breaker.reason
                        if llm_configured and breaker.disabled
                        else "未配置可用的私有模型"
                    )
                    self.db.update_paper_analysis(
                        paper["id"],
                        fallback,
                        "fallback" if not error else "failed",
                        "fallback",
                        reason,
                    )
                    source = "fallback"
                    if breaker.disabled:
                        async with lock:
                            failed += 1

                async with lock:
                    processed += 1
                    message = f"正在分析 {processed}/{total}"
                    if source == "llm":
                        message += " · 已完成模型摘要"
                    else:
                        message += " · 规则摘要"
                    if error:
                        message += "（模型调用失败，已降级）"
                    self.db.update_run(
                        run_id,
                        processed=processed,
                        failed=failed,
                        message=message,
                        stage="analyzing",
                    )

            if llm_configured:
                async with LLMClient(
                    self.settings.llm, self.settings.profile
                ) as llm:
                    concurrency = 1 if sequential else self.settings.llm.concurrency
                    semaphore = asyncio.Semaphore(concurrency)

                    async def guarded(paper: dict[str, Any]) -> None:
                        async with semaphore:
                            await process(paper)

                    await asyncio.gather(*(guarded(paper) for paper in to_process))
                    breaker_note = (
                        f"；连续 {breaker.failures} 次模型调用失败，后续论文已跳过模型：{breaker.reason}"
                        if breaker.disabled
                        else ""
                    )
                    digest = None
                    if digest_due and not existing_digest and not breaker.disabled:
                        self.db.update_run(
                            run_id,
                            stage="digest",
                            message="到达日报导读时间，正在生成一次今日导读…",
                        )
                        try:
                            top_n = max(1, self.settings.digest.top_n)
                            analyzed = self.db.get_papers_for_date(day)
                            top_papers = public_paper_order(analyzed)[:top_n]
                            digest = await llm.generate_digest(day, top_papers)
                            self.db.set_digest(run_id, digest)
                        except LLMError as exc:
                            LOGGER.warning("Digest generation failed: %s", exc)
                            digest = None
                    elif existing_digest:
                        digest = existing_digest
                    if total == 0:
                        message = (
                            "没有发现新文章。"
                            if not digest_due
                            else "没有发现新文章，已检查今日导读状态。"
                        )
                    else:
                        message = self._completion_message(
                            total, failed, llm_configured, breaker_note
                        )
            else:
                # Without a model there is no need to open a network client.
                semaphore = asyncio.Semaphore(1 if sequential else 4)

                async def guarded_fallback(paper: dict[str, Any]) -> None:
                    async with semaphore:
                        await process(paper)

                await asyncio.gather(*(guarded_fallback(paper) for paper in to_process))
                digest = None
                if digest_due and not existing_digest:
                    digest = heuristic_digest(
                        day,
                        public_paper_order(self.db.get_papers_for_date(day))[
                            :max(1, self.settings.digest.top_n)
                        ],
                        self.settings.profile,
                    )
                    self.db.set_digest(run_id, digest)
                elif existing_digest:
                    digest = existing_digest
                if total == 0:
                    message = "没有发现新文章。"
                else:
                    message = self._completion_message(total, failed, False, "")

            if digest:
                message += " 今日导读已保留。" if existing_digest else " 今日导读已生成。"
            elif self.settings.digest.enabled:
                if not digest_due:
                    message += f" 今日导读将在 {self.settings.schedule.digest_time} 后生成。"
                elif self.settings.schedule.enabled:
                    message += " 今日导读尚未生成，后台将按检查间隔重试，也可手动刷新。"
                else:
                    message += " 今日导读尚未生成，可手动刷新重试。"

            self.db.update_run(
                run_id,
                status="completed",
                stage="done",
                processed=total,
                failed=failed,
                message=message,
                finished_at=utc_now(),
            )

        except HFError as exc:
            self.db.update_run(
                run_id,
                status="failed",
                stage="failed",
                message=str(exc),
                finished_at=utc_now(),
            )
        except asyncio.CancelledError:
            self.db.update_run(
                run_id,
                status="interrupted",
                stage="stopped",
                message="任务已停止。",
                finished_at=utc_now(),
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            LOGGER.exception("Extraction failed for %s", day)
            self.db.update_run(
                run_id,
                status="failed",
                stage="failed",
                message=f"提取失败：{exc}",
                finished_at=utc_now(),
            )

    def _digest_due(self, day: str) -> bool:
        return digest_due(self.settings, day)

    def _completion_message(
        self, total: int, failed: int, llm_configured: bool, note: str
    ) -> str:
        if not llm_configured:
            return (
                f"已完成 {total} 篇论文的抓取与本地规则摘要。"
                "在 config.yaml 配置私有模型后，重新提取即可获得中文深度总结。"
            )
        if failed:
            return (
                f"已完成 {total} 篇论文，其中 {failed} 篇模型总结失败并已降级。"
                f"可检查配置后点击「刷新今日」。{note}"
            ).strip()
        return f"已完成 {total} 篇论文的抓取与统一分析。{note}".strip()


class _Breaker:
    def __init__(self) -> None:
        self.failures = 0
        self.reason = ""
        self.disabled = False
        self.threshold = 3

    def reset(self) -> None:
        self.failures = 0
        self.reason = ""
        self.disabled = False

    def record_failure(self, reason: str) -> None:
        self.failures += 1
        self.reason = reason
        if self.failures >= self.threshold:
            self.disabled = True
