"""Bounded paragraph evidence and deterministic acceptance; no extra model calls."""
from __future__ import annotations
import re

METHOD_CHARS = 6000
MAX_EXCERPTS = 12


def clean(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def select_evidence(paragraphs):
    """Round-robin sections before taking more context from any single section."""
    buckets = {}
    seen = set()
    for section, text in paragraphs:
        text = clean(text)
        if len(text) < 24 or text in seen:
            continue
        seen.add(text)
        # Sentence-aligned chunks avoid slicing away the end of a subsection.
        chunks = re.split(r'(?<=[.!?。])\s+', text)
        packed = []
        for sentence in chunks:
            if packed and len(packed[-1]) + len(sentence) < 700:
                packed[-1] += ' ' + sentence
            else:
                packed.extend(sentence[i:i + 700] for i in range(0, len(sentence), 700))
        buckets.setdefault(section, []).extend(packed)
    result = []
    remaining = METHOD_CHARS
    while buckets and len(result) < MAX_EXCERPTS and remaining >= 80:
        for section in list(buckets):
            text = buckets[section].pop(0)
            if not buckets[section]:
                del buckets[section]
            if len(text) > remaining:
                continue
            result.append({'id': f'M{len(result) + 1}', 'section': section, 'text': text})
            remaining -= len(text)
            if len(result) >= MAX_EXCERPTS or remaining < 80:
                break
    return result


def grounded_method(raw, source):
    evidence = source.get('method_evidence') or []
    if not evidence:
        return {'method': '未取得可核实的方法正文，暂不展开具体实现；摘要中的方法概述请参阅上方摘要。',
                'method_evidence': [], 'method_evidence_status': 'unavailable'}
    by_id = {e['id']: e for e in evidence}
    sections = raw.get('method_sections')
    sections = sections[:5] if isinstance(sections, list) else []
    accepted = []
    references = []
    for item in sections:
        if not isinstance(item, dict):
            continue
        text = str(item.get('text') or '').strip()
        refs = item.get('evidence')
        if not text or len(text) > 700 or not isinstance(refs, list) or not refs or len(refs) > 4:
            continue
        verified = []
        for ref in refs[:4]:
            if not isinstance(ref, dict):
                break
            original = by_id.get(ref.get('id')) if isinstance(ref.get('id'), str) else None
            quote = clean(ref.get('quote'))
            if not original or not 32 <= len(quote) <= 320 or quote not in clean(original['text']):
                break
            verified.append({'id': original['id'], 'section': original['section'], 'quote': quote,
                             'source_url': original.get('source_url', source.get('source_url', ''))})
        else:
            if verified:
                # No formula-heavy mechanism text is presented as a plain-language method.
                if re.search(r'(?:\bL_[a-z]+|[=θψλ])', text):
                    continue
                accepted.append(text)
                references.append({'paragraph': len(accepted), 'evidence': verified})
    status = 'supported' if accepted and len(accepted) == len(sections) else 'partial' if accepted else 'unverified'
    method = '\n\n'.join(accepted)
    if not accepted:
        method = '已取得部分方法正文，但本次生成的实现说明未通过原文引句校验，暂不展示，避免补写未经核实的细节。'
    elif status == 'partial':
        method += '\n\n部分实现说明未通过原文引句校验，已省略。'
    return {'method': method, 'method_evidence': references, 'method_evidence_status': status}
