"""
Skills — Load .md skill files into system prompt sections.

A skill is a markdown file that contains prompt instructions for Claude.
Skills are loaded at agent creation time and injected into the system prompt.

Design: Giovanni's architecture (2026-03-24 session).
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_skills(skill_paths: list[str], base_dir: str | None = None) -> str:
    """
    Load skill .md files and concatenate into a system prompt section.

    Args:
        skill_paths: List of paths to .md skill files (relative or absolute)
        base_dir: Base directory for relative paths (defaults to cwd)

    Returns:
        Combined skill content as a single string, with section headers.
    """
    base = Path(base_dir) if base_dir else Path.cwd()
    sections = []

    for path_str in skill_paths:
        path = Path(path_str)
        if not path.is_absolute():
            path = base / path

        if not path.exists():
            logger.warning(f"Skill file not found: {path}")
            continue

        content = path.read_text(encoding="utf-8").strip()

        # Strip YAML frontmatter if present
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()

        skill_name = path.stem.replace("-", " ").replace("_", " ").title()
        sections.append(f"## Skill: {skill_name}\n\n{content}")
        logger.info(f"Loaded skill: {path.name} ({len(content)} chars)")

    if not sections:
        logger.warning("No skills loaded!")
        return ""

    return "\n\n---\n\n".join(sections)
