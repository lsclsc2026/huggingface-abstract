import asyncio,json
import httpx,pytest
from app.config import LLMConfig,ProfileConfig
from app.db import Database
from app.llm import LLMClient
from app.model_store import ModelStore
from app.prompts import build_digest_prompt,PAPER_SYSTEM_PROMPT


def paper():
    return {'id':'2611.04391','title':'Example','abstract':'Original abstract','analysis':{'tldr':'共享编码、独立解码','summary':'DETAILED INPUT MUST NOT BE REPEATED','jargon':[{'term':'PRIVATE BULK'}]}}


def test_digest_input_is_compact_semantic_and_not_popularity_sensitive():
    p=paper();a=build_digest_prompt('2026-11-05',[p],ProfileConfig())
    p['upvotes']=999;p['analysis']['summary']='CHANGED DETAIL'
    b=build_digest_prompt('2026-11-05',[p],ProfileConfig())
    assert a==b
    assert 'DETAILED INPUT' not in a and 'jargon' not in a
    assert 'overview' in a


def test_article_prompt_requires_innovations_without_redundant_old_sections():
    assert '设计' in PAPER_SYSTEM_PROMPT and '方法正文' in PAPER_SYSTEM_PROMPT
    assert 'misconceptions' not in PAPER_SYSTEM_PROMPT
    assert 'why_it_matters' not in PAPER_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_success_cache_singleflight_survives_restart_and_tracks_usage(tmp_path):
    db=Database(tmp_path/'db.sqlite');db.init();store=ModelStore(db)
    calls=[]
    async def handle(request):
        calls.append(json.loads(request.content))
        await asyncio.sleep(.01)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'title_zh':'示例','tldr':'问题与机制','summary':'创新设计','method':'具体步骤'})}}],'usage':{'prompt_tokens':120,'completion_tokens':80,'total_tokens':200}})
    config=LLMConfig(api_key='test',model='deepseek-v4.1-flash-1017')
    client=LLMClient(config,ProfileConfig(),store=store)
    client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    first,second=await asyncio.gather(client.summarize_paper(paper()),client.summarize_paper(paper()))
    await client.close()
    other=LLMClient(config,ProfileConfig(),store=ModelStore(db))
    third=await other.summarize_paper(paper());await other.close()
    assert first==second==third and len(calls)==1
    assert first['prompt_version']=='reading-v7'
    stats=store.usage()
    assert stats['requests']==1 and stats['prompt_tokens']==120 and stats['completion_tokens']==80
    assert calls[0]['thinking']=={'type':'disabled'}


@pytest.mark.asyncio
async def test_malformed_response_is_not_a_success_cache(tmp_path):
    db=Database(tmp_path/'db.sqlite');db.init();store=ModelStore(db);calls=[]
    def handle(request):
        calls.append(1)
        content='not json' if len(calls)==1 else json.dumps({'summary':'Recovered','tldr':'Valid'})
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}]})
    client=LLMClient(LLMConfig(api_key='test'),ProfileConfig(),store=store)
    client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    from app.llm import LLMError
    with pytest.raises(LLMError):await client.summarize_paper(paper())
    assert (await client.summarize_paper(paper()))['summary']=='Recovered'
    assert len(calls)==2 and store.usage()['unknown_usage_requests']==2
    await client.close()


def test_full_day_groups_fingerprint_ignore_popularity_but_track_meaning():
    from app.briefing import compose,fingerprint
    p=paper();q={**paper(),'id':'2611.00002','upvotes':1}
    original=fingerprint([p,q]);p['upvotes']=999
    assert fingerprint([q,p])==original
    digest=compose('2026-11-05',[p,q])
    assert len(digest['entries'])==2
    assert sum(group['count'] for group in digest['groups'])==2
    p['analysis']={**p['analysis'],'tldr':'新机制'}
    assert fingerprint([p,q])!=original
    previous={**digest,'overview':'旧总览','overview_status':'ready'}
    assert compose('2026-11-05',[p,q],previous)['overview_status']=='stale'


def test_reading_and_exports_never_call_a_model(public_client,monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Reading must not call models')
    monkeypatch.setattr(LLMClient,'_chat',forbidden)
    for path in ['/api/days/2026-11-05','/api/papers/a','/api/days/2026-11-05/export.md','/api/days/2026-11-05/export.json']:
        assert public_client.get(path).status_code==200
    assert public_client.get('/api/admin/usage').status_code==403
    assert public_client.get('/api/admin/usage',headers={'Authorization':'Bearer test-admin-secret'}).json()['requests']==0


@pytest.mark.asyncio
async def test_model_switch_misses_cache_and_http_failures_are_bounded(tmp_path):
    from app.llm import LLMError
    db=Database(tmp_path/'db.sqlite');db.init();store=ModelStore(db);calls=[]
    def handle(request):
        payload=json.loads(request.content);calls.append(payload['model'])
        if payload['model']=='fail':return httpx.Response(429,json={'error':{'message':'busy'}})
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"summary":"OK"}'}}]})
    for name in ['model-one','model-two','model-one','fail']:
        client=LLMClient(LLMConfig(api_key='test',model=name,max_retries=0),ProfileConfig(),store=store)
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        if name=='fail':
            with pytest.raises(LLMError):await client.summarize_paper(paper())
        else:await client.summarize_paper(paper())
        await client.close()
    assert calls==['model-one','model-two','fail']
    assert store.usage()['requests']==3


def test_administrator_connection_test_is_metered(public_client,monkeypatch):
    runtime=public_client.app.state.runtime
    runtime.settings.llm.enabled=True;runtime.settings.llm.api_key='test'
    monkeypatch.setattr(LLMClient,'_make_client',lambda self:httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}],'usage':{'prompt_tokens':7,'completion_tokens':1,'total_tokens':8}}))))
    response=public_client.post('/api/settings/test',headers={'Authorization':'Bearer test-admin-secret'})
    assert response.status_code==200
    assert runtime.service.store.usage()['requests']==1
    assert runtime.service.store.usage()['total_tokens']==8


def test_formula_heavy_method_is_not_accepted_as_plain_reading():
    from app.llm import normalize_analysis,LLMError
    result=normalize_analysis({'summary':'OK','method':'L_pred=λL_rec'},paper(),ProfileConfig())
    assert 'L_pred' not in result['method'] and result['summary']=='OK'


@pytest.mark.asyncio
async def test_plain_overview_response_needs_no_extra_model_call(tmp_path):
    db=Database(tmp_path/'db.sqlite');db.init();calls=[]
    def handle(request):
        calls.append(1)
        return httpx.Response(200,json={'choices':[{'message':{'content':'今天的论文关注机器人迁移和模型效率，以下仅为本日选文观察。'}}]})
    client=LLMClient(LLMConfig(api_key='test'),ProfileConfig(),store=ModelStore(db))
    client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    first=await client.generate_digest('2026-11-05',[paper()])
    assert first['overview'].startswith('今天的论文')
    assert await client.generate_digest('2026-11-05',[paper()])==first
    assert len(calls)==1
    await client.close()


def test_glossary_keeps_as_many_needed_terms_without_a_four_or_five_term_cutoff():
    from app.llm import normalize_analysis
    terms=[{'term':f'Term {n}','plain':'必要的概念说明'} for n in range(8)]
    result=normalize_analysis({'summary':'研究创新','method':'具体方法','jargon':terms},paper(),ProfileConfig())
    assert len(result['jargon'])==8
    assert len(normalize_analysis({'summary':'研究创新','jargon':terms[:1]},paper(),ProfileConfig())['jargon'])==1
