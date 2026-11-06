from __future__ import annotations
import json
from typing import Any
from .config import Settings
from .prompts import public_paper_order


def build_markdown(day: str,papers: list[dict[str,Any]],digest: dict[str,Any]|None,settings: Settings) -> str:
    lines=[f'# Hugging Face Daily Papers · {day}','',f'> 共{len(papers)}篇 · 统一中文解读','']
    if digest:
        lines+=['## 今日导读','',str(digest.get('overview','')),'']
        for group in digest.get('groups') or []:
            lines += [f"### {group['name']} · {group['count']}篇",'']
            for item in group['entries']:lines.append(f"- {item['text']} 【{item['title']} · arXiv:{item['id']}】")
            lines.append('')
    for p in public_paper_order(papers):
        a=p.get('analysis') or {};publication=a.get('publication') or {}
        lines += ['---','',f"## {a.get('title_zh') or p.get('title','')}",'',p.get('title',''),'',f"> {a.get('tldr') or p.get('ai_summary','')}",'',
                  '**发表信息**：'+publication.get('statement','未核实发表或投稿状态'),'',
                  '### 英文摘要原文','',p.get('abstract',''),'','### 摘要','',a.get('summary',''),'','---','']
        for term in a.get('jargon') or []:
            if isinstance(term,dict):lines += [f"**{term.get('term','')}**：{term.get('plain','')}",'']
        lines+=['---','','### 方法','',a.get('method','') or '\n'.join(a.get('how_it_works') or []),'','### 主要贡献','']
        lines += ['- '+str(item) for item in a.get('key_points') or []]
        lines.append('')
    return '\n'.join(lines)


def build_json(day,papers,digest,settings=None):
    return json.dumps({'date':day,'papers':papers,'digest':digest},ensure_ascii=False,indent=2)
