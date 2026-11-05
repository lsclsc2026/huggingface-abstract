from __future__ import annotations

from pathlib import Path

from app.db import Database


def make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.init()
    return db


def test_papers_flags_and_search(tmp_path):
    db = make_db(tmp_path)
    db.upsert_papers(
        [
            {
                "id": "p1",
                "daily_date": "2026-11-04",
                "title": "Agent Systems",
                "abstract": "A paper about agents.",
                "authors": ["A"],
                "keywords": ["agent"],
                "upvotes": 3,
            },
            {
                "id": "p2",
                "daily_date": "2026-11-04",
                "title": "Vision Models",
                "abstract": "A paper about vision.",
                "authors": ["B"],
                "keywords": ["vision"],
            },
        ]
    )
    papers = db.get_papers_for_date("2026-11-04")
    assert len(papers) == 2
    summary = db.day_summary("2026-11-04")
    assert summary["total"] == 2
    updated = db.update_paper_flags("p1", bookmarked=True, note="note")
    assert updated and updated["bookmarked"] is True and "read" not in updated
    assert db.search("agents")[0]["id"] == "p1"
    assert db.list_days()[0]["count"] == 2


def test_runs_and_digest(tmp_path):
    db = make_db(tmp_path)
    run = db.create_run("2026-11-04", False)
    assert run["status"] == "queued"
    db.update_run(run["id"], status="running", stage="fetching", total=3)
    db.update_run(run["id"], status="completed", stage="done", processed=3)
    db.set_digest(run["id"], {"headline": "Hello"})
    digest = db.digest_for_day("2026-11-04")
    assert digest["headline"] == "Hello"
    assert db.active_run() is None
    assert db.latest_run("2026-11-04")["status"] == "completed"


def test_stale_running_jobs_are_interrupted(tmp_path):
    db = make_db(tmp_path)
    run = db.create_run("2026-11-04", False)
    db.update_run(run["id"], status="running")
    db.init()
    assert db.get_run(run["id"])["status"] == "interrupted"
