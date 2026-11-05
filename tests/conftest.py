from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
import yaml
from fastapi.testclient import TestClient
from app.main import create_app
@pytest.fixture
def public_client(tmp_path, monkeypatch):
    config = {'storage': {'data_dir': str(tmp_path/'data')},
              'llm': {'enabled': False, 'api_key': 'sensitive-secret', 'base_url':'https://private-gateway.example/v1'},
              'profile': {'interests': ['PRIVATE INTEREST'], 'notes':'PRIVATE STYLE'},
              'schedule': {'enabled':False}}
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv('HF_PAPER_CONFIG',str(path))
    monkeypatch.setenv('HF_PAPER_ADMIN_TOKEN','test-admin-secret')
    monkeypatch.setenv('HF_PAPER_COMMENTS_ENABLED','true')
    with TestClient(create_app(), client=('203.0.113.8',12345)) as client:
        runtime=client.app.state.runtime
        runtime.db.upsert_papers([{'id':'a','daily_date':'2026-11-05','title':'Public A','abstract':'Public abstract','upvotes':5},
                                  {'id':'b','daily_date':'2026-11-05','title':'Public B','upvotes':99}])
        runtime.db.update_paper_flags('a',bookmarked=True,note='PRIVATE NOTE SENTINEL')
        runtime.db.update_paper_analysis('a',{'summary':'Public explanation'},'ok','llm','PRIVATE MODEL ERROR')
        runtime.db.update_relevance('a',99)
        yield client


