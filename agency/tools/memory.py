"""Memory tool — persistent knowledge store per customer.

Each customer has a memory.md file that the agent can read from (loaded into
system prompt at startup) and write to (via the save_to_memory tool).

The memory file is a simple markdown document organized by sections:
- ## Contacts — email addresses, names, roles learned from conversations
- ## Preferences — how the customer likes things done
- ## Notes — anything else worth remembering

Usage:
    from agency.tools.memory import create_memory_tools

    tools = create_memory_tools("customers/pizzeria-mario/memory.md")
    # Returns: [save_to_memory, read_memory]
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def create_memory_tools(memory_path: str) -> list:
    """Create memory tools bound to a specific file path.

    Returns a list of tool functions that read/write to the given path.
    The functions have proper docstrings so the agent knows how to use them.
    """

    def save_to_memory(fact: str, section: str = "Notes") -> str:
        """Save a new fact to long-term memory.

        Use this when you learn something important during a conversation that
        should be remembered for future sessions. Examples:
        - A contact's email address or name
        - A customer preference ("never chase invoices under €500")
        - A special relationship ("Client X always pays late but always pays")
        - A phone number or alternative contact method

        Args:
            fact: The fact to remember. Be concise and specific.
            section: Which section to file it under.
                     Use "Contacts" for people/emails,
                     "Preferences" for how-to-do-things,
                     "Notes" for everything else.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d")

        # Create file with header if it doesn't exist
        if not os.path.exists(memory_path):
            with open(memory_path, "w", encoding="utf-8") as f:
                f.write("# Agent Memory\n\n")
                f.write("## Contacts\n\n")
                f.write("## Preferences\n\n")
                f.write("## Notes\n\n")

        # Read current content
        with open(memory_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find the section and append
        section_header = f"## {section}"
        if section_header in content:
            # Insert after the section header
            parts = content.split(section_header, 1)
            # Find the end of the section (next ## or end of file)
            rest = parts[1]
            # Add the fact right after the header
            lines = rest.split("\n", 1)
            new_entry = f"\n- {fact} _{timestamp}_"
            if len(lines) > 1:
                updated_rest = lines[0] + new_entry + "\n" + lines[1]
            else:
                updated_rest = lines[0] + new_entry + "\n"
            content = parts[0] + section_header + updated_rest
        else:
            # Section doesn't exist — create it
            content += f"\n{section_header}\n- {fact} _{timestamp}_\n"

        # Write back
        with open(memory_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"[Memory] Saved to {section}: {fact}")
        return f"Saved to memory ({section}): {fact}"

    def read_memory() -> str:
        """Read all facts from long-term memory.

        Returns the full memory file contents. Use this if you need to check
        what you already know before asking the user a question.
        """
        if not os.path.exists(memory_path):
            return "Memory is empty — no facts stored yet."

        with open(memory_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        return content or "Memory is empty — no facts stored yet."

    return [save_to_memory, read_memory]
