import pytest
from app.paper_sources import parse_sources, PaperSources


def test_sources_extract_actual_methods_and_publication_without_guessing():
    result=parse_sources('''<table><tr><td>Comments:</td><td>Accepted for Oral Presentation at the CoRL 2026 Workshop on Scaling H2R.</td></tr></table>''', '''<article><section id="S1"><h2>1 Introduction</h2><p>not method</p></section><section id="S2"><h2>2 Method</h2><section><h3>Shared action space</h3><p>Shared encoder and separate decoders preserve motion dynamics.</p></section></section><section id="S3"><h2>3 Experiments</h2><p>weighted task score, not success rate.</p></section></article>''')
    assert 'Shared encoder' in result['method_text']
    assert 'not method' not in result['method_text']
    assert result['publication']['kind']=='workshop'
    assert result['publication']['ranking']=='不适用（非期刊）'
    assert 'Workshop' in result['publication']['statement']
    assert result['evidence_level']=='method'
    assert parse_sources('','')['publication']['statement']=='未核实发表或投稿状态'


@pytest.mark.asyncio
async def test_source_lookup_is_bounded_cached_and_rejects_arbitrary_urls(tmp_path):
    import httpx
    from app.db import Database
    db=Database(tmp_path/'db.sqlite');db.init()
    calls=[]
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(404,text='unavailable')
    source=PaperSources(db,transport=httpx.MockTransport(handle))
    result=await source.get('2611.04391')
    assert result['evidence_level']=='abstract'
    await source.get('2611.04391')
    await source.get('https://127.0.0.1/private')
    assert len(calls)==2
    assert all(x.startswith('https://arxiv.org/') for x in calls)
