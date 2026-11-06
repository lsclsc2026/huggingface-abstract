"""Whole-day deterministic entries; only the short overview uses the model."""
from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from .prompts import public_paper_order

CATEGORIES=('机器人与具身智能','智能体与语言模型','视觉与多模态','三维与空间智能','效率与基础方法','其他研究')
TERMS=(('机器人与具身智能',('robot','vla','embodi','机器人','具身')),
       ('三维与空间智能',('3d','geometry','gaussian','三维','点云','spatial')),
       ('智能体与语言模型',('agent','llm','language','reasoning','智能体','语言模型','推理')),
       ('视觉与多模态',('video','image','vision','audio','multimodal','视频','图像','多模态','音频')),
       ('效率与基础方法',('efficien','train','distill','quantiz','forecast','优化','训练','蒸馏')))


def category(paper):
    a=paper.get('analysis') or {}
    if a.get('category') in CATEGORIES:return a['category']
    text=' '.join([paper.get('title',''),*(a.get('tags') or []),*(paper.get('keywords') or [])]).lower()
    return next((name for name,terms in TERMS if any(term in text for term in terms)),'其他研究')


def entries(papers):
    rows=[]
    for p in public_paper_order(papers):
        a=p.get('analysis') or {}
        rows.append({'id':p['id'],'title':a.get('title_zh') or p.get('title',''),
                     'text':str(a.get('tldr') or p.get('ai_summary') or p.get('title',''))[:180],
                     'category':category(p)})
    return rows


def fingerprint(papers):
    canonical=sorted(entries(papers),key=lambda p:p['id'])
    return hashlib.sha256(json.dumps(canonical,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def compose(day,papers,overview=None,status=None):
    rows=entries(papers);groups=[]
    for name in CATEGORIES:
        subset=[row for row in rows if row['category']==name]
        if subset:groups.append({'name':name,'count':len(subset),'entries':subset})
    current=fingerprint(papers);previous=overview or {}
    same=previous.get('content_fingerprint')==current
    summary=previous.get('overview') or f'本日共收录{len(rows)}篇论文，涵盖'+('、'.join(g['name'] for g in groups) or '多个研究方向')+'。逐篇要点见下方，短总览尚未生成。'
    return {'headline':previous.get('headline') or f'{day} 论文速览','overview':str(summary)[:240],
            'entries':rows,'groups':groups,'paper_count':len(rows),'content_fingerprint':current,
            'overview_status':status or (previous.get('overview_status','ready') if same else 'stale' if previous else 'unavailable'),
            'overview_fingerprint':previous.get('overview_fingerprint') or (current if same else previous.get('content_fingerprint')),
            'source':previous.get('source','heuristic'),'prompt_version':'briefing-v6',
            'generated_at':previous.get('generated_at') or datetime.now(timezone.utc).isoformat(timespec='seconds')}
