from __future__ import annotations

import json

import httpx
import pytest

from app.config import LLMConfig, ProfileConfig
from app.llm import (
    LLMClient,
    LLMError,
    extract_json_object,
    heuristic_analysis,
    heuristic_digest,
    normalize_analysis,
    normalize_digest,
    score_relevance,
)
from app.prompts import PAPER_SYSTEM_PROMPT, build_paper_prompt, build_digest_prompt


PROFILE = ProfileConfig(
    role="AI 工程师",
    interests=["Agent、工具调用", "强化学习与推理", "多模态视频生成"],
    favorite_topics=["Agent"],
    notes="关注可落地性。",
)


def paper(**overrides):
    data = {
        "id": "2611.00001",
        "daily_date": "2026-11-04",
        "title": "An Agent for Testing",
        "abstract": (
            "**Purpose:** We study tool-using agents. "
            "**Methods:** We train a small policy with reinforcement learning. "
            "**Results:** It improves success by 12% on a benchmark. "
            "**Conclusion:** Agent training is promising."
        ),
        "ai_summary": "A tool-using agent trained with RL.",
        "keywords": ["agents", "reinforcement learning"],
        "authors": ["A. Author"],
        "organization": "Example Lab",
        "upvotes": 10,
        "github_url": "https://github.com/example/agent",
    }
    data.update(overrides)
    return data


def test_extract_json_object_handles_fences_and_trailing_commas():
    text = 'Here you go:\n```json\n{"a": 1, "nested": {"b": 2},}\n```'
    assert extract_json_object(text) == {"a": 1, "nested": {"b": 2}}
    with pytest.raises(LLMError):
        extract_json_object("no object here")


def test_score_relevance_uses_specific_terms():
    score, matches = score_relevance(paper(), PROFILE)
    assert score >= 50
    assert any("Agent" in item for item in matches)


def test_config_does_not_use_generic_ai_as_interest_term():
    profile = ProfileConfig(interests=["AI 评测、安全对齐与可靠性"])
    terms = [term.lower() for term in profile.interest_terms()]
    assert "ai" not in terms
    assert "安全对齐" in terms


def test_heuristic_analysis_extracts_sections():
    analysis = heuristic_analysis(paper(), PROFILE)
    assert analysis["source"] == "heuristic"
    assert "tool-using agents" in analysis["key_points"][0]
    assert "12%" in analysis["findings"]
    assert analysis["code_available"] is True
    assert analysis["prompt_version"] == "heuristic-shared-v5"
    assert analysis["analogy"] == ""
    assert analysis["jargon"] == []


def test_heuristic_digest_has_valid_ids():
    papers = [paper(id="a"), paper(id="b", relevance=10)]
    for item in papers:
        item["analysis"] = heuristic_analysis(item, PROFILE)
    digest = heuristic_digest("2026-11-04", papers, PROFILE)
    ids = {"a", "b"}
    assert set(digest["reading_order"]) == ids
    assert all(pick["id"] in ids for pick in digest["top_picks"])


def test_normalize_digest_drops_unknown_ids_and_normalizes_themes():
    papers = [paper(id="a"), paper(id="b")]
    for item in papers:
        item["analysis"] = heuristic_analysis(item, PROFILE)
    raw = {
        "headline": "A day of agents",
        "overview": "Overview",
        "themes": [{"name": "Agents", "summary": "Many agents."}],
        "top_picks": [
            {"id": "does-not-exist", "reason": "bad"},
            {"id": "a", "reason": "good"},
        ],
        "reading_order": ["b", "missing"],
        "skip_hint": "skip",
    }
    digest = normalize_digest(raw, "2026-11-04", papers)
    assert [pick["id"] for pick in digest["top_picks"]] == ["a"]
    assert digest["reading_order"][0] == "b"
    assert set(digest["reading_order"]) == {"a", "b"}


@pytest.mark.asyncio
async def test_llm_retries_without_json_mode():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(
                400,
                json={"error": {"message": "response_format is unsupported"}},
            )
        content = {
            "title_zh": "一个用于测试的智能体",
            "tldr": "一个使用强化学习训练的工具智能体。",
            "problem": "很多工具智能体在陌生工具上容易失败，作者想降低训练门槛。",
            "summary": "研究工具智能体的训练方法，并在基准上取得了提升。",
            "analogy": "可以把它想成给新员工一套循序渐进的培训手册；类比的边界是真实工具环境更嘈杂。",
            "how_it_works": ["先收集工具调用轨迹", "用强化学习筛选成功路径", "在基准上验证迁移"],
            "contrast": "旧做法要针对每个工具写专用提示词；本文改成从轨迹里学习通用的工具使用策略。",
            "example": "假设一个客服 Agent 第一次使用退款工具；这是帮助理解的假设场景，不是论文实验。",
            "misconceptions": [
                {
                    "misunderstanding": "很多人会以为这只是把工具说明写进提示词。",
                    "clarify": "这里真正改变的是训练环境与可验证的任务链。",
                }
            ],
            "jargon": [
                {"term": "强化学习", "plain": "让模型通过试错和奖励信号学习，而不是只背标准答案。"},
                {"term": "benchmark", "plain": "大家用同一套测试题比较不同方法的表现。"},
            ],
            "takeaway": "工具智能体的训练数据质量可能比单纯扩大模型更重要。",
            "key_points": ["工具调用", "强化学习"],
            "method": "强化学习",
            "findings": "成功率提升 12%",
            "why_it_matters": "与 Agent 方向直接相关。",
            "limitations": "仅在单一基准上验证。",
            "relevance_score": 88,
            "tags": ["Agent", "强化学习"],
            "difficulty": "进阶",
            "read_minutes": 7,
            "paper_type": "方法",
            "highlight": True,
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(content, ensure_ascii=False)}}
                ]
            },
        )

    config = LLMConfig(
        base_url="http://mock.local/v1",
        api_key="test",
        model="mock",
        max_retries=0,
        json_mode="auto",
    )
    client = LLMClient(config, PROFILE)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        analysis = await client.summarize_paper(paper())
    finally:
        await client.close()
    assert analysis["source"] == "llm"
    assert "relevance_score" not in analysis
    assert analysis["prompt_version"] == "beginner-shared-v5"
    assert analysis["analogy"].startswith("可以把它想成")
    assert analysis["jargon"][0]["term"] == "强化学习"
    assert analysis["contrast"].startswith("旧做法")
    assert analysis["misconceptions"][0]["misunderstanding"].startswith("很多人")
    assert "critical_view" not in analysis
    assert "questions" not in analysis
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_llm_auth_error_is_clear():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    config = LLMConfig(
        base_url="http://mock.local/v1",
        api_key="wrong",
        model="mock",
        max_retries=0,
    )
    client = LLMClient(config, PROFILE)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMError, match="鉴权失败"):
            await client.summarize_paper(paper())
    finally:
        await client.close()


def test_normalize_beginner_fields_tolerates_loose_shapes():
    raw = {
        "title_zh": "测试",
        "tldr": "一句话。",
        "problem": "一个普通人能理解的问题。",
        "summary": "一段更长的解释。",
        "analogy": "像一个生活场景。",
        "how_it_works": "第一步\n第二步",
        "example": "一个假设场景，不是论文实验。",
        "jargon": ["token：模型处理文本的最小单位", "benchmark: 统一测试题"],
        "takeaway": "记住这一点。",
        "relevance_score": 70,
    }
    analysis = normalize_analysis(raw, paper(), PROFILE)
    assert analysis["how_it_works"] == ["第一步", "第二步"]
    assert len(analysis["jargon"]) == 2
    assert analysis["jargon"][0]["term"] == "token"
    assert analysis["takeaway"] == "记住这一点。"


def test_beginner_prompt_keeps_core_contract():
    for phrase in [
        "不是把英文摘要翻译",
        "一个主类比",
        "概念先行",
        "对照式解释",
        "绝不能编造",
        "1500-2200",
        "how_it_works",
        "jargon",
        "misconceptions",
        "contrast",
    ]:
        assert phrase in PAPER_SYSTEM_PROMPT


def test_normalize_digest_v3_rich_fields():
    papers = [paper(id="a"), paper(id="b")]
    for item in papers:
        item["analysis"] = heuristic_analysis(item, PROFILE)
    raw = {
        "headline": "从演示走向可靠",
        "overview": "今天的论文在解决可靠性问题。",
        "primer": [
            {
                "term": "长程任务",
                "plain": "需要很多步才能完成的任务。",
                "why": "步骤一多就容易忘掉目标。",
                "example": "改多处代码并跑通测试。",
                "paper_ids": ["a", "ghost"],
            }
        ],
        "threads": [
            {
                "name": "训练环境更真实",
                "problem": "模拟环境太浅。",
                "approach": "从真实轨迹合成任务。",
                "evidence": "新基准让模型掉分。",
                "boundary": "还不等于真实企业环境。",
                "paper_ids": ["a"],
            }
        ],
        "reading_path": [{"id": "a", "why": "先理解问题。", "before": "先懂长程任务。"}],
        "top_picks": [{"id": "a", "reason": "相关。"}],
    }
    digest = normalize_digest(raw, "2026-11-04", papers)
    assert digest["prompt_version"] == "digest-shared-v5"
    assert digest["primer"][0]["paper_ids"] == ["a"]
    assert digest["threads"][0]["name"] == "训练环境更真实"
    assert digest["threads"][0]["boundary"] == "还不等于真实企业环境。"
    assert digest["reading_path"][0]["before"] == "先懂长程任务。"
    assert digest["reading_order"][0] == "a"
    assert "questions" not in digest


def test_personal_profile_cannot_change_shared_prompts():
    other = ProfileConfig(role="医生", interests=["医学"], notes="只讲医疗例子",
                          output_language="English", favorite_topics=["biology"])
    assert build_paper_prompt(paper(), PROFILE) == build_paper_prompt(paper(), other)
    a = [paper(id="a", relevance=100, analysis={"relevance_score": 99}),
         paper(id="b", relevance=0, upvotes=20)]
    b = [paper(id="b", relevance=100, upvotes=20),
         paper(id="a", relevance=0, analysis={"relevance_score": 1})]
    assert build_digest_prompt("2026-11-05", a, PROFILE) == build_digest_prompt("2026-11-05", b, other)


def test_model_scores_and_personal_profile_are_not_stored_in_public_analysis():
    raw = {"summary": "Shared explanation", "relevance_score": 99, "highlight": True}
    first = normalize_analysis(raw, paper(), PROFILE)
    second = normalize_analysis(raw, paper(), ProfileConfig(interests=["vision"]))
    assert first == second
    assert "relevance_score" not in first
    assert "highlight" not in first


def test_rule_fallbacks_are_shared_across_profiles():
    other = ProfileConfig(interests=["vision"], notes="写成诗")
    assert heuristic_analysis(paper(), PROFILE) == heuristic_analysis(paper(), other)
    a = [paper(id='a', relevance=100), paper(id='b', relevance=0, upvotes=20)]
    b = [paper(id='b', relevance=100, upvotes=20), paper(id='a', relevance=0)]
    first = heuristic_digest('2026-11-05', a, PROFILE)
    second = heuristic_digest('2026-11-05', b, other)
    first.pop('generated_at', None)
    second.pop('generated_at', None)
    assert first == second


def test_relevance_ignores_popularity_hidden_favorites_and_model_fields():
    profile = ProfileConfig(interests=['Agent', '机器人'], favorite_topics=['vision'])
    original = paper(title='Agent systems', abstract='', keywords=[], ai_summary='', upvotes=0)
    changed = dict(original, upvotes=999, analysis={'relevance_score':99, 'tags':['机器人']})
    assert score_relevance(original, profile)[0] == 50
    assert score_relevance(changed, profile)[0] == 50
    assert score_relevance(paper(title='Vision', abstract='', keywords=[], ai_summary=''), profile)[0] == 0
    assert score_relevance(paper(title='Robotics', abstract='', keywords=[], ai_summary=''),
                           ProfileConfig(interests=['机器人']))[0] == 100


@pytest.mark.asyncio
async def test_actual_model_requests_are_identical_for_different_profiles():
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={'choices':[{'message':{'content':'{"summary":"Shared"}'}}]})
    for profile in [PROFILE, ProfileConfig(interests=['医学'], notes='用英语写', output_language='English')]:
        client = LLMClient(LLMConfig(api_key='test', model='mock'), profile)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await client.summarize_paper(paper())
            await client.generate_digest('2026-11-05', [paper()])
        finally:
            await client.close()
    assert bodies[0] == bodies[2]
    assert bodies[1] == bodies[3]
