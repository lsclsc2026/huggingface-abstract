"""Bounded, cached official arXiv evidence; no model guesses about venues."""
from __future__ import annotations
import asyncio,re
import httpx
from bs4 import BeautifulSoup
from .model_store import ModelStore

MAX_BYTES=2_000_000
ARXIV_ID=re.compile(r'^\d{4}\.\d{4,5}(?:v\d+)?$')


def parse_sources(abstract_html: str,body_html: str) -> dict:
    abstract=BeautifulSoup(abstract_html,'html.parser')
    fields={}
    for row in abstract.select('tr'):
        cells=row.find_all('td')
        if len(cells)>=2:fields[cells[0].get_text(' ',strip=True).lower().rstrip(':')]=cells[1].get_text(' ',strip=True)
    comments=fields.get('comments','');journal=fields.get('journal reference',fields.get('journal-ref',''))
    statement=journal or (comments if re.search(r'\b(accepted|published|submitted|under review|workshop|conference|ICLR|ICML|NeurIPS|CVPR|CoRL|ICRA|IROS)\b',comments,re.I) else '')
    kind='journal' if journal else ('workshop' if 'workshop' in comments.lower() else 'unverified')
    if kind=='unverified' and re.search(r'\b(accepted|published|submitted|under review)\b',comments,re.I):kind='conference' if re.search(r'\b(conference|ICLR|ICML|NeurIPS|CVPR|CoRL|ICRA|IROS)\b',comments,re.I) else kind
    statement=re.split(r'\b(?:Project page|Code|Website)\s*:',statement,flags=re.I)[0].strip()
    publication={'statement':statement[:600] or '未核实发表或投稿状态','kind':kind,'ranking':'不适用（非期刊）' if kind in {'conference','workshop'} else '未核实','source':'arxiv_author_metadata' if statement else 'unverified'}
    body=BeautifulSoup(body_html,'html.parser')
    for element in body.select('script,style,nav,footer,.ltx_equation,.ltx_equationgroup'):
        if element.parent is not None:element.decompose()
    for element in body.select('math,.ltx_Math'):
        if element.parent is not None:element.replace_with(' '+(element.get('alttext') or element.get_text(' ',strip=True))+' ')
    notes='\n'.join(p.get_text(' ',strip=True) for p in body.select('p') if re.search(r'(at deployment|deployment uses|although deployment|weighted task score)',p.get_text(' ',strip=True),re.I))[:1800]
    methods=[];results=[]
    for section in body.select('section'):
        heading=section.find(['h2','h3'],recursive=False)
        if not heading:continue
        title=heading.get_text(' ',strip=True)
        if re.search(r'\b(methods?|methodology|approach(?:es)?|architectures?|frameworks?|proposed)\b',title,re.I):
            if not any(parent in methods for parent in section.parents):methods.append(section)
        elif re.search(r'\b(experiments?|results?)\b',title,re.I):
            if not any(parent in results for parent in section.parents):results.append(section)
    method='\n'.join(item.get_text(' ',strip=True) for item in methods)[:12000]
    evidence='\n'.join(item.get_text(' ',strip=True) for item in results)[:3500]
    return {'publication':publication,'method_text':method,'results_text':evidence,'implementation_notes':notes,'evidence_level':'method' if method else 'abstract'}


class PaperSources:
    def __init__(self,db,transport=None):
        self.store=ModelStore(db);self.transport=transport

    async def get(self,paper_id: str) -> dict:
        if not ARXIV_ID.fullmatch(paper_id):return parse_sources('','')
        async with self.store.lock('source:'+paper_id):
            cached=self.store.get('arxiv-reading-v3',paper_id)
            if cached is not None:return cached
            pages=[]
            async with httpx.AsyncClient(timeout=httpx.Timeout(15,connect=5),transport=self.transport,follow_redirects=False,trust_env=False) as client:
                for endpoint in ('abs','html'):
                    try:
                        async with client.stream('GET',f'https://arxiv.org/{endpoint}/{paper_id}') as response:
                            if response.status_code!=200:pages.append('');continue
                            chunks=[];size=0
                            async for chunk in response.aiter_bytes():
                                size+=len(chunk)
                                if size>MAX_BYTES:raise ValueError('source too large')
                                chunks.append(chunk)
                            pages.append(b''.join(chunks).decode('utf-8',errors='replace'))
                    except (httpx.HTTPError,ValueError):pages.append('')
            result=parse_sources(*pages)
            result['source_url']=f'https://arxiv.org/abs/{paper_id}'
            self.store.put('arxiv-reading-v3',paper_id,result,ttl=None if result['method_text'] else 86400)
            return result
