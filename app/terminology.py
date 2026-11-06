"""Conservative display terminology rules; never rewrite source evidence."""
from __future__ import annotations
from copy import deepcopy
import re

VERSION = 'terms-v1'
# These names are already natural Chinese: do not append redundant translations.
COMMON_TRANSLATIONS = {
    '自回归解码': {'autoregressive decoding'}, '推测解码': {'speculative decoding'},
    '边界框': {'bounding box', 'bounding boxes', 'bbox'}, '强化学习': {'reinforcement learning'},
    '注意力机制': {'attention', 'attention mechanism'}, '编码器': {'encoder'}, '解码器': {'decoder'},
    '长尾分布': {'long-tail distribution', 'long-tailed distribution'}, '工作记忆': {'working memory'},
    '流匹配': {'flow matching'}, '表示蒸馏': {'representation distillation'},
    '上下文学习': {'in-context learning', 'icl'}, '本体': {'ontology'},
    '逆动力学模型': {'inverse dynamics model', 'idm'}, '多模态智能体': {'multimodal agent'},
    '持续预训练': {'continued pretraining', 'continual pretraining'},
    '高斯泼溅': {'gaussian splatting'}, '混合专家': {'mixture of experts', 'mixture-of-experts'},
    '扩散模型': {'diffusion model'}, '末端执行器': {'end effector', 'end-effector'},
}
COMMON_CHINESE = set(COMMON_TRANSLATIONS)
_COMMON_PATTERN = re.compile('(' + '|'.join(sorted(COMMON_CHINESE, key=len, reverse=True)) + r')\s*[（(]([^()（）]+)[）)]')


def _remove_translation(match):
    base, translation = match.groups()
    return base if translation.strip().lower() in COMMON_TRANSLATIONS[base] else match.group(0)

TEXT_FIELDS = {
    'title_zh', 'tldr', 'problem', 'summary', 'analogy', 'how_it_works', 'contrast',
    'example', 'misconceptions', 'key_points', 'method', 'findings', 'why_it_matters',
    'limitations', 'takeaway', 'tags',
}


def prose(text):
    # Longer forms first. Preserve model names, measured values and English originals.
    text = _COMMON_PATTERN.sub(_remove_translation, text)
    text = re.sub(r'(?i)\btokens?\s*[（(]词元[）)]', 'token', text)
    return text.replace('词元器', 'tokenizer').replace('词元化器', 'tokenizer').replace('词元化', 'token化').replace('词元', 'token')


def term_name(value):
    text = value.strip()
    if text.lower() in {'token', 'tokens', '词元', 'token（词元）', 'token(词元)'}:
        return 'token（词元）'
    if text.lower() in {'tokenizer', '词元化器', 'tokenizer（词元化器）'}:
        return 'tokenizer（分词器）'
    match = re.fullmatch(r'(.+?)\s*[（(]([^()（）]+)[）)]', text)
    if match:
        base, explanation = (part.strip() for part in match.groups())
        if base in COMMON_CHINESE:
            return base if explanation.lower() in COMMON_TRANSLATIONS[base] else prose(text)
        if explanation in COMMON_CHINESE and base.lower() in COMMON_TRANSLATIONS[explanation]:
            return explanation
        if base == '词元' and explanation.lower() in {'token', 'tokens'}:
            return 'token（词元）'
        # Reverse only existing bilingual labels; never invent English names.
        if re.search(r'[\u4e00-\u9fff]', base) and not re.search(r'[\u4e00-\u9fff]', explanation) and re.search(r'[A-Za-z]', explanation):
            return f'{explanation}（{prose(base)}）'
    return prose(text)


def _text_tree(value):
    if isinstance(value, str):
        return prose(value)
    if isinstance(value, list):
        return [_text_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: _text_tree(item) for key, item in value.items()}
    return value


def normalize_terminology(analysis):
    result = deepcopy(analysis)
    for field in TEXT_FIELDS:
        if field in result:
            result[field] = _text_tree(result[field])
    if isinstance(result.get('jargon'), list):
        for item in result['jargon']:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get('term'), str):
                item['term'] = term_name(item['term'])
            for field in ('plain', 'why', 'example'):
                if isinstance(item.get(field), str):
                    item[field] = prose(item[field])
    result['terminology_version'] = VERSION
    return result


def normalize_day(db, day, *, apply=False):
    """Explicit offline migration of one date. No model calls or source changes."""
    import json
    from datetime import date
    date.fromisoformat(day)
    report = {'date': day, 'selected': 0, 'updated': 0, 'text_changed': 0, 'ids': []}
    with db.connect() as con:
        # A writer cannot create a run between the active-run guard and updates.
        con.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        if apply and con.execute("SELECT 1 FROM runs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise RuntimeError('有提取任务进行中，请完成后再更新术语。')
        rows = con.execute('SELECT id,analysis FROM papers WHERE daily_date=? ORDER BY id', (day,)).fetchall()
        for row in rows:
            if not row['analysis']:
                continue
            before = json.loads(row['analysis'])
            if not isinstance(before, dict):
                continue
            report['selected'] += 1
            after = normalize_terminology(before)
            if {k:v for k,v in after.items() if k!='terminology_version'} != {k:v for k,v in before.items() if k!='terminology_version'}:
                report['text_changed'] += 1
            if before == after:
                continue
            report['updated'] += 1
            report['ids'].append(row['id'])
            if apply:
                con.execute('UPDATE papers SET analysis=? WHERE id=? AND daily_date=?',
                            (json.dumps(after, ensure_ascii=False), row['id'], day))
    return report
