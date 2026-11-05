from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import pytest


def test_comment_lifecycle_masked_identity_time_and_admin_delete(public_client):
    client=public_client
    response=client.post('/api/papers/a/comments',json={'content':'  一条公开评论  '})
    assert response.status_code==201
    comment=response.json()['comment']
    assert comment['content']=='一条公开评论'
    assert comment['ip']=='203.0.*.*'
    assert comment['visitor'].startswith('访客 ')
    assert comment['avatar_color'].startswith('#')
    assert comment['created_at'].endswith('+00:00')
    assert '203.0.113.8' not in response.text
    assert 'visitor_hash' not in response.text
    listed=client.get('/api/papers/a/comments').json()
    assert listed['comments']==[comment]
    cid=comment['id']
    assert client.delete('/api/comments/'+cid).status_code==403
    assert client.delete('/api/comments/'+cid,headers={'Authorization':'Bearer test-admin-secret'}).status_code==200
    assert client.get('/api/papers/a/comments').json()['comments']==[]
    # Deletion must not reset flood controls.
    assert client.post('/api/papers/a/comments',json={'content':'再次发送'}).status_code==429


@pytest.mark.parametrize('content',['','   ','a'*1001,'bad\x00text'])
def test_comment_validation(public_client,content):
    assert public_client.post('/api/papers/a/comments',json={'content':content}).status_code==422


def test_unknown_paper_and_forged_identity(public_client):
    assert public_client.post('/api/papers/missing/comments',json={'content':'hello'}).status_code==404
    assert public_client.get('/api/papers/missing/comments').status_code==404
    assert public_client.post('/api/papers/a/comments',json={'content':'hello','ip':'8.8.8.8'}).status_code==422
    first=public_client.post('/api/papers/a/comments',json={'content':'hello'},headers={'X-Forwarded-For':'8.8.8.8'})
    assert first.status_code==201
    assert first.json()['comment']['ip']=='203.0.*.*'
    assert public_client.post('/api/papers/a/comments',json={'content':'other'},headers={'X-Forwarded-For':'9.9.9.9'}).status_code==429


def test_rate_limit_pagination_and_plaintext_xss(public_client):
    client=public_client
    clock=[1_800_000_000.0]
    client.app.state.runtime.comments.clock=lambda:clock[0]
    content='<img src=x onerror=alert(1)>'
    for n in range(20):
        r=client.post('/api/papers/a/comments',json={'content':content+str(n)})
        assert r.status_code==201
        clock[0]+=15
    limited=client.post('/api/papers/a/comments',json={'content':'hourly limit'})
    assert limited.status_code==429 and int(limited.headers['Retry-After'])>0
    first=client.get('/api/papers/a/comments?limit=7').json()
    assert len(first['comments'])==7 and first['has_more']
    assert first['comments'][0]['content']==content+'19'
    second=client.get('/api/papers/a/comments',params={'limit':7,'before':first['next_before']}).json()
    assert len(second['comments'])==7
    assert not ({c['id'] for c in first['comments']} & {c['id'] for c in second['comments']})
    clock[0]+=3600
    assert client.post('/api/papers/a/comments',json={'content':'next hour'}).status_code==201


def test_comment_identity_and_rate_limit_survive_restart(public_client):
    rt=public_client.app.state.runtime
    first=public_client.post('/api/papers/a/comments',json={'content':'hello'}).json()['comment']
    rt.reload()
    assert public_client.get('/api/papers/a/comments').json()['comments'][0]==first
    assert public_client.post('/api/papers/a/comments',json={'content':'again'}).status_code==429
    key=rt.settings.storage.data_dir/'comment_identity.key'
    assert key.stat().st_mode & 0o777==0o600
    assert rt.db.get_paper('a')['note']=='PRIVATE NOTE SENTINEL'


def test_atomic_rate_limit_under_concurrent_posts(public_client):
    def post(n):return public_client.post('/api/papers/a/comments',json={'content':str(n)}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:codes=list(pool.map(post,range(4)))
    assert codes.count(201)==1 and codes.count(429)==3


def test_ipv6_is_masked_and_normalized(public_client):
    with TestClient(public_client.app,client=('2001:db8:abcd:1234::1',12345)) as client:
        response=client.post('/api/papers/a/comments',json={'content':'IPv6'})
        assert response.status_code==201
        comment=response.json()['comment']
        assert comment['ip']=='2001:db8:abcd:*:*:*:*:*'
        assert '1234' not in comment['ip']


def test_comments_default_closed_and_switch_does_not_remove_data(public_client,monkeypatch):
    public_client.post('/api/papers/a/comments',json={'content':'Existing comment'})
    monkeypatch.delenv('HF_PAPER_COMMENTS_ENABLED')
    assert public_client.post('/api/papers/a/comments',json={'content':'disabled'}).status_code==403
    payload=public_client.get('/api/papers/a/comments').json()
    assert payload['enabled'] is False
    monkeypatch.setenv('HF_PAPER_COMMENTS_ENABLED','true')
    assert len(public_client.get('/api/papers/a/comments').json()['comments'])==1


def test_anonymous_comments_need_no_identity_but_reject_cross_site_posts(public_client):
    public_client.cookies.clear()
    url='/api/papers/a/comments'
    for origin in ('https://unrelated.example','null','http://[broken'):
        response=public_client.post(url,json={'content':'cross-site'},headers={'Origin':origin})
        assert response.status_code==403
    assert public_client.get(url).json()['comments']==[]
    response=public_client.post(url,json={'content':'无需登录'},headers={'Origin':'http://testserver'})
    assert response.status_code==201
    assert public_client.get('/api/comment-auth').status_code==404
