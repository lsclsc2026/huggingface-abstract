from __future__ import annotations

import json
import yaml
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Database



def test_public_surfaces_never_expose_personal_data_or_infrastructure(public_client):
    client=public_client
    for path in ['/api/meta','/api/health','/api/history','/api/days/2026-11-05',
                 '/api/papers/a','/api/search?q=Public','/api/days/2026-11-05/export.json',
                 '/api/days/2026-11-05/export.md']:
        response=client.get(path);assert response.status_code==200,path
        text=response.text
        for private in ['PRIVATE NOTE SENTINEL','PRIVATE INTEREST','PRIVATE STYLE',
                        'private-gateway.example','sensitive-secret','PRIVATE MODEL ERROR',
                        'config_path','pdf_path','relevance','bookmarked']:
            assert private not in text,(path,private)
    papers=client.get('/api/days/2026-11-05').json()['papers']
    assert [p['id'] for p in papers]==['b','a']


@pytest.mark.parametrize('method,path,body',[
    ('get','/api/settings',None),('post','/api/settings/test',None),
    ('post','/api/settings/reload',None),('post','/api/runs',{'date':'2026-11-05'}),
    ('patch','/api/papers/a',{'note':'attacker edit'}),
    ('put','/api/profile',{'interests':['attacker']})])
def test_visitors_cannot_mutate_admin_or_private_state(public_client,method,path,body):
    response=getattr(public_client,method)(path,json=body) if body is not None else getattr(public_client,method)(path)
    assert response.status_code==403
    rt=public_client.app.state.runtime
    assert rt.db.get_paper('a')['note']=='PRIVATE NOTE SENTINEL'
    assert rt.db.active_run() is None


def test_admin_requires_exact_bearer_secret(public_client):
    for value in ['Bearer wrong','Basic test-admin-secret']:
        assert public_client.get('/api/settings',headers={'Authorization':value}).status_code==403
    assert public_client.get('/api/settings',headers={'Authorization':'Bearer test-admin-secret'}).status_code==200
    assert public_client.get('/api/settings',headers={'X-Forwarded-For':'127.0.0.1'}).status_code==403


def test_missing_admin_configuration_fails_closed(public_client,monkeypatch):
    monkeypatch.delenv('HF_PAPER_ADMIN_TOKEN')
    monkeypatch.delenv('HF_PAPER_ADMIN_TOKEN_FILE',raising=False)
    assert public_client.get('/api/settings',headers={'Authorization':'Bearer test-admin-secret'}).status_code==403


def test_runtime_no_longer_recalculates_relevance(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Personal scoring must be disabled')
    monkeypatch.setattr(Database,'update_relevance',forbidden)
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump({'storage':{'data_dir':str(tmp_path/'data')}}))
    monkeypatch.setenv('HF_PAPER_CONFIG',str(path))
    with TestClient(create_app()) as client:
        assert client.get('/api/meta').status_code==200


def test_public_search_does_not_use_private_notes_as_a_match(public_client):
    payload=public_client.get('/api/search',params={'q':'PRIVATE NOTE SENTINEL'}).json()
    assert payload['papers']==[]
