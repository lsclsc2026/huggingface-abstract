from __future__ import annotations

import html
import json

import httpx
import pytest

from app.config import FetchConfig
from app.hf import (
    HFClient,
    HFError,
    _date_not_ready_error,
    _extract_latest_hf_date,
    clean_url,
    find_github_url,
    normalize_daily_item,
    parse_daily_page,
    parse_detail_links,
    parse_detail_media,
    paper_download_name,
)


def test_normalize_item_extracts_links_and_authors():
    item = {
        "paper": {
            "id": "2611.03360",
            "title": "Every Pixel Is an Option",
            "authors": [{"name": "Ada Lovelace"}, {"name": "Alan Turing"}],
            "publishedAt": "2026-11-02T00:00:00.000Z",
            "submittedOnDailyAt": "2026-11-04T00:00:00.000Z",
            "summary": "Code: https://github.com/example/paper.",
            "upvotes": 7,
            "numComments": 2,
            "ai_keywords": ["vision", "biometry"],
            "organization": {"fullname": "Example University"},
        },
        "title": "Every Pixel Is an Option",
        "thumbnail": "https://example.com/thumb.png",
    }
    paper = normalize_daily_item(item)
    assert paper is not None
    assert paper["id"] == "2611.03360"
    assert paper["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert paper["organization"] == "Example University"
    assert paper["github_url"] == "https://github.com/example/paper"
    assert paper["arxiv_url"] == "https://arxiv.org/abs/2611.03360"
    assert paper["pdf_url"] == "https://arxiv.org/pdf/2611.03360"
    assert paper["daily_date"] == "2026-11-04"
    assert paper["upvotes"] == 7


def test_find_github_url_handles_markdown_and_punctuation():
    assert find_github_url("see (https://github.com/a/b).") == "https://github.com/a/b"
    assert find_github_url("https://github.com/org/repo/tree/main?x=1") == (
        "https://github.com/org/repo/tree/main?x=1"
    )
    assert clean_url("https://github.com/a/b.git") == "https://github.com/a/b"
    assert find_github_url("no link") == ""


def test_parse_daily_page_from_embedded_props():
    payload = {
        "dateString": "2026-11-04",
        "dailyPapers": [
            {
                "paper": {
                    "id": "2611.00001",
                    "title": "A Test Paper",
                    "summary": "A summary.",
                    "authors": [{"name": "A. Author"}],
                }
            }
        ],
    }
    page = (
        '<div data-target="DailyPapers" data-props="'
        + html.escape(json.dumps(payload), quote=True)
        + '"></div>'
    )
    date_string, papers = parse_daily_page(page)
    assert date_string == "2026-11-04"
    assert len(papers) == 1
    assert papers[0]["title"] == "A Test Paper"


def test_parse_detail_links_prefers_action_buttons():
    page = """
    <a href="https://github.com/huggingface">nav</a>
    <a class="btn inline-flex" href="https://arxiv.org/abs/2611.00001">arXiv</a>
    <a class="btn inline-flex" href="https://arxiv.org/pdf/2611.00001">PDF</a>
    <a class="btn inline-flex" href="https://github.com/foo/bar">GitHub</a>
    <a class="btn inline-flex" href="https://foo.example/project">Project</a>
    """
    links = parse_detail_links(page)
    assert links["arxiv_url"] == "https://arxiv.org/abs/2611.00001"
    assert links["pdf_url"] == "https://arxiv.org/pdf/2611.00001"
    assert links["github_url"] == "https://github.com/foo/bar"
    assert links["project_url"] == "https://foo.example/project"


def test_pdf_filename_is_safe():
    name = paper_download_name({"id": "2611.00001", "title": "A/B: Test Paper?"})
    assert name.endswith(".pdf")
    assert "/" not in name
    assert name.startswith("2611.00001_")


def test_hf_date_error_is_friendly_and_structured():
    detail = '✖ "date" must be less than or equal to "2026-11-04T00:00:00.000Z"'
    assert _extract_latest_hf_date(detail) == "2026-11-04"
    error = _date_not_ready_error("2026-11-05", "2026-11-04", detail)
    assert isinstance(error, HFError)
    assert "还没有发布 2026-11-05" in str(error)
    assert "2026-11-04" in str(error)
    assert error.latest_date == "2026-11-04"
    assert error.status_code == 400


@pytest.mark.asyncio
async def test_hf_probes_latest_available_date():
    def handler(request: httpx.Request) -> httpx.Response:
        day = request.url.params.get("date")
        if day == "2026-11-04":
            return httpx.Response(200, json=[{"paper": {"id": "1", "title": "ok"}}])
        return httpx.Response(
            400,
            json={"error": 'date must be less than or equal to "2026-11-03"'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found = await HFClient(FetchConfig(detail_pages=False))._find_latest_available_date(
            client, "2026-11-05"
        )
    assert found == "2026-11-04"


def test_parse_detail_media_reads_social_image_and_video():
    page = """
    <meta property="og:image" content="https://cdn.example/paper.png">
    <meta property="og:video" content="https://cdn.example/paper.mp4">
    """
    media = parse_detail_media(page)
    assert media["image_url"] == "https://cdn.example/paper.png"
    assert media["video_url"] == "https://cdn.example/paper.mp4"


def daily_html(day, ids):
    payload = {"dateString": day, "dailyPapers": [
        {"paper": {"id": paper_id, "title": f"Paper {paper_id}",
                   "publishedAt": "2026-10-01T00:00:00Z"}}
        for paper_id in ids
    ]}
    return '<div data-target="DailyPapers" data-props="' + html.escape(
        json.dumps(payload), quote=True
    ) + '"></div>'


def mock_hf(monkeypatch, handler):
    # Mock only HTTP; exercise actual parsing, merging and error handling.
    monkeypatch.setattr(HFClient, "_client", lambda self, timeout=None:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return HFClient(FetchConfig(detail_pages=False))


@pytest.mark.asyncio
async def test_fetch_merges_partial_api_with_page_and_assigns_requested_day(monkeypatch):
    def handler(request):
        if request.url.path.startswith('/api/'):
            return httpx.Response(200, json=[{"paper": {
                "id": "a", "title": "Paper a", "publishedAt": "2026-10-01T00:00:00Z"
            }}])
        return httpx.Response(200, text=daily_html("2026-11-05", ["a", "b"]))
    papers = await mock_hf(monkeypatch, handler).fetch_daily("2026-11-05")
    assert {p['id'] for p in papers} == {'a', 'b'}
    assert len(papers) == 2
    assert {p['daily_date'] for p in papers} == {'2026-11-05'}


@pytest.mark.asyncio
async def test_fetch_keeps_api_when_page_unavailable(monkeypatch):
    def handler(request):
        if request.url.path.startswith('/api/'):
            return httpx.Response(200, json=[{"paper": {"id": "a", "title": "A"}}])
        return httpx.Response(429)
    papers = await mock_hf(monkeypatch, handler).fetch_daily("2026-11-05")
    assert [p['id'] for p in papers] == ['a']


@pytest.mark.asyncio
async def test_fetch_keeps_page_when_api_rejects_request(monkeypatch):
    def handler(request):
        if request.url.path.startswith('/api/'):
            return httpx.Response(403)
        return httpx.Response(200, text=daily_html("2026-11-05", ["b"]))
    papers = await mock_hf(monkeypatch, handler).fetch_daily("2026-11-05")
    assert [p['id'] for p in papers] == ['b']


@pytest.mark.asyncio
async def test_fetch_rejects_page_for_another_day(monkeypatch):
    def handler(request):
        if request.url.path.startswith('/api/'):
            return httpx.Response(200, json=[])
        return httpx.Response(200, text=daily_html("2026-11-04", ["wrong"]))
    with pytest.raises(HFError, match="日期"):
        await mock_hf(monkeypatch, handler).fetch_daily("2026-11-05")


@pytest.mark.asyncio
async def test_fetch_fails_explicitly_if_both_sources_fail(monkeypatch):
    def handler(request):
        return httpx.Response(403)
    with pytest.raises(HFError):
        await mock_hf(monkeypatch, handler).fetch_daily("2026-11-05")
