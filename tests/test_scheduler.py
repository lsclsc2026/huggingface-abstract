from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import yaml

from app.config import Settings
from app.hf import HFClient
from app.llm import LLMClient, LLMError
from app.main import Runtime


@pytest.fixture
def scheduled_runtime(tmp_path, monkeypatch):
    config = {
        'storage': {'data_dir': str(tmp_path / 'data')},
        'profile': {'interests': ['agent']},
        'llm': {'enabled': False},
        'fetch': {'detail_pages': False},
        'schedule': {'enabled': True, 'check_interval_minutes': 15,
                     'digest_time': '16:00', 'run_on_start': True},
        'digest': {'enabled': True, 'top_n': 1},
    }
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv('HF_PAPER_CONFIG', str(path))
    clock = {'now': datetime(2026, 11, 5, 15, 30, tzinfo=ZoneInfo('Asia/Shanghai'))}
    monkeypatch.setattr(Settings, 'now', lambda self: clock['now'])
    runtime = Runtime()
    papers = [{'id': 'a', 'daily_date': '2026-11-05', 'title': 'Agent A', 'upvotes': 1},
              {'id': 'b', 'daily_date': '2026-11-05', 'title': 'Vision B', 'upvotes': 20}]
    async def fetch(self, day, enrich_details=None):
        assert day == '2026-11-05'
        return [dict(paper) for paper in papers]
    monkeypatch.setattr(HFClient, 'fetch_daily', fetch)
    return runtime, clock, papers


async def tick(runtime):
    await runtime._scheduler_tick()
    if runtime.tasks:
        await asyncio.gather(*list(runtime.tasks))


@pytest.mark.asyncio
async def test_interval_is_per_check_and_updates_continue_after_digest(scheduled_runtime):
    runtime, clock, papers = scheduled_runtime
    try:
        await tick(runtime)
        assert len(runtime.db.get_papers_for_date('2026-11-05')) == 2
        original = runtime.db.get_paper('a')['analysis']
        assert runtime.db.digest_for_day('2026-11-05') is None
        papers.append({'id': 'c', 'daily_date': '2026-11-05', 'title': 'C'})
        clock['now'] += timedelta(minutes=14, seconds=59)
        await tick(runtime)
        assert runtime.db.get_paper('c') is None
        clock['now'] += timedelta(seconds=1)
        await tick(runtime)
        assert runtime.db.get_paper('c')['analysis']
        assert runtime.db.get_paper('a')['analysis'] == original
        clock['now'] = clock['now'].replace(hour=16, minute=0)
        await tick(runtime)
        digest = runtime.db.digest_for_day('2026-11-05')
        assert digest is not None
        papers.append({'id': 'd', 'daily_date': '2026-11-05', 'title': 'D'})
        clock['now'] += timedelta(minutes=15)
        await tick(runtime)
        assert runtime.db.get_paper('d')['analysis']
        assert runtime.db.digest_for_day('2026-11-05') == digest
        assert runtime.db.get_paper('a')['analysis'] == original
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_failed_digest_retries_next_interval_with_public_selection(scheduled_runtime, monkeypatch):
    runtime, clock, papers = scheduled_runtime
    runtime.settings.llm.enabled = True
    runtime.settings.llm.api_key = 'test'
    runtime.db.upsert_papers(papers)
    for paper in papers:
        runtime.db.update_paper_analysis(paper['id'], {'summary': 'Shared'}, 'ok', 'llm')
    # Personal relevance favors A, public popularity favors B. top_n=1 must
    # select B regardless of this user's interest and the database's sort order.
    runtime.apply_profile(['agent'], '只写智能体')
    clock['now'] = clock['now'].replace(hour=16, minute=0)
    attempts = []
    async def chat(self, messages, **kwargs):
        payload = messages[-1]['content']
        assert '"id": "b"' in payload and '"id": "a"' not in payload
        assert '只写智能体' not in payload
        attempts.append(clock['now'])
        if len(attempts) == 1:
            raise LLMError('temporary upstream error')
        return json.dumps({'headline': 'Shared digest', 'overview': 'Overview'})
    monkeypatch.setattr(LLMClient, '_chat', chat)
    try:
        await tick(runtime)
        assert runtime.db.digest_for_day('2026-11-05') is None
        clock['now'] += timedelta(minutes=14, seconds=59)
        await tick(runtime)
        assert runtime.db.digest_for_day('2026-11-05') is None
        clock['now'] += timedelta(seconds=1)
        await tick(runtime)
        assert runtime.db.digest_for_day('2026-11-05')['headline'] == 'Shared digest'
        assert len(attempts) == 2
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_disabled_digest_does_not_trigger_a_run(scheduled_runtime):
    runtime, clock, papers = scheduled_runtime
    runtime.settings.digest.enabled = False
    runtime.db.upsert_papers(papers)
    runtime._last_check = clock['now'].replace(hour=16, minute=0)
    clock['now'] = runtime._last_check
    try:
        await tick(runtime)
        assert runtime.db.latest_run('2026-11-05') is None
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_scheduler_resumes_unanalyzed_papers_after_interruption(scheduled_runtime):
    runtime, clock, papers = scheduled_runtime
    # A crash can happen after upsert but before analysis. All IDs already
    # exist, yet the next periodic check must complete their pending analyses.
    runtime.db.upsert_papers(papers)
    try:
        await tick(runtime)
        assert all(p['analysis'] for p in runtime.db.get_papers_for_date('2026-11-05'))
        assert runtime.db.latest_run('2026-11-05')['status'] == 'completed'
    finally:
        await runtime.shutdown()
