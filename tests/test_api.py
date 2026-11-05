from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.main import create_app


def make_config(tmp_path: Path) -> Path:
    config = {
        "app": {
            "host": "127.0.0.1",
            "port": 8765,
            "open_browser": False,
            "timezone": "Asia/Shanghai",
        },
        "storage": {"data_dir": str(tmp_path / "data"), "cache_pdfs": False},
        "profile": {"interests": ["Agent"], "notes": "test"},
        "llm": {"enabled": False, "api_key": "", "model": "test"},
        "fetch": {"detail_pages": False},
        "digest": {"enabled": False},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_api_day_flags_and_export(tmp_path, monkeypatch):
    config_path = make_config(tmp_path)
    monkeypatch.setenv("HF_PAPER_CONFIG", str(config_path))
    app = create_app()
    with TestClient(app) as client:
        runtime = client.app.state.runtime
        runtime.db.upsert_papers(
            [
                {
                    "id": "2611.00001",
                    "daily_date": "2026-11-04",
                    "title": "A Local Paper",
                    "abstract": "Abstract.",
                    "authors": ["A. Author"],
                    "keywords": ["agent"],
                }
            ]
        )
        runtime.db.update_paper_analysis(
            "2611.00001",
            {
                "title_zh": "一篇本地论文",
                "tldr": "一句话。",
                "problem": "它想解决一个具体问题。",
                "summary": "这是更长的通俗解释。",
                "analogy": "可以把它想成整理书架。",
                "how_it_works": ["先分类", "再归档"],
                "contrast": "以前靠人工整理，现在按规则自动分类。",
                "misconceptions": [
                    {
                        "misunderstanding": "这只是在旧流程上换个名字。",
                        "clarify": "真正的变化在数据如何分类和校验。",
                    }
                ],
                "example": "假设一个本地场景，不是论文实验。",
                "jargon": [{"term": "token", "plain": "模型处理文本的最小单位"}],
                "key_points": ["要点一"],
                "method": "方法。",
                "findings": "结果。",
                "why_it_matters": "与 Agent 相关。",
                "limitations": "证据有限。",
                "takeaway": "记住这一点。",
                "relevance_score": 78,
                "tags": ["Agent"],
                "difficulty": "入门",
                "read_minutes": 10,
                "paper_type": "方法",
                "prompt_version": "beginner-v4",
            },
            "ok",
            "llm",
        )
        response = client.get("/api/days/2026-11-04")
        assert response.status_code == 200
        payload = response.json()
        assert payload["papers"][0]["id"] == "2611.00001"
        assert payload["digest"] is None
        assert payload["digest_status"] in {"pending", "collecting"}

        response = client.patch(
            "/api/papers/2611.00001",
            json={"bookmarked": True, "note": "local note"},
        )
        assert response.status_code == 200
        assert response.json()["paper"]["bookmarked"] is True
        assert "read" not in response.json()["paper"]

        response = client.get("/api/days/2026-11-04/export.md")
        assert response.status_code == 200
        assert "A Local Paper" in response.text
        assert "local note" in response.text
        assert "读之前，先认识这几个词" in response.text
        assert "可以把它想成整理书架" in response.text
        assert "假设一个本地场景" in response.text
        assert "和原来的做法比" in response.text
        assert "容易理解错的地方" in response.text

        response = client.get("/api/days/2026-11-04/export.json")
        assert response.status_code == 200
        assert response.json()["date"] == "2026-11-04"

        response = client.get("/api/history")
        assert response.json()["days"][0]["count"] == 1

        response = client.post("/api/runs", json={"date": "2999-01-01"})
        assert response.status_code == 400

        response = client.get("/api/papers/missing")
        assert response.status_code == 404

        response = client.post("/api/settings/test")
        assert response.status_code == 400

        response = client.get("/")
        assert response.status_code == 200
        assert "Paper Daily" in response.text


def test_profile_update_validates_and_rescores(tmp_path, monkeypatch):
    config_path = make_config(tmp_path)
    monkeypatch.setenv("HF_PAPER_CONFIG", str(config_path))
    app = create_app()
    with TestClient(app) as client:
        runtime = client.app.state.runtime
        runtime.db.upsert_papers(
            [
                {
                    "id": "p-agent",
                    "daily_date": "2026-11-04",
                    "title": "Agent Systems",
                    "abstract": "A paper about agents.",
                    "keywords": ["agent"],
                },
                {
                    "id": "p-vision",
                    "daily_date": "2026-11-04",
                    "title": "Vision Models",
                    "abstract": "A paper about vision.",
                    "keywords": ["vision"],
                },
            ]
        )
        response = client.put(
            "/api/profile",
            json={"interests": ["Agent"], "notes": "先讲结论"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["rescored"] == 2
        assert payload["settings"]["profile"]["interests"] == ["Agent"]
        assert payload["settings"]["profile"]["notes"] == "先讲结论"
        papers = client.get("/api/days/2026-11-04").json()["papers"]
        assert papers[0]["id"] == "p-agent"
        assert papers[0]["relevance"] > papers[1]["relevance"]

        too_many = [f"kw{i}" for i in range(13)]
        assert client.put("/api/profile", json={"interests": too_many}).status_code == 400
        assert client.put("/api/profile", json={"interests": ["x"]}).status_code == 400
        assert client.put(
            "/api/profile", json={"interests": ["Agent"], "notes": "长" * 301}
        ).status_code == 400
