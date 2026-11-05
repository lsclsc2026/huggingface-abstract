"""Deterministic personal keyword coverage; no generated text or popularity."""
from __future__ import annotations

import re
from typing import Any

from .config import ProfileConfig

# Explicit aliases are versioned rules, not model-generated expansion.
ALIASES = (
    ("agent", "agents", "agentic", "智能体"),
    ("工具调用", "tool calling", "tool use", "tool-using", "function calling"),
    ("强化学习", "reinforcement learning", "rl"),
    ("推理", "reasoning"),
    ("机器人", "robot", "robots", "robotics", "robotic"),
    ("检索增强生成", "检索增强", "rag", "retrieval augmented generation", "retrieval-augmented"),
    ("多模态", "multimodal", "multi-modal"),
    ("视频生成", "video generation", "text-to-video"),
    ("多模态视频生成", "multimodal video generation", "video generation", "text-to-video"),
    ("视觉", "vision", "visual"),
)


def _contains(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9 -]+", term):
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None
    return term in text


def score_relevance(paper: dict[str, Any], profile: ProfileConfig) -> tuple[int, list[str]]:
    """0–100 = matched interest terms / all interest terms, rounded.

    Only explicit UI interests participate. Hidden legacy favorites, style notes,
    community votes and model-generated fields never alter this personal score.
    """
    terms = ProfileConfig(interests=profile.interests).interest_terms()
    if not terms:
        return 0, []
    text = " ".join([
        str(paper.get("title") or ""), str(paper.get("abstract") or ""),
        str(paper.get("ai_summary") or ""),
        " ".join(str(x) for x in paper.get("keywords") or []),
    ]).casefold()
    matches = []
    for term in terms:
        needle = term.casefold()
        aliases = next((group for group in ALIASES if needle in group), (needle,))
        if any(_contains(text, alias) for alias in aliases):
            matches.append(term)
    return round(100 * len(matches) / len(terms)), matches
