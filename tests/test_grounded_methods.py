import json
import httpx
import pytest
from app.config import ProfileConfig, LLMConfig
from app.paper_sources import parse_sources, PaperSources
from app.llm import normalize_analysis, LLMClient
from app.db import Database
from app.model_store import ModelStore


def material():
    return parse_sources('', '<section id="S3"><h2>3 Method</h2><section id="S3.1"><h3>3.1 Training</h3><p>The encoder is frozen while separate decoders learn human and robot motions.</p></section><section id="S3.2"><h3>3.2 Deployment</h3><p>At deployment only the robot decoder receives the shared representation.</p></section></section>')


def test_budget_covers_late_method_subsections_and_omits_results():
    text='<section><h2>3 Method</h2><section><h3>3.1 Overview</h3>'+''.join(f'<p>Overview sentence {n}. '+('Repeated context. '*80)+'</p>' for n in range(30))+'</section><section><h3>3.2 Training</h3><p>FREEZE_ENCODER and train separate decoders.</p></section><section><h3>3.3 Deployment</h3><p>ROBOT_DECODER_ONLY runs at inference.</p></section></section><section><h2>4 Results</h2><p>IRRELEVANT_RESULT</p></section>'
    source=parse_sources('',text)
    excerpts=source['method_evidence']
    joined=' '.join(e['text'] for e in excerpts)
    assert 'FREEZE_ENCODER' in joined and 'ROBOT_DECODER_ONLY' in joined
    assert 'IRRELEVANT_RESULT' not in joined
    assert sum(len(e['text']) for e in excerpts)<=6000
    assert len({e['id'] for e in excerpts})==len(excerpts)
    assert all(e['section'] for e in excerpts)


def test_method_heading_alone_is_not_evidence():
    s=parse_sources('','<section><h2>3 Method</h2></section>')
    assert s['evidence_level']=='abstract' and not s['method_evidence']


def test_grounded_paragraphs_require_original_quote_and_valid_id():
    s=material();e=s['method_evidence'][0]
    raw={'summary':'摘要','method':'凭空编造的长方法','method_sections':[
        {'text':'编码器保持冻结，两个解码器学习不同身体的动作。','evidence':[{'id':e['id'],'quote':e['text']}]},
        {'text':'作者使用了未经提供的强化学习机制。','evidence':[{'id':e['id'],'quote':'nonexistent quote from paper'}]},
        {'text':'错误编号的段落。','evidence':[{'id':'M999','quote':e['text']}]}]}
    result=normalize_analysis(raw,{'source_material':s},ProfileConfig())
    assert '编码器保持冻结' in result['method']
    assert '强化学习' not in result['method'] and '错误编号' not in result['method'] and '凭空编造' not in result['method']
    assert len(result['method_evidence'])==1
    assert result['method_evidence_status']=='partial'


def test_absent_body_never_displays_generated_method():
    result=normalize_analysis({'summary':'原摘要','method':'模型猜的实现','method_sections':[{'text':'也是猜的','evidence':[]}]},{'abstract':'abstract only'},ProfileConfig())
    assert '模型猜' not in result['method'] and '也是猜' not in result['method']
    assert '未取得' in result['method']
    assert result['method_evidence_status']=='unavailable'


def test_no_valid_quotes_does_not_present_method_as_verified():
    result=normalize_analysis({'summary':'摘要','method_sections':[{'text':'未经证实的机制','evidence':[]}]},{'source_material':material()},ProfileConfig())
    assert '未经证实的机制' not in result['method']
    assert result['method_evidence_status']=='unverified'


@pytest.mark.asyncio
async def test_invalid_method_needs_no_repair_call_and_cached_result_is_reused(tmp_path):
    db=Database(tmp_path/'db.sqlite');db.init();store=ModelStore(db);calls=[]
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'摘要','method':'无依据方法'})}}]})
    client=LLMClient(LLMConfig(api_key='test'),ProfileConfig(),store=store)
    client._client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    p={'id':'2611.04391','abstract':'abstract','source_material':material()}
    first=await client.summarize_paper(p);second=await client.summarize_paper(p)
    assert len(calls)==1 and first==second
    assert first['method_evidence_status']=='unverified'
    assert '无依据方法' not in first['method']
    await client.close()


def pdf_bytes(text):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content=DecodedStreamObject();commands=['BT /F1 11 Tf 40 760 Td']
    for line in text.splitlines():commands.append('('+line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')+') Tj 0 -16 Td')
    commands.append('ET');content.set_data('\n'.join(commands).encode('ascii'));page[NameObject('/Contents')]=writer._add_object(content)
    output=BytesIO();writer.write(output);return output.getvalue()


@pytest.mark.asyncio
async def test_pdf_fallback_is_cached_and_excludes_experiments(tmp_path):
    db=Database(tmp_path/'db.sqlite');db.init();calls=[]
    data=pdf_bytes('1 Introduction\nPrior work uses costly data.\n2 Method\n2.1 Training\nWe freeze the visual encoder and train separate human and robot decoders.\n2.2 Deployment\nThe robot decoder alone executes actions at deployment.\n3 Experiments\nDO_NOT_INCLUDE_THIS_RESULT in method evidence.')
    def respond(request):
        calls.append(request.url.path)
        if '/pdf/' in request.url.path:return httpx.Response(200,content=data,headers={'content-type':'application/pdf'})
        return httpx.Response(404)
    source=PaperSources(db,transport=httpx.MockTransport(respond))
    result=await source.get('2611.04391')
    assert result['source_format']=='pdf' and result['evidence_level']=='method'
    assert 'visual encoder' in result['method_text'] and 'robot decoder alone' in result['method_text']
    assert 'DO_NOT_INCLUDE' not in result['method_text']
    assert all('/pdf/' in e['source_url'] for e in result['method_evidence'])
    assert await source.get('2611.04391')==result
    assert len(calls)==3


@pytest.mark.asyncio
async def test_html_success_avoids_pdf_and_corrupt_pdf_falls_back_without_crash(tmp_path):
    db=Database(tmp_path/'db.sqlite');db.init();calls=[]
    def respond(request):
        calls.append(request.url.path)
        if '/html/' in request.url.path and '00001' in request.url.path:
            return httpx.Response(200,text='<section><h2>3 Method</h2><p>We freeze the encoder while training two separate action decoders.</p></section>')
        return httpx.Response(200,content=b'not a PDF' if '/pdf/' in request.url.path else b'')
    source=PaperSources(db,transport=httpx.MockTransport(respond))
    assert (await source.get('2611.00001'))['source_format']=='html'
    assert not any('/pdf/' in x for x in calls)
    result=await source.get('2611.00002')
    assert result['evidence_level']=='abstract' and not result['method_evidence']


def test_pdf_parser_rejects_named_intro_heading_as_method():
    from app.paper_sources import parse_pdf_sections
    result=parse_pdf_sections('3 Method\nWe use a shared encoder and distinct decoders.\n4 Experiments\nSHOULD_NOT_LEAK\n5 Conclusion\nNot method.')
    assert 'shared encoder' in result['method_text'] and 'SHOULD_NOT_LEAK' not in result['method_text']


@pytest.mark.parametrize('html',[
 '<section><h2>4 Experiments</h2><section><h3>4.1 Comparison with Other Methods</h3><p>Baseline authors freeze their encoder and train a distinct decoder.</p></section></section>',
 '<section><h2>2 Related Work</h2><section><h3>2.1 Existing Architectures</h3><p>Earlier methods use shared actions but separate decoders for all robots.</p></section></section>'
])
def test_comparison_and_related_methods_are_not_paper_method_evidence(html):
    assert not parse_sources('',html)['method_evidence']


def test_pdf_compared_methods_and_unnumbered_references_are_excluded():
    from app.paper_sources import parse_pdf_sections
    result=parse_pdf_sections('3 Method\nOur encoder learns shared motion representations for two robot bodies.\nReferences\nUNRELATED reference text should never enter the method.\n4 Experiments\n4.1 Compared Methods\nBASELINE learns actions with a distinct frozen visual encoder.')
    assert 'UNRELATED' not in result['method_text'] and 'BASELINE' not in result['method_text']


def test_unused_legacy_method_does_not_fail_the_entire_analysis():
    result=normalize_analysis({'summary':'可用摘要','method':'L_pred=λL_rec','method_sections':[]},{},ProfileConfig())
    assert result['summary']=='可用摘要' and result['method_evidence_status']=='unavailable'


def test_flash_disables_reasoning_without_a_conflicting_low_effort():
    client=LLMClient(LLMConfig(api_key='test',model='deepseek-v4.1-flash-1017',reasoning_effort='low'),ProfileConfig())
    payload=client._payload([],max_tokens=5000,include_json=True,include_reasoning=True,include_temperature=True)
    assert payload['thinking']=={'type':'disabled'} and payload['reasoning_effort']=='none'
    generic=LLMClient(LLMConfig(api_key='test',model='another-model',reasoning_effort='low'),ProfileConfig())
    assert generic._payload([],max_tokens=5000,include_json=True,include_reasoning=True,include_temperature=True)['reasoning_effort']=='low'


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,422])
async def test_flash_compatibility_fallback_keeps_thinking_disabled(status):
    calls=[]
    def respond(request):
        p=json.loads(request.content);calls.append(p)
        if len(calls)==1:return httpx.Response(status,json={'error':{'message':'response_format not supported'}})
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"summary":"可用摘要","method_sections":[]}'}}]})
    client=LLMClient(LLMConfig(api_key='test',model='deepseek-v4.1-flash-1017',reasoning_effort='low',max_retries=0),ProfileConfig())
    client._client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    result=await client.summarize_paper({'abstract':'abstract'})
    assert result['summary']=='可用摘要' and len(calls)==2
    assert all(p.get('reasoning_effort')=='none' and p.get('thinking')=={'type':'disabled'} for p in calls)
    await client.close()
