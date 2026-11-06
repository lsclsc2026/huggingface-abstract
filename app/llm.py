from __future__ import annotations

import asyncio
import json
import hashlib
import time
from contextvars import ContextVar
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import LLMConfig, ProfileConfig
from .net import proxy_for
from .relevance import score_relevance  # Compatibility for existing imports.
from .prompts import (
    DIGEST_SYSTEM_PROMPT,
    PAPER_SYSTEM_PROMPT,
    build_digest_prompt,
    build_paper_prompt,
    public_paper_order,
)

LOGGER = logging.getLogger(__name__)
_PURPOSE = ContextVar("model_purpose", default="connection")


class LLMError(RuntimeError):
    """A user-facing model call or output parsing error."""


class LLMClient:
    def __init__(self, config: LLMConfig, profile: ProfileConfig, store=None) -> None:
        self.config = config
        self.profile = profile
        self.store = store
        self._client: httpx.AsyncClient | None = None
        # 0 = all optional fields, 1 = no JSON/reasoning/temperature,
        # 2 = core payload only. Once a provider rejects optional fields, avoid
        # paying for the same failed round-trip on every remaining paper.
        self._degraded_level = 0

    async def __aenter__(self) -> "LLMClient":
        self._client = self._make_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout_seconds, connect=20),
            follow_redirects=True,
            trust_env=False,
            proxy=proxy_for(self.config.base_url, self.config.proxy),
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers.update(self.config.extra_headers)
        return headers

    def _payload(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        include_json: bool,
        include_reasoning: bool,
        include_temperature: bool,
        include_extra_body: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        }
        if include_temperature:
            payload["temperature"] = self.config.temperature
        if include_reasoning and self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        if include_json and self.config.json_mode != "never":
            payload["response_format"] = {"type": "json_object"}
        if 'deepseek' in self.config.model.lower() and any(part in self.config.model.lower() for part in ('flash','v4')):
            payload['thinking'] = {'type': 'disabled'}
        if include_extra_body and self.config.extra_body:
            payload.update(self.config.extra_body)
        return payload

    async def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        if not self.config.is_configured:
            raise LLMError("私有模型尚未配置完整。")
        client = await self._ensure_client()
        variants: list[dict[str, Any]] = []
        if self._degraded_level == 0:
            variants.append(
                self._payload(
                    messages,
                    max_tokens=max_tokens,
                    include_json=json_mode,
                    include_reasoning=True,
                    include_temperature=True,
                )
            )
        if self._degraded_level <= 1:
            variants.append(
                self._payload(
                    messages,
                    max_tokens=max_tokens,
                    include_json=False,
                    include_reasoning=False,
                    include_temperature=False,
                )
            )
        if self.config.extra_body or self._degraded_level >= 2:
            variants.append(
                self._payload(
                    messages,
                    max_tokens=max_tokens,
                    include_json=False,
                    include_reasoning=False,
                    include_temperature=False,
                    include_extra_body=False,
                )
            )
        if not variants:
            variants.append(
                self._payload(
                    messages,
                    max_tokens=max_tokens,
                    include_json=False,
                    include_reasoning=False,
                    include_temperature=False,
                    include_extra_body=False,
                )
            )

        last_error = ""
        for variant_index, payload in enumerate(variants):
            for attempt in range(self.config.max_retries + 1):
                started = time.monotonic()
                try:
                    response = await client.post(
                        self.config.chat_url,
                        headers=self._headers(),
                        json=payload,
                    )
                except httpx.TimeoutException as exc:
                    if self.store:self.store.record(self.config.model,_PURPOSE.get(),"timeout",time.monotonic()-started)
                    last_error = "模型请求超时"
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    raise LLMError(
                        f"模型请求超时（{self.config.timeout_seconds}s）。"
                        "可以在 config.yaml 增大 llm.timeout_seconds。"
                    ) from exc
                except httpx.HTTPError as exc:
                    if self.store:self.store.record(self.config.model,_PURPOSE.get(),"network_error",time.monotonic()-started)
                    last_error = f"网络错误：{type(exc).__name__}"
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    raise LLMError(
                        "无法连接模型服务，请检查 llm.base_url、网络和代理。"
                    ) from exc

                if self.store:
                    try: usage = response.json().get('usage')
                    except (ValueError,AttributeError): usage = None
                    self.store.record(self.config.model,_PURPOSE.get(),response.status_code,time.monotonic()-started,usage)
                if response.status_code == 200:
                    return self._extract_content(response)

                detail = _error_detail(response)
                if response.status_code in {401, 403}:
                    raise LLMError(
                        f"模型鉴权失败（HTTP {response.status_code}）。"
                        f"请检查 llm.api_key。{detail}"
                    )
                if response.status_code == 404:
                    raise LLMError(
                        "模型接口返回 404。请确认 llm.base_url 是否包含正确的 /v1 路径，"
                        f"以及模型名是否存在。{detail}"
                    )
                if response.status_code in {400, 422} and variant_index < len(variants) - 1:
                    last_error = f"HTTP {response.status_code}: {detail}"
                    self._degraded_level = (
                        1
                        if self._degraded_level == 0 and variant_index == 0
                        else 2
                    )
                    LOGGER.info(
                        "Retrying model call with fewer optional fields (level %s): %s",
                        self._degraded_level,
                        detail,
                    )
                    break
                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = f"HTTP {response.status_code}: {detail}"
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(2.5 * (attempt + 1))
                        continue
                raise LLMError(
                    f"模型调用失败（HTTP {response.status_code}）：{detail}"
                )
        raise LLMError(f"模型调用失败：{last_error or '未知错误'}")

    def _extract_content(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("模型返回的不是 JSON，可能命中了错误页面或代理。") from exc
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise LLMError("模型返回中没有 choices。")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or ""))
                else:
                    parts.append(str(part))
            content = "".join(parts)
        if not content:
            content = choice.get("text") or ""
        content = str(content).strip()
        if not content:
            finish_reason = choice.get("finish_reason") or ""
            raise LLMError(
                "模型返回了空内容"
                + (f"（finish_reason={finish_reason}）" if finish_reason else "")
                + "。请检查模型是否支持 Chat Completions。"
            )
        return content

    async def _result(self, purpose, messages, max_tokens, normalize):
        material={'endpoint':self.config.chat_url,'model':self.config.model,'messages':messages,
                  'credentials_hash':hashlib.sha256(json.dumps(self._headers(),sort_keys=True).encode()).hexdigest(),
                  'max_tokens':max_tokens,'temperature':self.config.temperature,
                  'reasoning':self.config.reasoning_effort,'extra':self.config.extra_body,
                  'json_mode':self.config.json_mode,'protocol':'reading-v6'}
        key=hashlib.sha256(json.dumps(material,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        async def generate():
            marker=_PURPOSE.set(purpose)
            try:
                raw=extract_json_object(await self._chat(messages,max_tokens=max_tokens,json_mode=True))
                if not raw.get('overview' if purpose=='digest' else 'summary'):
                    raise LLMError('模型缺少必需的正文，未缓存，请检查上游输出。')
                return normalize(raw)
            finally:_PURPOSE.reset(marker)
        if self.store is None:return await generate()
        async with self.store.lock(key):
            cached=self.store.get('model-result-v6',key)
            if cached is not None:return cached
            result=await generate()
            self.store.put('model-result-v6',key,result)
            return result

    async def summarize_paper(self, paper: dict[str, Any]) -> dict[str, Any]:
        system=PAPER_SYSTEM_PROMPT
        if self.config.system_prompt_extra:system+='\n'+self.config.system_prompt_extra
        result=await self._result('paper',[{'role':'system','content':system},
                    {'role':'user','content':build_paper_prompt(paper,self.profile)}],
                    min(5000,self.config.max_tokens),lambda raw:normalize_analysis(raw,paper,self.profile))
        # Publication and evidence are parsed source metadata, never model inventions.
        source=paper.get('source_material') or {}
        result=dict(result)
        result['publication']=source.get('publication',{'statement':'未核实发表或投稿状态','kind':'unverified','ranking':'未核实','source':'unverified'})
        result['evidence_level']=source.get('evidence_level','abstract')
        return result

    async def generate_digest(self, day: str, papers: list[dict[str, Any]]) -> dict[str, Any]:
        if not papers:return heuristic_digest(day,papers,self.profile)
        return await self._result('digest',[{'role':'system','content':DIGEST_SYSTEM_PROMPT},
                 {'role':'user','content':build_digest_prompt(day,papers,self.profile)}],
                 min(900,self.config.max_tokens),lambda raw:{
                     'headline':_clip(_text(raw.get('headline'),80),40),
                     'overview':_clip(_text(raw.get('overview'),600),240),
                     'source':'llm','prompt_version':'briefing-v6',
                     'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})

    async def test_connection(self) -> str:
        content = await self._chat(
            [
                {
                    "role": "user",
                    "content": "这是连接测试。请只回复：连接成功",
                }
            ],
            max_tokens=32,
            json_mode=False,
        )
        return content[:200]


def extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip().lstrip("\ufeff")
    if not text:
        raise LLMError("模型返回为空，无法解析 JSON。")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise LLMError("模型没有返回 JSON 对象。")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError:
                    cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        value = json.loads(cleaned)
                    except json.JSONDecodeError as exc:
                        raise LLMError(
                            f"模型返回的 JSON 格式不正确：{exc.msg}"
                        ) from exc
                if not isinstance(value, dict):
                    raise LLMError("模型返回的 JSON 顶层不是对象。")
                return value
    raise LLMError("模型返回的 JSON 不完整，可能被 max_tokens 截断。")


def _text(value: Any, limit: int = 1000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text[:limit]


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _as_list(value: Any, limit: int = 6) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[\n;；]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = _clip(_text(part, 200), 80)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        value = match.group(0) if match else value
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def _as_jargon(value: Any, limit: int = 5) -> list[dict[str, str]]:
    """Normalize the jargon glossary from forgiving model output shapes."""
    if isinstance(value, dict):
        items: list[Any] = [{"term": key, "plain": item} for key, item in value.items()]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        term = ""
        plain = ""
        why = ""
        example = ""
        if isinstance(item, dict):
            term = _text(item.get("term") or item.get("name") or item.get("word"), 100)
            plain = _text(
                item.get("plain")
                or item.get("explanation")
                or item.get("meaning")
                or item.get("description"),
                300,
            )
            why = _text(item.get("why") or item.get("importance") or item.get("role"), 200)
            example = _text(item.get("example") or item.get("instance"), 200)
        elif isinstance(item, str):
            match = re.match(r"^(.{1,80}?)[：:—–-]\s*(.+)$", item)
            if match:
                term, plain = match.group(1), match.group(2)
        term = _clip(term.strip(" ：:—-"), 60)
        plain = _clip(plain.strip(), 160)
        why = _clip(why.strip(), 120)
        example = _clip(example.strip(), 140)
        key = term.lower()
        if term and plain and key not in seen:
            seen.add(key)
            result.append(
                {"term": term, "plain": plain, "why": why, "example": example}
            )
        if len(result) >= limit:
            break
    return result


def _as_misconceptions(value: Any, limit: int = 3) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        misunderstanding = _clip(
            _text(
                item.get("misunderstanding")
                or item.get("myth")
                or item.get("mistake")
                or item.get("misread"),
                200,
            ),
            110,
        )
        clarify = _clip(
            _text(item.get("clarify") or item.get("clarification"), 240), 130
        )
        key = misunderstanding.lower()
        if misunderstanding and clarify and key not in seen:
            seen.add(key)
            result.append({"misunderstanding": misunderstanding, "clarify": clarify})
        if len(result) >= limit:
            break
    return result


def normalize_analysis(
    raw: dict[str, Any], paper: dict[str, Any], profile: ProfileConfig
) -> dict[str, Any]:
    tags = _as_list(raw.get("tags"), limit=6)
    if not tags:
        tags = _as_list(paper.get("keywords"), limit=5)
    title = _text(raw.get("title_zh") or paper.get("title"), 160)
    summary = _clip(_text(raw.get("summary"), 1600), 900)
    tldr = _clip(_text(raw.get("tldr"), 300), 140)
    if not summary:
        summary = _clip(paper.get("ai_summary") or paper.get("abstract"), 900)
    if not tldr:
        tldr = _clip(summary or paper.get("title", ""), 140)
    difficulty = _text(raw.get("difficulty"), 10)
    if difficulty not in {"入门", "进阶", "硬核"}:
        difficulty = "进阶"
    paper_type = _text(raw.get("paper_type"), 10)
    if paper_type not in {"方法", "模型", "评测", "数据集", "综述", "系统", "分析", "应用"}:
        paper_type = "分析"
    key_points = _as_list(raw.get("key_points"), limit=6)
    if not key_points:
        key_points = [tldr]
    how_it_works = _as_list(raw.get("how_it_works"), limit=5)
    how_it_works = [_clip(item, 180) for item in how_it_works]
    return {
        "title_zh": title,
        "tldr": tldr,
        "problem": _clip(_text(raw.get("problem"), 500), 300),
        "summary": summary,
        "analogy": _clip(_text(raw.get("analogy"), 500), 320),
        "how_it_works": how_it_works,
        "contrast": _clip(_text(raw.get("contrast"), 600), 380),
        "example": _clip(_text(raw.get("example"), 600), 380),
        "misconceptions": _as_misconceptions(raw.get("misconceptions")),
        "jargon": _as_jargon(raw.get("jargon")),
        "key_points": key_points,
        "method": _text(raw.get("method"), 2000),
        "findings": _clip(_text(raw.get("findings"), 500), 220),
        "why_it_matters": _clip(_text(raw.get("why_it_matters"), 500), 220),
        "limitations": _clip(_text(raw.get("limitations"), 500), 200),
        "takeaway": _clip(_text(raw.get("takeaway"), 400), 220),
        "tags": tags,
        "difficulty": difficulty,
        "read_minutes": _as_int(raw.get("read_minutes"), 12, 8, 30),
        "paper_type": paper_type,
        "code_available": bool(paper.get("github_url")),
        "source": "llm",
        "prompt_version": "reading-v6",
        "category": _clip(_text(raw.get("category"), 40), 40),
    }


def _plain(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text or "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _section(text: str, label: str) -> str:
    pattern = rf"\*?\*?{label}\*?\*?\s*[:：]\s*(.+?)(?=\n\s*\*?\*?(?:Purpose|Methods?|Results?|Conclusions?|背景|方法|结果|结论)\*?\*?\s*[:：]|$)"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return _plain(match.group(1)) if match else ""


def _sentences(text: str, count: int = 3) -> list[str]:
    text = _plain(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？.!?])\s+", text)
    if len(parts) == 1:
        parts = re.split(r"(?<=[。！？])", text)
    return [part.strip() for part in parts if part.strip()][:count]


def heuristic_analysis(
    paper: dict[str, Any], profile: ProfileConfig
) -> dict[str, Any]:
    abstract = paper.get("abstract") or ""
    ai_summary = paper.get("ai_summary") or ""
    purpose = _section(abstract, "Purpose")
    methods = _section(abstract, "Methods?")
    results = _section(abstract, "Results?")
    conclusion = _section(abstract, "Conclusions?")
    sentences = _sentences(abstract or ai_summary, 4)
    plain_summary = _plain(ai_summary) or _plain(abstract)
    key_points = [item for item in [purpose, methods, results, conclusion] if item][:4]
    if not key_points:
        key_points = sentences[:3] or [_clip(paper.get("title", ""), 100)]
    tags = _as_list(paper.get("keywords"), limit=6)
    if not tags:
        tags = ["HF Daily"]
    tldr = _clip(plain_summary or paper.get("title", ""), 140)
    problem = purpose or (sentences[0] if sentences else "")
    return {
        "title_zh": paper.get("title", ""),
        "tldr": tldr,
        "problem": _clip(problem, 200),
        "summary": _clip(plain_summary, 900) or "暂无摘要。",
        "analogy": "",
        "how_it_works": [],
        "contrast": "",
        "example": "",
        "misconceptions": [],
        "jargon": [],
        "key_points": [_clip(item, 120) for item in key_points],
        "method": _clip(methods, 160),
        "findings": _clip(results or conclusion, 220),
        "why_it_matters": "请结合论文的问题、方法和证据判断其一般用途；规则摘要不补充原文之外的结论。",
        "limitations": "尚未启用私有模型，本摘要由规则提取，未判断方法与证据质量。",
        "takeaway": _clip(results or conclusion or tldr, 220),
        "tags": tags,
        "difficulty": "进阶",
        "read_minutes": max(5, min(20, round(len(abstract.split()) / 180) + 5)),
        "paper_type": "分析",
        "code_available": bool(paper.get("github_url")),
        "source": "heuristic",
        "prompt_version": "heuristic-shared-v5",
    }


def _ids_for(value: Any, valid_ids: set[str], limit: int = 5) -> list[str]:
    if isinstance(value, str):
        values: list[Any] = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    for item in values:
        paper_id = item.get("id") if isinstance(item, dict) else item
        paper_id = str(paper_id or "").strip()
        if paper_id in valid_ids and paper_id not in result:
            result.append(paper_id)
        if len(result) >= limit:
            break
    return result


def _normalize_primer(raw: Any, valid_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        term = _clip(_text(item.get("term") or item.get("name"), 80), 40)
        plain = _clip(_text(item.get("plain") or item.get("definition"), 240), 120)
        if not term or not plain or term.lower() in seen:
            continue
        seen.add(term.lower())
        result.append(
            {
                "term": term,
                "plain": plain,
                "why": _clip(_text(item.get("why"), 200), 110),
                "example": _clip(_text(item.get("example"), 220), 120),
                "paper_ids": _ids_for(item.get("paper_ids"), valid_ids, limit=3),
            }
        )
        if len(result) >= 5:
            break
    return result


def _normalize_threads(raw: Any, valid_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _clip(_text(item.get("name") or item.get("theme"), 100), 50)
        problem = _clip(_text(item.get("problem"), 300), 150)
        approach = _clip(_text(item.get("approach"), 400), 220)
        if not name or not (problem or approach):
            continue
        result.append(
            {
                "name": name,
                "problem": problem,
                "approach": approach,
                "evidence": _clip(_text(item.get("evidence"), 260), 140),
                "boundary": _clip(
                    _text(item.get("boundary") or item.get("caveat"), 260), 140
                ),
                "paper_ids": _ids_for(item.get("paper_ids"), valid_ids, limit=6),
            }
        )
        if len(result) >= 4:
            break
    return result


def _normalize_reading_path(raw: Any, valid_ids: set[str]) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("id") or "").strip()
        if paper_id not in valid_ids or paper_id in seen:
            continue
        seen.add(paper_id)
        result.append(
            {
                "id": paper_id,
                "why": _clip(_text(item.get("why"), 240), 130),
                "before": _clip(_text(item.get("before"), 200), 110),
            }
        )
        if len(result) >= 5:
            break
    return result


def normalize_digest(
    raw: dict[str, Any], day: str, papers: list[dict[str, Any]]
) -> dict[str, Any]:
    valid_ids = {paper["id"] for paper in papers}
    sorted_papers = public_paper_order(papers)
    themes: list[dict[str, str]] = []
    for theme in raw.get("themes") or []:
        if isinstance(theme, dict):
            name = _clip(_text(theme.get("name"), 80), 40)
            summary = _clip(_text(theme.get("summary"), 220), 100)
        else:
            name = _clip(_text(theme, 80), 40)
            summary = ""
        if name:
            themes.append({"name": name, "summary": summary})
        if len(themes) >= 4:
            break

    primer = _normalize_primer(raw.get("primer"), valid_ids)
    threads = _normalize_threads(raw.get("threads"), valid_ids)
    reading_path = _normalize_reading_path(raw.get("reading_path"), valid_ids)
    if not themes:
        themes = (
            [{"name": item["name"], "summary": item["problem"]} for item in threads]
            if threads
            else _themes_from_papers(sorted_papers)
        )

    picks: list[dict[str, str]] = []
    for pick in raw.get("top_picks") or []:
        if isinstance(pick, dict):
            paper_id = _text(pick.get("id"), 80)
            reason = _clip(_text(pick.get("reason"), 220), 130)
        else:
            paper_id = _text(pick, 80)
            reason = ""
        if paper_id in valid_ids and paper_id not in {item["id"] for item in picks}:
            picks.append({"id": paper_id, "reason": reason or "当日推荐"})
        if len(picks) >= 5:
            break
    if not picks:
        for paper in sorted_papers[:3]:
            analysis = paper.get("analysis") or {}
            picks.append(
                {
                    "id": paper["id"],
                    "reason": analysis.get("tldr") or paper.get("ai_summary", ""),
                }
            )
    if not reading_path:
        reading_path = [
            {"id": pick["id"], "why": pick["reason"], "before": ""}
            for pick in picks[:5]
        ]

    order: list[str] = []
    for value in raw.get("reading_order") or []:
        paper_id = value.get("id") if isinstance(value, dict) else value
        paper_id = str(paper_id or "")
        if paper_id in valid_ids and paper_id not in order:
            order.append(paper_id)
    for item in reading_path:
        if item["id"] not in order:
            order.append(item["id"])
    for pick in picks:
        if pick["id"] not in order:
            order.append(pick["id"])
    for paper in sorted_papers:
        if paper["id"] not in order:
            order.append(paper["id"])

    headline = _clip(_text(raw.get("headline"), 160), 80)
    overview = _clip(_text(raw.get("overview"), 1400), 900)
    if not headline:
        headline = f"{day}：{len(papers)} 篇新论文"
    if not overview:
        overview = (
            f"今日 Hugging Face Daily Papers 共收录 {len(papers)} 篇论文，"
            "以下是当天研究主题与统一阅读建议。"
        )
    return {
        "headline": headline,
        "overview": overview,
        "primer": primer,
        "threads": threads,
        "themes": themes,
        "reading_path": reading_path,
        "top_picks": picks,
        "reading_order": order,
        "skip_hint": _clip(_text(raw.get("skip_hint"), 200), 100),
        "source": "llm",
        "prompt_version": "digest-shared-v5",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def heuristic_digest(
    day: str, papers: list[dict[str, Any]], profile: ProfileConfig
) -> dict[str, Any]:
    if not papers:
        return {
            "headline": f"{day}：今天还没有论文",
            "overview": "Hugging Face Daily Papers 当天暂无收录。可以稍后再试，或检查日期是否为周末/节假日。",
            "themes": [],
            "primer": [],
            "threads": [],
            "reading_path": [],
            "top_picks": [],
            "reading_order": [],
            "skip_hint": "",
            "source": "heuristic",
            "prompt_version": "digest-heuristic-shared-v5",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    sorted_papers = public_paper_order(papers)
    themes = _themes_from_papers(sorted_papers)
    top = sorted_papers[0]
    top_analysis = top.get("analysis") or {}
    headline = _clip(f"今日 {len(papers)} 篇，先看这 {max(1, min(3, len(themes)))} 个方向", 60)
    overview = (
        f"今日 Hugging Face Daily Papers 共收录 {len(papers)} 篇论文，"
        f"当前排在首位的是《{top_analysis.get('title_zh') or top.get('title', '')}》："
        f"{_clip(top_analysis.get('tldr') or top.get('ai_summary', ''), 130)}"
        " 当前为统一规则导读，阅读顺序参考社区热度。"
    )
    picks = []
    for paper in sorted_papers[:3]:
        analysis = paper.get("analysis") or {}
        picks.append(
            {
                "id": paper["id"],
                "reason": _clip(
                    analysis.get("why_it_matters")
                    or analysis.get("tldr")
                    or paper.get("ai_summary", ""),
                    130,
                ),
            }
        )
    reading_path = [
        {
            "id": paper["id"],
            "why": _clip(
                (paper.get("analysis") or {}).get("tldr")
                or paper.get("ai_summary", ""),
                130,
            ),
            "before": "",
        }
        for paper in sorted_papers[:3]
    ]
    return {
        "headline": headline,
        "overview": overview,
        "themes": themes,
        "primer": [],
        "threads": [],
        "reading_path": reading_path,
        "top_picks": picks,
        "reading_order": [paper["id"] for paper in sorted_papers],
        "skip_hint": "时间有限时先按统一阅读路径阅读；其余可先浏览标题和摘要。",
        "source": "heuristic",
        "prompt_version": "digest-heuristic-shared-v5",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _themes_from_papers(papers: list[dict[str, Any]]) -> list[dict[str, str]]:
    counts: dict[str, int] = {}
    for paper in papers:
        tags = (paper.get("analysis") or {}).get("tags") or paper.get("keywords") or []
        for tag in tags[:4]:
            name = str(tag).strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [
        {"name": name, "summary": f"今日有 {count} 篇论文涉及这一方向。"}
        for name, count in ordered[:3]
    ]


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("detail") or ""
            else:
                message = error or data.get("message") or data.get("detail") or ""
            if message:
                return _clip(str(message), 300)
    except ValueError:
        pass
    text = re.sub(r"<[^>]+>", " ", response.text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return _clip(text, 240)
