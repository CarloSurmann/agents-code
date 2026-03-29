"""
Knowledge Base Search — Simple markdown-based FAQ lookup for support agents.

MVP approach: load all .md files from a directory into memory, search by
keyword matching. No vector DB, no embeddings — just split docs into sections
and match against the query.

Upgrade path: swap keyword matching for embedding search when a client has
50+ FAQ entries. Same interface, different backend.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class KBSection:
    """A section from a knowledge base document."""
    title: str
    content: str
    source_file: str
    keywords: list[str]


# ─── Module-level state ───────────────────────────────────────────────

_sections: list[KBSection] = []
_kb_dir: str = ""


def init_kb(kb_dir: str):
    """Load all .md files from the knowledge base directory."""
    global _sections, _kb_dir
    _kb_dir = kb_dir
    _sections = []

    kb_path = Path(kb_dir)
    if not kb_path.exists():
        logger.warning(f"KB directory not found: {kb_dir}")
        return

    for md_file in sorted(kb_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            sections = _parse_markdown_sections(content, md_file.name)
            _sections.extend(sections)
            logger.info(f"Loaded {len(sections)} sections from {md_file.name}")
        except Exception as e:
            logger.error(f"Failed to load {md_file.name}: {e}")

    logger.info(f"KB initialized: {len(_sections)} total sections from {kb_dir}")


def _parse_markdown_sections(content: str, filename: str) -> list[KBSection]:
    """Split markdown into sections by headings."""
    sections = []
    current_title = filename.replace(".md", "").replace("-", " ").title()
    current_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("## ") or line.startswith("### "):
            # Save previous section
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    sections.append(KBSection(
                        title=current_title,
                        content=text,
                        source_file=filename,
                        keywords=_extract_keywords(current_title + " " + text),
                    ))
            current_title = line.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Last section
    if current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append(KBSection(
                title=current_title,
                content=text,
                source_file=filename,
                keywords=_extract_keywords(current_title + " " + text),
            ))

    return sections


def _extract_keywords(text: str) -> list[str]:
    """Extract lowercase keywords from text (simple word tokenization)."""
    # Remove markdown formatting
    clean = re.sub(r"[#*_`\[\]()>]", " ", text.lower())
    # Split and filter
    words = set(clean.split())
    # Remove very short and very common words
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "to", "of",
                  "and", "or", "in", "on", "at", "for", "with", "by", "from", "as",
                  "it", "that", "this", "can", "will", "do", "if", "you", "your",
                  "we", "our", "i", "my", "not", "no", "yes", "so", "but", "has",
                  "have", "had", "been"}
    return [w for w in words if len(w) > 2 and w not in stop_words]


def _score_match(query_keywords: list[str], section: KBSection) -> int:
    """Score how well a section matches the query. Higher = better."""
    score = 0
    section_keywords = set(section.keywords)
    section_text = section.content.lower()

    for kw in query_keywords:
        if kw in section_keywords:
            score += 2
        if kw in section_text:
            score += 1
        if kw in section.title.lower():
            score += 3  # Title matches are strongest

    return score


# ─── Tool function (exposed to Agent) ─────────────────────────────────


def search_kb(query: str) -> str:
    """Search the knowledge base for answers matching the query. Returns the top matching FAQ sections with their source files. If no match is found, returns an empty result — the agent should flag this as a KB gap."""
    if not _sections:
        return json.dumps({
            "matched": False,
            "results": [],
            "message": "Knowledge base is empty or not initialized.",
        })

    query_keywords = _extract_keywords(query)
    if not query_keywords:
        return json.dumps({
            "matched": False,
            "results": [],
            "message": "Could not extract keywords from query.",
        })

    # Score all sections
    scored = [(section, _score_match(query_keywords, section)) for section in _sections]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Take top 3 with score > 0
    top = [(s, score) for s, score in scored[:3] if score > 0]

    if not top:
        return json.dumps({
            "matched": False,
            "results": [],
            "query": query,
            "message": f"No FAQ match found for: '{query}'. This should be flagged as a KB gap.",
        })

    results = []
    for section, score in top:
        results.append({
            "title": section.title,
            "content": section.content[:500],  # Truncate long sections
            "source": section.source_file,
            "relevance_score": score,
        })

    return json.dumps({
        "matched": True,
        "results": results,
        "query": query,
    })
