"""Bounded, cached official arXiv evidence; no model guesses about venues."""
from __future__ import annotations
import asyncio,json,re,sys
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from .model_store import ModelStore
from .method_evidence import select_evidence

MAX_BYTES=2_000_000
EXCLUDED_HEADING=re.compile(r'\b(experiments?|results?|related work|comparisons?|compared|baselines?|ablations?|references|bibliography|conclusions?)\b',re.I)
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
        ancestors=[section,*section.find_parents('section')]
        excluded=any(EXCLUDED_HEADING.search(h.get_text(' ',strip=True)) for parent in ancestors if (h:=parent.find(['h2','h3','h4','h5'],recursive=False)))
        if not excluded and re.search(r'\b(methods?|methodology|approach(?:es)?|architectures?|frameworks?|proposed)\b',title,re.I):
            if not any(parent in methods for parent in section.parents):methods.append(section)
        elif re.search(r'\b(experiments?|results?)\b',title,re.I):
            if not any(parent in results for parent in section.parents):results.append(section)
    paragraphs=[]
    for section in methods:
        for paragraph in section.select('p'):
            lineage=paragraph.find_parents('section')
            if any(EXCLUDED_HEADING.search(h.get_text(' ',strip=True)) for parent in lineage if (h:=parent.find(['h2','h3','h4','h5'],recursive=False))):continue
            owner=paragraph.find_parent('section')
            heading=owner.find(['h2','h3','h4','h5'],recursive=False) if owner else None
            title=heading.get_text(' ',strip=True) if heading else 'Method'
            paragraphs.append((title,paragraph.get_text(' ',strip=True)))
    method_evidence=select_evidence(paragraphs)
    method='\n'.join(e['text'] for e in method_evidence)
    evidence='\n'.join(item.get_text(' ',strip=True) for item in results)[:3500]
    return {'publication':publication,'method_evidence':method_evidence,'method_text':method,'results_text':evidence,'implementation_notes':notes,'evidence_level':'method' if method else 'abstract'}


METHOD_HEADING=re.compile(r'\b(methods?|methodology|approach(?:es)?|architectures?|frameworks?|proposed)\b',re.I)


def parse_pdf_sections(text: str) -> dict:
    paragraphs=[];results=[];current='';root=None;mode=None;buffer=[];excluded_roots=[]
    def flush():
        content=' '.join(buffer).strip()
        if content and mode=='method':paragraphs.append((current,content))
        elif content and mode=='results':results.append(content)
        buffer.clear()
    for line in text.splitlines():
        line=line.strip()
        heading=re.match(r'^((?:\d+(?:\.\d+)*|[IVX]+)\.?)\s+([A-Za-z][^.!?]{0,90})$',line)
        plain=bool(re.fullmatch(r'(?:Methods?|Methodology|Our Approach|Experiments?|Results?|References|Bibliography|Conclusion|Related Work)',line,re.I))
        if heading or plain:
            flush()
            number=heading.group(1).rstrip('.') if heading else ''
            title=heading.group(2) if heading else line
            excluded=EXCLUDED_HEADING.search(title)
            if excluded and number:excluded_roots.append(number)
            blocked=any(number==prefix or number.startswith(prefix+'.') for prefix in excluded_roots) if number else False
            if excluded or blocked:
                if not root or not number.startswith(root+'.'):root=None
                mode='results' if re.search(r'\b(experiments?|results?)\b',title,re.I) else None
                current=line
            elif root and number.startswith(root+'.'):
                current=line;mode='method'
            elif METHOD_HEADING.search(title):
                root=number;mode='method';current=line
            elif re.search(r'\b(experiments?|results?)\b',title,re.I):
                root=None;mode='results';current=line
            else:
                root=None;mode=None;current=line
        elif not line:
            flush()
        elif mode:
            buffer.append(line)
    flush()
    evidence=select_evidence(paragraphs)
    return {'method_evidence':evidence,'method_text':'\n'.join(e['text'] for e in evidence),
            'results_text':'\n'.join(results)[:1500], 'implementation_notes':'',
            'evidence_level':'method' if evidence else 'abstract'}


async def pdf_text(data: bytes) -> str:
    if not data.startswith(b'%PDF-'):
        return ''
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.pdf_text',
        cwd=str(Path(__file__).resolve().parent.parent),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        output,_=await asyncio.wait_for(process.communicate(data),timeout=20)
        return str(json.loads(output).get('text','')) if process.returncode==0 else ''
    except (asyncio.TimeoutError,ValueError,UnicodeError):
        return ''
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class PaperSources:
    def __init__(self,db,transport=None):
        self.store=ModelStore(db);self.transport=transport

    async def get(self,paper_id: str) -> dict:
        if not ARXIV_ID.fullmatch(paper_id):return parse_sources('','')
        async with self.store.lock('source:'+paper_id):
            cached=self.store.get('arxiv-reading-v4',paper_id)
            if cached is not None:return cached
            pages=[]
            async def download(client,endpoint,limit):
                try:
                    async with client.stream('GET',f'https://arxiv.org/{endpoint}/{paper_id}') as response:
                        if response.status_code!=200:return b''
                        chunks=[];size=0
                        async for chunk in response.aiter_bytes():
                            size+=len(chunk)
                            if size>limit:return b''
                            chunks.append(chunk)
                        return b''.join(chunks)
                except httpx.HTTPError:return b''
            async with httpx.AsyncClient(timeout=httpx.Timeout(15,connect=5),transport=self.transport,follow_redirects=False,trust_env=False) as client:
                for endpoint in ('abs','html'):
                    data=await download(client,endpoint,MAX_BYTES)
                    pages.append(data.decode('utf-8',errors='replace'))
                result=parse_sources(*pages)
                result['source_format']='html' if result['method_evidence'] else 'abstract'
                if not result['method_evidence']:
                    data=await download(client,'pdf',12_000_000)
                    fallback=parse_pdf_sections(await pdf_text(data))
                    if fallback['method_evidence']:
                        result.update(fallback);result['source_format']='pdf'
            endpoint={'pdf':'pdf','html':'html','abstract':'abs'}[result['source_format']]
            result['source_url']=f'https://arxiv.org/{endpoint}/{paper_id}'
            for excerpt in result['method_evidence']:
                excerpt['source_url']=result['source_url']
            self.store.put('arxiv-reading-v4',paper_id,result,ttl=None if result['method_text'] else 86400)
            return result
