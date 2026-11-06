from __future__ import annotations
import asyncio,logging
from typing import Any
from .config import Settings
from .db import Database,utc_now
from .hf import HFClient,HFError
from .llm import LLMClient,LLMError,heuristic_analysis
from .model_store import ModelStore
from .paper_sources import PaperSources,ARXIV_ID
from .briefing import compose,fingerprint

LOGGER=logging.getLogger(__name__)


def digest_due(settings: Settings,day: str) -> bool:
    return settings.digest.enabled and day<=settings.today()


class ExtractionService:
    def __init__(self,settings: Settings,db: Database):
        self.settings=settings;self.db=db;self.store=ModelStore(db)
        self.sources=PaperSources(db)

    async def run(self,run_id: str,day: str,refresh: bool=False,*,sequential: bool=False):
        if not self.db.get_run(run_id):return
        configured=self.settings.llm.is_configured
        try:
            self.db.update_run(run_id,status='running',stage='fetching',message='正在同步当天论文…')
            hf=HFClient(self.settings.fetch)
            fetched=await hf.fetch_daily(day,enrich_details=False)
            old={p['id']:p for p in self.db.get_papers_for_date(day)}
            for p in fetched:
                previous=old.get(p['id'],{})
                for field in ('abstract','ai_summary','keywords','authors','organization','thumbnail','pdf_url','arxiv_url'):
                    if not p.get(field) and previous.get(field):p[field]=previous[field]
                p['raw']={**(previous.get('raw') or {}),**(p.get('raw') or {})}
            self.db.upsert_papers(fetched)
            papers=self.db.get_papers_for_date(day)
            pending=[p for p in papers if refresh or not p.get('analysis')]
            total=len(pending);processed=failed=0;breaker=_Breaker()
            self.db.update_run(run_id,total=total,processed=0,failed=0,stage='analyzing' if total else 'digest')
            if self.settings.fetch.detail_pages:
                targets=[p for p in pending if ARXIV_ID.fullmatch(p['id'])]
                if targets:
                    await hf._enrich_details(targets)
                    self.db.upsert_papers(targets)
            async def process(p,llm=None):
                nonlocal processed,failed
                if llm is not None and not breaker.disabled:
                    try:
                        p['source_material']=await self.sources.get(p['id'])
                        analysis=await llm.summarize_paper(p)
                        self.db.update_paper_analysis(p['id'],analysis,'ok','llm')
                        breaker.reset()
                    except LLMError as exc:
                        breaker.record_failure(str(exc));failed+=1
                        self.db.update_paper_analysis(p['id'],heuristic_analysis(p,self.settings.profile),'failed','fallback',str(exc))
                else:
                    if configured:failed+=1
                    self.db.update_paper_analysis(p['id'],heuristic_analysis(p,self.settings.profile),'fallback','fallback',breaker.reason if configured else '模型未配置')
                processed+=1
                self.db.update_run(run_id,processed=processed,failed=failed,stage='analyzing',message=f'已分析 {processed}/{total} 篇。')
            async def work(llm=None):
                semaphore=asyncio.Semaphore(1 if sequential else max(1,self.settings.llm.concurrency))
                async def guarded(p):
                    async with semaphore:await process(p,llm)
                await asyncio.gather(*(guarded(p) for p in pending))
                if digest_due(self.settings,day):
                    await self._update_digest(run_id,day,llm if not breaker.disabled else None)
            if configured:
                async with LLMClient(self.settings.llm,self.settings.profile,store=self.store) as llm:await work(llm)
            else:await work()
            self.db.update_run(run_id,status='completed',stage='done',processed=processed,failed=failed,
                               message=f'已同步{len(papers)}篇，完成{total}篇分析；相同内容复用已有结果。',finished_at=utc_now())
        except asyncio.CancelledError:
            self.db.update_run(run_id,status='interrupted',stage='stopped',message='任务已停止。',finished_at=utc_now());raise
        except Exception as exc:
            LOGGER.exception('Extraction failed for %s',day)
            self.db.update_run(run_id,status='failed',stage='failed',message=str(exc),finished_at=utc_now())

    async def _update_digest(self,run_id,day,llm):
        papers=self.db.get_papers_for_date(day)
        if not papers:return
        previous=self.db.digest_for_day(day)
        key=fingerprint(papers)
        if previous and previous.get('content_fingerprint')==key and previous.get('overview_status')=='ready' and (previous.get('source')=='llm' or not self.settings.llm.is_configured):return
        self.db.update_run(run_id,stage='digest',message='正在更新简短总览，逐篇条目复用分析…')
        overview=previous;status='unavailable' if not previous else 'stale'
        if llm:
            try:
                overview=await llm.generate_digest(day,papers)
                overview['content_fingerprint']=key
                overview['overview_fingerprint']=key
                status='ready'
            except LLMError as exc:LOGGER.warning('Short overview unavailable: %s',exc)
        digest=compose(day,papers,overview,status=status if llm else 'unavailable')
        if not llm:
            # A deterministic overview is complete for a deliberately model-free setup.
            digest['overview_status']='ready' if not self.settings.llm.is_configured else status
        self.db.set_digest(run_id,digest)


class _Breaker:
    def __init__(self):self.failures=0;self.reason='';self.disabled=False
    def reset(self):self.failures=0;self.reason='';self.disabled=False
    def record_failure(self,reason):
        self.failures+=1;self.reason=reason;self.disabled=self.failures>=3
