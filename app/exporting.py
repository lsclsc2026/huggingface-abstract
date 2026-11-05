from __future__ import annotations

import json
from typing import Any

from .config import Settings


def build_markdown(
    day: str, papers: list[dict[str, Any]], digest: dict[str, Any] | None, settings: Settings
) -> str:
    papers = sorted(
        papers, key=lambda item: float(item.get("relevance") or 0), reverse=True
    )
    lines: list[str] = [
        f"# Hugging Face Daily Papers · {day}",
        "",
        f"> 共 {len(papers)} 篇 · 由 Paper Daily 导出 · "
        f"模型：{settings.llm.model if settings.llm.is_configured else '未配置（规则摘要）'}",
        "",
    ]
    if digest:
        lines.extend(
            [
                "## 今日导读",
                "",
                f"### {digest.get('headline', '')}",
                "",
                str(digest.get("overview", "")),
                "",
            ]
        )
        by_id = {paper["id"]: paper for paper in papers}
        primer = digest.get("primer") or []
        if primer:
            lines.extend(["### 先补这几个概念", ""])
            for item in primer:
                lines.append(
                    f"- **{item.get('term', '')}**：{item.get('plain', '')}"
                    + (f"（为什么重要：{item.get('why')}）" if item.get("why") else "")
                )
                if item.get("example"):
                    lines.append(f"  - 例子：{item['example']}")
            lines.append("")
        threads = digest.get("threads") or []
        if threads:
            lines.extend(["### 今天到底在解决什么", ""])
            for index, thread in enumerate(threads, 1):
                lines.append(f"#### {index}. {thread.get('name', '')}")
                lines.append("")
                if thread.get("problem"):
                    lines.append(f"- **原来卡在**：{thread['problem']}")
                if thread.get("approach"):
                    lines.append(f"- **今天怎么做**：{thread['approach']}")
                if thread.get("evidence"):
                    lines.append(f"- **证据**：{thread['evidence']}")
                boundary = thread.get("boundary") or thread.get("caveat")
                if boundary:
                    lines.append(f"- **适用边界**：{boundary}")
                refs = []
                for paper_id in thread.get("paper_ids") or []:
                    paper = by_id.get(paper_id)
                    if paper:
                        refs.append(
                            f"[{(paper.get('analysis') or {}).get('title_zh') or paper['title']}]({paper.get('hf_url', '')})"
                        )
                if refs:
                    lines.append(f"- **相关论文**：{' · '.join(refs)}")
                lines.append("")
        elif digest.get("themes"):
            lines.extend(["### 今日主题", ""])
            for theme in digest["themes"]:
                lines.append(f"- **{theme.get('name', '')}**：{theme.get('summary', '')}")
            lines.append("")
        reading_path = digest.get("reading_path") or []
        if reading_path:
            lines.extend(["### 建议阅读路径", ""])
            for index, item in enumerate(reading_path, 1):
                paper = by_id.get(item.get("id", ""))
                if not paper:
                    continue
                title = (paper.get("analysis") or {}).get("title_zh") or paper["title"]
                lines.append(f"{index}. **{title}** — {item.get('why', '')}")
                if item.get("before"):
                    lines.append(f"   - 先理解：{item['before']}")
                lines.append(f"   <{paper.get('hf_url', '')}>")
            lines.append("")
        picks = digest.get("top_picks") or []
        if picks and not reading_path:
            lines.extend(["### 优先阅读", ""])
            for index, pick in enumerate(picks, 1):
                paper = by_id.get(pick.get("id", ""))
                if not paper:
                    continue
                title = (paper.get("analysis") or {}).get("title_zh") or paper["title"]
                lines.append(f"{index}. **{title}** — {pick.get('reason', '')}")
                lines.append(f"   <{paper.get('hf_url', '')}>")
            lines.append("")
        if digest.get("skip_hint"):
            lines.extend([f"> 时间有限：{digest['skip_hint']}", ""])
        lines.extend(["---", ""])

    for index, paper in enumerate(papers, 1):
        analysis = paper.get("analysis") or {}
        title = analysis.get("title_zh") or paper.get("title", "")
        lines.extend(
            [
                f"## {index}. {title}",
                "",
                f"**原标题**：{paper.get('title', '')}",
                "",
                f"**相关度**：{int(float(paper.get('relevance') or 0))}/100 · "
                f"**阅读时长**：约 {analysis.get('read_minutes', 12)} 分钟 · "
                f"**类型**：{analysis.get('paper_type', '')} · "
                f"**难度**：{analysis.get('difficulty', '')}",
                "",
                f"> {analysis.get('tldr', '')}",
                "",
            ]
        )
        jargon = analysis.get("jargon") or []
        if jargon:
            lines.extend(["**读之前，先认识这几个词**", ""])
            for item in jargon:
                if isinstance(item, dict):
                    line = f"- **{item.get('term', '')}**：{item.get('plain', '')}"
                    if item.get("why"):
                        line += f"（为什么重要：{item['why']}）"
                    lines.append(line)
                    if item.get("example"):
                        lines.append(f"  - 例子：{item['example']}")
            lines.append("")
        if analysis.get("problem"):
            lines.extend(["**先讲人话**", "", analysis["problem"], ""])
        if analysis.get("summary"):
            lines.extend(["**这篇论文做了什么**", "", analysis["summary"], ""])
        if analysis.get("analogy"):
            lines.extend(["**一个生活化的类比**", "", analysis["analogy"], ""])
        steps = analysis.get("how_it_works") or []
        if steps:
            lines.extend(["**它是怎么工作的**", ""])
            lines.extend(f"{i}. {step}" for i, step in enumerate(steps, 1))
            lines.append("")
        if analysis.get("contrast"):
            lines.extend(["**和原来的做法比，改了什么**", "", analysis["contrast"], ""])
        misconceptions = analysis.get("misconceptions") or []
        if misconceptions:
            lines.extend(["**容易理解错的地方**", ""])
            for item in misconceptions:
                if isinstance(item, dict):
                    lines.append(
                        f"- 可能误解：{item.get('misunderstanding', '')}  "
                        f"更准确：{item.get('clarify', '')}"
                    )
            lines.append("")
        if analysis.get("example"):
            lines.extend(["**举个例子**", "", analysis["example"], ""])
        points = analysis.get("key_points") or []
        if points:
            lines.extend(["**关键要点**", ""])
            lines.extend(f"- {point}" for point in points)
            lines.append("")
        if analysis.get("takeaway"):
            lines.extend([f"**读完记住这一点**：{analysis['takeaway']}", ""])
        if analysis.get("why_it_matters"):
            lines.extend([f"**为什么值得读**：{analysis['why_it_matters']}", ""])
        if analysis.get("limitations"):
            lines.extend([f"**局限**：{analysis['limitations']}", ""])
        if analysis.get("method") or analysis.get("findings"):
            lines.extend(["**进阶细节**", ""])
            if analysis.get("method"):
                lines.extend([f"- 方法：{analysis['method']}"])
            if analysis.get("findings"):
                lines.extend([f"- 发现：{analysis['findings']}"])
            lines.append("")
        tags = analysis.get("tags") or []
        if tags:
            lines.extend([f"**标签**：{' · '.join(str(tag) for tag in tags)}", ""])
        links = [
            f"[HF 论文页]({paper.get('hf_url')})" if paper.get("hf_url") else "",
            f"[arXiv]({paper.get('arxiv_url')})" if paper.get("arxiv_url") else "",
            f"[PDF]({paper.get('pdf_url')})" if paper.get("pdf_url") else "",
            f"[GitHub]({paper.get('github_url')})" if paper.get("github_url") else "",
            f"[项目页]({paper.get('project_url')})" if paper.get("project_url") else "",
        ]
        lines.append("**链接**：" + " · ".join(link for link in links if link))
        lines.append("")
        authors = paper.get("authors") or []
        if authors:
            shown = ", ".join(authors[:10]) + (" 等" if len(authors) > 10 else "")
            lines.extend([f"**作者**：{shown}", ""])
        if paper.get("organization"):
            lines.extend([f"**机构**：{paper['organization']}", ""])
        if paper.get("note"):
            lines.extend([f"**我的笔记**：{paper['note']}", ""])
        if paper.get("abstract"):
            lines.extend(["<details>", "<summary>原始摘要</summary>", ""])
            lines.extend([paper["abstract"], "", "</details>"])
            lines.append("")
        if analysis.get("source") != "llm":
            lines.extend(["> 注：本篇为规则摘要，未经过私有模型深度分析。", ""])
        lines.extend(["---", ""])

    lines.extend(
        [
            f"*由 Paper Daily 生成于 {settings.now().strftime('%Y-%m-%d %H:%M')}，"
            "内容仅供阅读参考，请以论文原文为准。*",
            "",
        ]
    )
    return "\n".join(lines)


def build_json(
    day: str, papers: list[dict[str, Any]], digest: dict[str, Any] | None
) -> str:
    payload = {
        "date": day,
        "paper_count": len(papers),
        "digest": digest,
        "papers": papers,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
