"""Skill loader — reads .md files and injects them into the agent's system prompt.

A skill is a markdown file containing knowledge and instructions for the agent.
Skills live in agency/skills/ (shared) or agents/<name>/skills/ (workflow-specific).

Example skill file (agency/skills/draft-chase-email.md):

    # Skill: Draft Chase Email

    You can write AR chase emails. Follow these rules:
    - Stage 1 (1-6 days overdue): friendly, assume oversight
    - Stage 2 (7-13 days): firm but warm, offer help
    ...

The skill content gets appended to the system prompt so the agent
"knows" how to do the thing without any code changes.
"""

from __future__ import annotations

import os
from pathlib import Path

# Default directory for shared skills
_DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills"


def load_skills(
    skill_names: list[str],
    skills_dir: str | None = None,
    extra_dirs: list[str] | None = None,
) -> str:
    """Load skill files by name and return their combined content.

    Searches for each skill as a .md file in:
    1. skills_dir (if provided)
    2. extra_dirs (if provided) — for workflow-specific skills
    3. The default shared skills directory (agency/skills/)

    Args:
        skill_names: List of skill names (without .md extension).
        skills_dir: Primary directory to search.
        extra_dirs: Additional directories to search.

    Returns:
        Combined content of all found skill files, separated by headers.
    """
    if not skill_names:
        return ""

    search_dirs = []
    if skills_dir:
        search_dirs.append(Path(skills_dir))
    for d in extra_dirs or []:
        search_dirs.append(Path(d))
    search_dirs.append(_DEFAULT_SKILLS_DIR)

    loaded = []

    for name in skill_names:
        filename = f"{name}.md"
        content = None

        for directory in search_dirs:
            filepath = directory / filename
            if filepath.is_file():
                content = filepath.read_text(encoding="utf-8").strip()
                break

        if content:
            loaded.append(f"<!-- skill: {name} -->\n{content}")
        else:
            # List available skills for debugging
            available = []
            for d in search_dirs:
                if d.is_dir():
                    available.extend(f.stem for f in d.glob("*.md"))
            raise FileNotFoundError(
                f"Skill '{name}' not found in {[str(d) for d in search_dirs]}. "
                f"Available: {available}"
            )

    return "\n\n---\n\n".join(loaded)
