from copy import deepcopy
from app.terminology import normalize_terminology


def test_industry_token_names_and_common_chinese_are_natural_and_idempotent():
    original={'summary':'自回归解码每步生成一个词元，词元化器处理词元序列。','jargon':[
        {'term':'词元','plain':'模型生成的最小离散单位，每步预测下一个词元。'},
        {'term':'自回归解码（Autoregressive Decoding）','plain':'顺序执行。'},
        {'term':'边界框 (Bounding Box)','plain':'定位范围。'},
        {'term':'视觉-语言-动作模型（VLA）','plain':'融合视觉、语言和动作。'},
        {'term':'分布外（OOD）','plain':'训练分布之外。'},
        {'term':'Harness','plain':'提供工具和运行环境。'}],
        'method_evidence':[{'quote':'Original 词元 evidence must remain exact.'}],
        'publication':{'statement':'Original 词元 metadata'},'category':'其他研究'}
    before=deepcopy(original);result=normalize_terminology(original)
    assert original==before
    assert result['summary']=='自回归解码每步生成一个token，tokenizer处理token序列。'
    assert [x['term'] for x in result['jargon']]==['token（词元）','自回归解码','边界框','VLA（视觉-语言-动作模型）','OOD（分布外）','Harness']
    assert result['method_evidence']==original['method_evidence'] and result['publication']==original['publication']
    assert normalize_terminology(result)==result


def test_does_not_blanket_translate_all_terms_or_modify_research_content():
    original={'method':'自回归解码可使用冻结的目标模型，ADE/FDE用于评价轨迹。','key_points':['成功率为90%。'],
              'jargon':[{'term':'视觉图元','plain':'空间预测单元。'},{'term':'ADE/FDE','plain':'平均误差和终点误差。'},
                        {'term':'持久事实（Persistent Facts）','plain':'跨对话保留的信息。'},{'term':'上下文学习（in-context learning）','plain':'利用提示中的示例。'}]}
    result=normalize_terminology(original)
    assert result['method']==original['method'] and result['key_points']==original['key_points']
    assert result['jargon'][0]['term']=='视觉图元' and result['jargon'][1]['term']=='ADE/FDE'
    assert result['jargon'][2]['term']=='Persistent Facts（持久事实）'
    assert result['jargon'][3]['term']=='上下文学习'


def test_date_update_preserves_yesterday_and_all_raw_evidence(tmp_path):
    import json
    from app.db import Database
    from app.terminology import normalize_day
    db=Database(tmp_path/'db.sqlite');db.init()
    for ident,day in [('old','2026-11-05'),('today','2026-11-06')]:
        db.upsert_papers([{'id':ident,'daily_date':day,'title':'Source title','abstract':'Original token abstract'}])
        db.update_paper_analysis(ident,{'summary':'模型生成词元。','jargon':[{'term':'词元','plain':'最小单位。'}],
                                     'method_evidence':[{'quote':'Original 词元 evidence'}]},'ok','llm')
    with db.connect() as con:before=[tuple(r) for r in con.execute('select * from papers order by id')]
    preview=normalize_day(db,'2026-11-06',apply=False)
    assert preview['selected']==1
    with db.connect() as con:assert before==[tuple(r) for r in con.execute('select * from papers order by id')]
    result=normalize_day(db,'2026-11-06',apply=True)
    assert result['updated']==1
    with db.connect() as con:
        after=[tuple(r) for r in con.execute('select * from papers order by id')]
        old=con.execute("select analysis from papers where id='old'").fetchone()[0]
        today=json.loads(con.execute("select analysis from papers where id='today'").fetchone()[0])
    assert before[0]==after[0] and '词元' in old
    assert today['jargon'][0]['term']=='token（词元）' and today['method_evidence'][0]['quote']=='Original 词元 evidence'
    assert normalize_day(db,'2026-11-06',apply=True)['updated']==0


def test_date_update_refuses_an_active_extraction(tmp_path):
    import pytest
    from app.db import Database
    from app.terminology import normalize_day
    db=Database(tmp_path/'db.sqlite');db.init();db.create_run('2026-11-06',True)
    with pytest.raises(RuntimeError):normalize_day(db,'2026-11-06',apply=True)


def test_common_chinese_translation_is_removed_from_prose_too():
    result=normalize_terminology({'summary':'自回归解码（autoregressive decoding）生成词元，边界框（bounding box）负责定位。',
                                 'method':'编码器（encoder）保持冻结。','jargon':[{'term':'VLA','plain':'使用边界框（Bounding Box）。'}]})
    assert result['summary']=='自回归解码生成token，边界框负责定位。'
    assert result['method']=='编码器保持冻结。'
    assert result['jargon'][0]['plain']=='使用边界框。'


def test_parenthetical_technical_qualifiers_are_never_deleted():
    result=normalize_terminology({'summary':'编码器（仅用于训练）在训练期间冻结，边界框（xyxy）是坐标格式。',
        'jargon':[{'term':'编码器（仅用于训练）','plain':'编码器（frozen at inference）描述执行条件。'},
                  {'term':'边界框（xyxy）','plain':'四个坐标。'}]})
    assert result['jargon'][0]['term']=='编码器（仅用于训练）'
    assert result['jargon'][1]['term']=='边界框（xyxy）'
    assert '仅用于训练' in result['summary'] and 'xyxy' in result['summary']
    assert 'frozen at inference' in result['jargon'][0]['plain']


def test_day_update_keeps_equivalent_briefing_ready_without_model_calls(tmp_path):
    from app.db import Database
    from app.briefing import compose, fingerprint
    from app.terminology import normalize_day
    db=Database(tmp_path/'db.sqlite');db.init()
    db.upsert_papers([{'id':'p','daily_date':'2026-11-06','title':'Research'}])
    db.update_paper_analysis('p',{'summary':'研究','tldr':'提高词元生成效率。'},'ok','llm')
    run=db.create_run('2026-11-06',True);db.update_run(run['id'],status='completed')
    papers=db.get_papers_for_date('2026-11-06');digest=compose('2026-11-06',papers)
    digest.update(overview='研究词元生成。',overview_status='ready',source='llm')
    db.set_digest(run['id'],digest)
    normalize_day(db,'2026-11-06',apply=True)
    after=db.digest_for_day('2026-11-06')
    assert after['overview']=='研究token生成。'
    assert after['content_fingerprint']==fingerprint(db.get_papers_for_date('2026-11-06'))
    assert compose('2026-11-06',db.get_papers_for_date('2026-11-06'),after)['overview_status']=='ready'
    assert normalize_day(db,'2026-11-06',apply=True)['updated']==0


def test_vocabulary_update_never_truncates_a_full_length_overview(tmp_path):
    from app.db import Database
    from app.briefing import compose
    from app.terminology import normalize_day
    db=Database(tmp_path/'db.sqlite');db.init()
    db.upsert_papers([{'id':'p','daily_date':'2026-11-06','title':'Research'}])
    db.update_paper_analysis('p',{'summary':'词元研究','tldr':'词元生成。'},'ok','llm')
    run=db.create_run('2026-11-06',True);db.update_run(run['id'],status='completed')
    original='词元'+'甲'*234+'重要结论'
    assert len(original)==240
    digest=compose('2026-11-06',db.get_papers_for_date('2026-11-06'))
    digest.update(overview=original,overview_status='ready',source='llm');db.set_digest(run['id'],digest)
    normalize_day(db,'2026-11-06',apply=True)
    assert db.digest_for_day('2026-11-06')['overview']==original
