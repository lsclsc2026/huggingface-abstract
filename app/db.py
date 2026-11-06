from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .public import ANALYSIS_FIELDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    daily_date TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    ai_summary TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    authors TEXT NOT NULL DEFAULT '[]',
    organization TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    submitted_on_daily TEXT NOT NULL DEFAULT '',
    upvotes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    thumbnail TEXT NOT NULL DEFAULT '',
    hf_url TEXT NOT NULL DEFAULT '',
    arxiv_url TEXT NOT NULL DEFAULT '',
    pdf_url TEXT NOT NULL DEFAULT '',
    github_url TEXT NOT NULL DEFAULT '',
    project_url TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    analysis TEXT,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_source TEXT NOT NULL DEFAULT '',
    analysis_error TEXT NOT NULL DEFAULT '',
    relevance REAL NOT NULL DEFAULT 0,
    read INTEGER NOT NULL DEFAULT 0,
    bookmarked INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    pdf_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(daily_date);
CREATE INDEX IF NOT EXISTS idx_papers_relevance ON papers(daily_date, relevance DESC);
CREATE INDEX IF NOT EXISTS idx_papers_bookmarked ON papers(bookmarked);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'queued',
    total INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    digest TEXT,
    llm_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(date, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_comments (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    content TEXT NOT NULL,
    visitor_hash TEXT NOT NULL,
    masked_ip TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_site_comments_paper ON site_comments(paper_id, seq DESC);
CREATE TABLE IF NOT EXISTS comment_limits (
    visitor_hash TEXT PRIMARY KEY,
    last_posted_at REAL NOT NULL,
    window_start REAL NOT NULL,
    post_count INTEGER NOT NULL
);

"""

PAPER_COLUMNS = {
    "id",
    "daily_date",
    "title",
    "abstract",
    "ai_summary",
    "keywords",
    "authors",
    "organization",
    "published_at",
    "submitted_on_daily",
    "upvotes",
    "comments",
    "thumbnail",
    "hf_url",
    "arxiv_url",
    "pdf_url",
    "github_url",
    "project_url",
    "raw_json",
    "analysis",
    "analysis_status",
    "analysis_source",
    "analysis_error",
    "relevance",
    "read",
    "bookmarked",
    "note",
    "pdf_path",
    "updated_at",
}

RUN_COLUMNS = {
    "status",
    "stage",
    "total",
    "processed",
    "failed",
    "message",
    "digest",
    "finished_at",
    "updated_at",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            now = utc_now()
            conn.execute(
                """
                UPDATE runs
                SET status = 'interrupted', stage = 'stopped',
                    message = '程序重启，任务已中断。再次点击提取即可继续。',
                    updated_at = ?, finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (now, now),
            )

    # ------------------------------------------------------------------ papers
    def upsert_papers(self, papers: list[dict[str, Any]]) -> None:
        if not papers:
            return
        now = utc_now()
        with self.connect() as conn:
            for paper in papers:
                conn.execute(
                    """
                    INSERT INTO papers (
                        id, daily_date, title, abstract, ai_summary, keywords,
                        authors, organization, published_at, submitted_on_daily,
                        upvotes, comments, thumbnail, hf_url, arxiv_url, pdf_url,
                        github_url, project_url, raw_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        daily_date = excluded.daily_date,
                        title = excluded.title,
                        abstract = excluded.abstract,
                        ai_summary = excluded.ai_summary,
                        keywords = excluded.keywords,
                        authors = excluded.authors,
                        organization = excluded.organization,
                        published_at = excluded.published_at,
                        submitted_on_daily = excluded.submitted_on_daily,
                        upvotes = excluded.upvotes,
                        comments = excluded.comments,
                        thumbnail = excluded.thumbnail,
                        hf_url = excluded.hf_url,
                        arxiv_url = excluded.arxiv_url,
                        pdf_url = excluded.pdf_url,
                        github_url = CASE
                            WHEN excluded.github_url != '' THEN excluded.github_url
                            ELSE papers.github_url
                        END,
                        project_url = CASE
                            WHEN excluded.project_url != '' THEN excluded.project_url
                            ELSE papers.project_url
                        END,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        paper["id"],
                        paper["daily_date"],
                        paper.get("title", ""),
                        paper.get("abstract", ""),
                        paper.get("ai_summary", ""),
                        json.dumps(paper.get("keywords", []), ensure_ascii=False),
                        json.dumps(paper.get("authors", []), ensure_ascii=False),
                        paper.get("organization", ""),
                        paper.get("published_at", ""),
                        paper.get("submitted_on_daily", ""),
                        int(paper.get("upvotes", 0) or 0),
                        int(paper.get("comments", 0) or 0),
                        paper.get("thumbnail", ""),
                        paper.get("hf_url", ""),
                        paper.get("arxiv_url", ""),
                        paper.get("pdf_url", ""),
                        paper.get("github_url", ""),
                        paper.get("project_url", ""),
                        json.dumps(paper.get("raw", {}), ensure_ascii=False),
                        now,
                        now,
                    ),
                )

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return _paper_row(row) if row else None

    def get_papers_for_date(self, day: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM papers WHERE daily_date = ?
                ORDER BY upvotes DESC, id ASC
                """,
                (day,),
            ).fetchall()
        return [_paper_row(row) for row in rows]

    def day_summary(self, day: str) -> dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN bookmarked = 1 THEN 1 ELSE 0 END) AS bookmarked_count,
                    SUM(CASE WHEN github_url != '' THEN 1 ELSE 0 END) AS github_count,
                    SUM(CASE WHEN pdf_path != '' THEN 1 ELSE 0 END) AS pdf_count,
                    SUM(CASE WHEN analysis_status = 'failed' THEN 1 ELSE 0 END) AS failed_count
                FROM papers WHERE daily_date = ?
                """,
                (day,),
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "bookmarked": int(row["bookmarked_count"] or 0),
            "with_github": int(row["github_count"] or 0),
            "pdf_cached": int(row["pdf_count"] or 0),
            "failed": int(row["failed_count"] or 0),
        }

    def update_paper_analysis(
        self,
        paper_id: str,
        analysis: dict[str, Any],
        status: str,
        source: str,
        error: str = "",
    ) -> None:
        analysis = {key: value for key, value in analysis.items()
                    if key not in {"relevance_score", "highlight"}}
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE papers
                SET analysis = ?, analysis_status = ?, analysis_source = ?,
                    analysis_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(analysis, ensure_ascii=False),
                    status,
                    source,
                    error[:2000],
                    utc_now(),
                    paper_id,
                ),
            )

    def update_paper_flags(
        self,
        paper_id: str,
        *,
        bookmarked: bool | None = None,
        note: str | None = None,
        github_url: str | None = None,
    ) -> dict[str, Any] | None:
        fields: list[str] = []
        values: list[Any] = []
        if bookmarked is not None:
            fields.append("bookmarked = ?")
            values.append(1 if bookmarked else 0)
        if note is not None:
            fields.append("note = ?")
            values.append(note[:10000])
        if github_url is not None:
            fields.append("github_url = ?")
            values.append(github_url[:1000])
        if not fields:
            return self.get_paper(paper_id)
        fields.append("updated_at = ?")
        values.append(utc_now())
        values.append(paper_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE papers SET {', '.join(fields)} WHERE id = ?", values
            )
        return self.get_paper(paper_id)

    def set_pdf_path(self, paper_id: str, path: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE papers SET pdf_path = ?, updated_at = ? WHERE id = ?",
                (path, utc_now(), paper_id),
            )

    def update_metadata(
        self, paper_id: str, *, github_url: str = "", project_url: str = ""
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE papers SET
                    github_url = CASE WHEN ? != '' THEN ? ELSE github_url END,
                    project_url = CASE WHEN ? != '' THEN ? ELSE project_url END,
                    updated_at = ?
                WHERE id = ?
                """,
                (github_url, github_url, project_url, project_url, utc_now(), paper_id),
            )

    def all_papers(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM papers ORDER BY daily_date DESC").fetchall()
        return [_paper_row(row) for row in rows]

    def update_relevance(self, paper_id: str, relevance: float) -> None:
        relevance = max(0.0, min(100.0, float(relevance)))
        with self.connect() as conn:
            conn.execute(
                "UPDATE papers SET relevance = ?, updated_at = ? WHERE id = ?",
                (relevance, utc_now(), paper_id),
            )

    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def search(self, query: str, limit: int = 80) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        # Only public analysis values may determine an anonymous search match.
        paths = ', '.join("'$." + field + "'" for field in ANALYSIS_FIELDS)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM papers
                WHERE title LIKE ? OR abstract LIKE ? OR
                      json_extract(CASE WHEN json_valid(analysis) THEN analysis ELSE '{{}}' END, {paths}) LIKE ?
                ORDER BY daily_date DESC, upvotes DESC, id ASC
                LIMIT ?
                """,
                (pattern, pattern, pattern, max(1, min(limit, 200))),
            ).fetchall()
        return [_paper_row(row) for row in rows]

    def list_days(self, limit: int = 90) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT daily_date AS date, COUNT(*) AS count,
                       SUM(CASE WHEN bookmarked = 1 THEN 1 ELSE 0 END) AS bookmarked
                FROM papers
                GROUP BY daily_date
                ORDER BY daily_date DESC
                LIMIT ?
                """,
                (max(1, min(limit, 365)),),
            ).fetchall()
        return [
            {
                "date": row["date"],
                "count": int(row["count"]),
                "bookmarked": int(row["bookmarked"] or 0),
            }
            for row in rows
        ]

    # -------------------------------------------------------------------- runs
    def create_run(self, day: str, llm_enabled: bool) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, date, status, stage, llm_enabled, created_at, updated_at)
                VALUES (?, ?, 'queued', 'queued', ?, ?, ?)
                """,
                (run_id, day, 1 if llm_enabled else 0, now, now),
            )
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run_row(row) if row else None

    def active_run(self, day: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM runs WHERE status IN ('queued', 'running')"
        params: tuple[Any, ...] = ()
        if day:
            query += " AND date = ?"
            params = (day,)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
        return _run_row(row) if row else None

    def latest_run(self, day: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE date = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (day,),
            ).fetchone()
        return _run_row(row) if row else None

    def update_run(self, run_id: str, **fields: Any) -> None:
        updates: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in RUN_COLUMNS:
                raise ValueError(f"Unsupported run field: {key}")
            if key == "digest" and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            updates.append(f"{key} = ?")
            values.append(value)
        if not updates:
            return
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(run_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE id = ?", values
            )

    def set_digest(self, run_id: str, digest: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET digest = ?, updated_at = ? WHERE id = ?",
                (json.dumps(digest, ensure_ascii=False), utc_now(), run_id),
            )

    def digest_for_day(self, day: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT digest FROM runs
                WHERE date = ? AND digest IS NOT NULL AND digest != ''
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (day,),
            ).fetchone()
        return _json_value(row["digest"], None) if row else None


def _paper_row(row: sqlite3.Row) -> dict[str, Any]:
    paper = dict(row)
    paper.pop("read", None)
    paper["keywords"] = _json_value(paper.get("keywords"), [])
    paper["authors"] = _json_value(paper.get("authors"), [])
    paper["raw"] = _json_value(paper.pop("raw_json", None), {})
    paper["analysis"] = _json_value(paper.get("analysis"), None)
    if isinstance(paper["analysis"], dict):
        # Legacy records can contain model/person-specific scores. Keep stored
        # text intact, but never expose those fields as shared content.
        paper["analysis"].pop("relevance_score", None)
        paper["analysis"].pop("highlight", None)
    paper["bookmarked"] = bool(paper.get("bookmarked"))
    paper["relevance"] = float(paper.get("relevance") or 0)
    paper["has_pdf"] = bool(paper.get("pdf_path"))
    return paper


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    run = dict(row)
    run["digest"] = _json_value(run.get("digest"), None)
    run["llm_enabled"] = bool(run.get("llm_enabled"))
    return run
