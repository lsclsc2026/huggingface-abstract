from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .config import FetchConfig
from .net import proxy_for

LOGGER = logging.getLogger(__name__)
HF_API = "https://huggingface.co/api/daily_papers"
HF_PAPER = "https://huggingface.co/papers"
GITHUB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s<>\"')]*)?",
    re.IGNORECASE,
)
ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
TRAILING_URL_CHARS = ".,;:!?，。；：！？）)]}》"


class HFError(RuntimeError):
    """A user-facing Hugging Face fetch error."""

    def __init__(
        self,
        message: str,
        *,
        latest_date: str = "",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.latest_date = latest_date
        self.status_code = status_code


def _response_detail(response: httpx.Response, limit: int = 300) -> str:
    """Extract a short, human-readable error without leaking huge HTML pages."""
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error") or payload.get("message") or payload.get("detail")
            if isinstance(error, dict):
                error = error.get("message") or error.get("detail") or error
            if error:
                detail = str(error)
    except ValueError:
        detail = response.text or ""
    if not detail:
        detail = response.text or ""
    detail = re.sub(r"<[^>]+>", " ", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    return detail[:limit]


def _extract_latest_hf_date(detail: str) -> str:
    patterns = (
        r"less than or equal to\s*[\"']?(\d{4}-\d{2}-\d{2})",
        r"maximum[^\d]{0,30}(\d{4}-\d{2}-\d{2})",
        r"latest[^\d]{0,30}(\d{4}-\d{2}-\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, detail, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _date_not_ready_error(day: str, latest_date: str, detail: str) -> HFError:
    if latest_date and day > latest_date:
        message = (
            f"Hugging Face 还没有发布 {day} 的 Daily Papers。"
            f"接口目前允许的最新日期是 {latest_date}。"
            f"请稍后再试，或先选择 {latest_date} 提取。"
        )
    elif latest_date:
        message = (
            f"Hugging Face API 不接受日期 {day}（最新可用日期：{latest_date}）。"
            "请检查日期，或改用最新可用日期。"
        )
    else:
        message = f"Hugging Face API 返回 HTTP 400：{detail or '请求被拒绝。'}"
    return HFError(message, latest_date=latest_date, status_code=400)


def normalize_daily_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convert both API and embedded-page daily paper objects into one shape."""
    if not isinstance(item, dict):
        return None
    paper = item.get("paper") if isinstance(item.get("paper"), dict) else item
    paper_id = str(paper.get("id") or "").strip()
    title = str(item.get("title") or paper.get("title") or "").strip()
    if not paper_id or not title:
        return None

    abstract = str(item.get("summary") or paper.get("summary") or "").strip()
    ai_summary = str(paper.get("ai_summary") or "").strip()
    keywords = paper.get("ai_keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]

    authors_raw = paper.get("authors") or []
    authors: list[str] = []
    if isinstance(authors_raw, list):
        for author in authors_raw:
            if isinstance(author, dict):
                name = str(author.get("name") or author.get("fullname") or "").strip()
            else:
                name = str(author).strip()
            if name:
                authors.append(name)

    organization = paper.get("organization") or item.get("organization") or {}
    if isinstance(organization, dict):
        org_name = str(
            organization.get("fullname")
            or organization.get("name")
            or organization.get("user")
            or ""
        ).strip()
    else:
        org_name = str(organization or "").strip()

    submitted_by = paper.get("submittedOnDailyBy") or {}
    submitted_by_name = ""
    if isinstance(submitted_by, dict):
        submitted_by_name = str(
            submitted_by.get("fullname") or submitted_by.get("name") or ""
        ).strip()

    project_page = str(
        paper.get("projectPage") or paper.get("project_page") or ""
    ).strip()
    github_url = find_github_url(abstract)
    if not github_url:
        github_url = find_github_url(project_page)

    published_at = str(
        paper.get("publishedAt") or item.get("publishedAt") or ""
    ).strip()
    submitted_on_daily = str(
        paper.get("submittedOnDailyAt")
        or item.get("submittedOnDailyAt")
        or item.get("publishedAt")
        or ""
    ).strip()
    daily_date = submitted_on_daily[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", daily_date):
        daily_date = published_at[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", daily_date):
        daily_date = date.today().isoformat()

    arxiv_url = f"https://arxiv.org/abs/{paper_id}" if ARXIV_ID_RE.match(paper_id) else ""
    pdf_url = f"https://arxiv.org/pdf/{paper_id}" if ARXIV_ID_RE.match(paper_id) else ""
    hf_url = f"{HF_PAPER}/{paper_id}"

    return {
        "id": paper_id,
        "daily_date": daily_date,
        "title": title,
        "abstract": abstract,
        "ai_summary": ai_summary,
        "keywords": keywords,
        "authors": authors,
        "organization": org_name,
        "published_at": published_at,
        "submitted_on_daily": submitted_on_daily,
        "upvotes": _safe_int(paper.get("upvotes") or item.get("upvotes")),
        "comments": _safe_int(paper.get("numComments") or item.get("numComments")),
        "thumbnail": str(item.get("thumbnail") or paper.get("thumbnail") or ""),
        "hf_url": hf_url,
        "arxiv_url": arxiv_url,
        "pdf_url": pdf_url,
        "github_url": github_url,
        "project_url": project_page if _is_http_url(project_page) else "",
        "submitted_by": submitted_by_name,
        "raw": {
            "ai_summary_model": paper.get("ai_summary_model", ""),
            "discussionId": paper.get("discussionId", ""),
            "linkedModels": paper.get("linkedModels", []),
            "linkedDatasets": paper.get("linkedDatasets", []),
            "linkedSpaces": paper.get("linkedSpaces", []),
            "media_urls": _media_urls(item.get("mediaUrls") or paper.get("mediaUrls")),
        },
    }


def _media_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        url = clean_url(str(item or ""))
        if url and _is_http_url(url) and url not in result:
            result.append(url)
    return result[:4]


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def clean_url(value: str) -> str:
    url = value.strip().strip("<>\"'")
    # Markdown authors often put punctuation directly after a URL.
    while url and url[-1] in TRAILING_URL_CHARS:
        url = url[:-1]
    if url.endswith(".git"):
        url = url[:-4]
    return url


def find_github_url(text: str) -> str:
    if not text:
        return ""
    for match in GITHUB_RE.finditer(text):
        url = clean_url(match.group(0))
        path = urlparse(url).path.strip("/")
        if not path or path.count("/") < 1:
            continue
        if path.lower() in {"huggingface", "huggingface/"}:
            continue
        return url
    return ""


def parse_daily_page(html: str) -> tuple[str, list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(attrs={"data-target": "DailyPapers"})
    if not isinstance(node, Tag):
        raise HFError("Hugging Face 页面结构发生变化，未能定位 Daily Papers 数据。")
    raw_props = node.get("data-props")
    if not isinstance(raw_props, str):
        raise HFError("Hugging Face 页面缺少论文数据。")
    try:
        props = json.loads(raw_props)
    except json.JSONDecodeError as exc:
        raise HFError("Hugging Face 页面数据解析失败。") from exc
    date_string = str(props.get("dateString") or "")
    items = props.get("dailyPapers") or []
    if not isinstance(items, list):
        items = []
    papers = [paper for item in items if (paper := normalize_daily_item(item))]
    return date_string, papers


def parse_detail_media(html: str) -> dict[str, str]:
    """Pick up a paper's social image or an embedded video when HF provides one."""
    soup = BeautifulSoup(html, "html.parser")
    media: dict[str, str] = {}
    for meta in soup.find_all("meta"):
        key = str(meta.get("property") or meta.get("name") or "").lower()
        content = clean_url(str(meta.get("content") or ""))
        if not content or not _is_http_url(content):
            continue
        if key in {"og:video", "og:video:url", "twitter:player:stream"}:
            media.setdefault("video_url", content)
        elif key in {"og:image", "twitter:image"} and "video_url" not in media:
            media.setdefault("image_url", content)
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src") or tag.get("data-src")
        if src:
            media.setdefault("video_url", clean_url(str(src)))
            break
    return media


def parse_detail_links(html: str) -> dict[str, str]:
    """Read the paper's action buttons (arXiv / PDF / GitHub / project)."""
    soup = BeautifulSoup(html, "html.parser")
    links: dict[str, str] = {}
    for anchor in soup.select("a[href]"):
        if not isinstance(anchor, Tag):
            continue
        classes = anchor.get("class") or []
        if "btn" not in classes:
            continue
        href = clean_url(str(anchor.get("href") or ""))
        if not _is_http_url(href):
            continue
        lowered = href.lower()
        if "github.com/" in lowered and "github_url" not in links:
            links["github_url"] = href
        elif "arxiv.org/abs/" in lowered and "arxiv_url" not in links:
            links["arxiv_url"] = href
        elif "arxiv.org/pdf/" in lowered and "pdf_url" not in links:
            links["pdf_url"] = href
        elif "huggingface.co/papers/" not in lowered and "arxiv.org" not in lowered:
            links.setdefault("project_url", href)
    return links


class HFClient:
    def __init__(self, config: FetchConfig) -> None:
        self.config = config
        self.base_url = (config.hf_base_url or "https://huggingface.co").rstrip("/")
        self.headers = {
            "User-Agent": config.user_agent,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        }
        self._pdf_locks: dict[str, asyncio.Lock] = {}

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers,
            timeout=timeout or self.config.timeout_seconds,
            follow_redirects=True,
            trust_env=False,
            proxy=proxy_for(self.base_url, self.config.proxy),
        )

    async def fetch_daily(
        self, day: str, enrich_details: bool | None = None
    ) -> list[dict[str, Any]]:
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise HFError(f"日期格式不正确：{day}") from exc

        api_error: Exception | None = None
        async with self._client() as client:
            for attempt in range(2):
                try:
                    response = await client.get(
                        f"{self.base_url}/api/daily_papers",
                        params={"date": day, "limit": self.config.max_papers},
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        if isinstance(payload, list):
                            papers = [
                                paper
                                for item in payload
                                if (paper := normalize_daily_item(item))
                            ]
                            papers = papers[: self.config.max_papers]
                            break
                    if response.status_code in {429, 500, 502, 503, 504}:
                        await asyncio.sleep(1.2 * (attempt + 1))
                        continue
                    detail = _response_detail(response)
                    if response.status_code == 400:
                        parsed_latest = _extract_latest_hf_date(detail)
                        latest_date = parsed_latest
                        if parsed_latest:
                            # The API edge cache can lag behind the date in the
                            # error body; verify by probing backwards.
                            latest_date = (
                                await self._find_latest_available_date(client, day)
                                or parsed_latest
                            )
                        LOGGER.warning(
                            "HF Daily date unavailable: requested=%s latest=%s detail=%s",
                            day,
                            latest_date or "unknown",
                            detail,
                        )
                        raise _date_not_ready_error(day, latest_date, detail)
                    raise HFError(
                        f"Hugging Face API 返回 HTTP {response.status_code}：{detail or '请求被拒绝。'}",
                        status_code=response.status_code,
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    api_error = exc
                    if attempt == 0:
                        await asyncio.sleep(1.0)
            else:
                papers = []
                if api_error:
                    LOGGER.warning("Daily API failed, falling back to page: %s", api_error)

        if not papers:
            # The API occasionally lags behind the rendered page; this also makes
            # historical dates resilient to API changes.
            papers = await self._fetch_daily_from_page(day)
        if not papers:
            return []

        should_enrich = self.config.detail_pages if enrich_details is None else enrich_details
        if should_enrich:
            await self._enrich_details(papers)
        return papers

    async def _find_latest_available_date(
        self, client: httpx.AsyncClient, requested_day: str, lookback_days: int = 7
    ) -> str:
        """Probe recent dates to give the user a useful fallback date.

        HF's error can be served from a stale cache, so the maximum date in the
        400 payload is not always the real latest. We only run this after a
        rejected "today" request; it is at most seven tiny requests.
        """
        current = date.fromisoformat(requested_day) - timedelta(days=1)
        for _ in range(lookback_days):
            try:
                response = await client.get(
                    f"{self.base_url}/api/daily_papers",
                    params={"date": current.isoformat(), "limit": 1},
                )
            except httpx.HTTPError:
                return ""
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, list) and payload:
                    return current.isoformat()
            current -= timedelta(days=1)
        return ""

    async def _fetch_daily_from_page(self, day: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/papers?date={day}"
        async with self._client() as client:
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                raise HFError(
                    "无法连接 Hugging Face，请检查网络或代理设置（fetch.proxy）。"
                ) from exc
        if response.status_code != 200:
            raise HFError(f"Hugging Face 页面返回 HTTP {response.status_code}。")
        _, papers = parse_daily_page(response.text)
        return papers[: self.config.max_papers]

    async def _enrich_details(self, papers: list[dict[str, Any]]) -> None:
        semaphore = asyncio.Semaphore(self.config.detail_concurrency)

        async def enrich(paper: dict[str, Any]) -> None:
            async with semaphore:
                try:
                    async with self._client(timeout=min(20, self.config.timeout_seconds)) as client:
                        response = await client.get(paper["hf_url"])
                    if response.status_code != 200:
                        return
                    links = parse_detail_links(response.text)
                    media = parse_detail_media(response.text)
                except (httpx.HTTPError, HFError):
                    return
                raw = paper.get("raw") if isinstance(paper.get("raw"), dict) else {}
                if media.get("image_url") and not paper.get("thumbnail"):
                    paper["thumbnail"] = media["image_url"]
                if media.get("video_url"):
                    raw["video_url"] = media["video_url"]
                    urls = raw.get("media_urls") if isinstance(raw.get("media_urls"), list) else []
                    if media["video_url"] not in urls:
                        raw["media_urls"] = [media["video_url"], *urls][:4]
                paper["raw"] = raw
                for key in ("github_url", "project_url"):
                    if not paper.get(key) and links.get(key):
                        paper[key] = links[key]
                if links.get("pdf_url"):
                    paper["pdf_url"] = links["pdf_url"]
                if links.get("arxiv_url"):
                    paper["arxiv_url"] = links["arxiv_url"]

        await asyncio.gather(*(enrich(paper) for paper in papers), return_exceptions=True)

    async def download_pdf(self, paper: dict[str, Any], target_dir: Path) -> Path:
        paper_id = str(paper["id"])
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", paper_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{safe_id}.pdf"
        if target.exists() and target.stat().st_size > 1000:
            return target
        lock = self._pdf_locks.setdefault(paper_id, asyncio.Lock())
        async with lock:
            if target.exists() and target.stat().st_size > 1000:
                return target
            url = paper.get("pdf_url") or ""
            if not url:
                raise HFError("这篇论文没有可用的 PDF 链接。")
            temp = target.with_suffix(".pdf.part")
            try:
                async with self._client(timeout=120) as client:
                    async with client.stream("GET", url) as response:
                        if response.status_code != 200:
                            raise HFError(
                                f"PDF 下载失败（HTTP {response.status_code}）。"
                            )
                        content_type = response.headers.get("content-type", "").lower()
                        if "pdf" not in content_type and "octet-stream" not in content_type:
                            raise HFError("下载地址没有返回 PDF 文件，可能被拦截或需要验证。")
                        total = 0
                        first_chunk = True
                        with temp.open("wb") as handle:
                            async for chunk in response.aiter_bytes(65536):
                                if first_chunk:
                                    first_chunk = False
                                    if not chunk.startswith(b"%PDF"):
                                        raise HFError("下载内容不是有效的 PDF 文件。")
                                total += len(chunk)
                                if total > 300 * 1024 * 1024:
                                    raise HFError("PDF 超过 300 MB，已停止下载。")
                                handle.write(chunk)
                if total < 1000:
                    raise HFError("PDF 文件为空或下载不完整。")
                temp.replace(target)
                return target
            except httpx.HTTPError as exc:
                raise HFError("PDF 下载网络错误，请稍后重试。") from exc
            finally:
                if temp.exists():
                    try:
                        temp.unlink()
                    except OSError:
                        pass


def paper_download_name(paper: dict[str, Any]) -> str:
    title = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", paper.get("title", ""))[:80]
    title = title.strip("_") or "paper"
    return f"{paper.get('id', 'paper')}_{title}.pdf"
