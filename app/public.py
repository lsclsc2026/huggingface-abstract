"""Allowlisted public representations. Legacy private fields stay in SQLite."""
from __future__ import annotations
from typing import Any

PAPER_FIELDS = ('id','daily_date','title','abstract','ai_summary','keywords','authors',
    'organization','published_at','submitted_on_daily','upvotes','comments','thumbnail',
    'hf_url','arxiv_url','pdf_url','github_url','project_url','analysis_status',
    'analysis_source','created_at','updated_at')
ANALYSIS_FIELDS = ('title_zh','tldr','problem','summary','analogy','how_it_works','contrast',
    'example','misconceptions','jargon','key_points','method','findings','why_it_matters',
    'limitations','takeaway','tags','difficulty','read_minutes','paper_type',
    'code_available','source','prompt_version','category','publication','evidence_level')


def public_paper(paper: dict[str,Any]) -> dict[str,Any]:
    result={key:paper[key] for key in PAPER_FIELDS if key in paper}
    analysis=paper.get('analysis')
    result['analysis']={key:analysis[key] for key in ANALYSIS_FIELDS if key in analysis} if isinstance(analysis,dict) else None
    result['raw']={key:value for key,value in (paper.get('raw') or {}).items()
                   if key in {'media_urls','video_url','image_url'}}
    return result


def public_run(run: dict[str,Any] | None) -> dict[str,Any] | None:
    if not run:return None
    result={key:run.get(key) for key in ('id','date','status','stage','total','processed','failed','created_at','updated_at','finished_at')}
    result['message']='后台更新中。' if run.get('status') in {'queued','running'} else '后台更新状态已刷新。'
    return result


def reader_settings(settings) -> dict[str,Any]:
    return {'timezone':settings.server.timezone,
            'schedule':{'enabled':settings.schedule.enabled,
                        'check_interval_minutes':settings.schedule.check_interval_minutes,
                        'digest_time':settings.schedule.digest_time},
            'llm':{'configured':settings.llm.is_configured}}
