"""
skills/__init__.py — Skill Registry
=====================================
Exports all available skills for clean imports in main.py and router.py.
Adding a new skill: create skills/my_skill.py, add it here, then register
in router.py (VALID_SKILLS + pre-check) and main.py (SKILL_MAP).
"""

from skills import (
    os_controller,
    conversational,
    web_search,
    creator,
    fs_manager,
    backup_manager,
    inspector,
    dev_architect,
    github_search,
    structured_logger,
    notion_logger,
    conversation_memory,
    reasoner,
)

__all__ = [
    "os_controller",
    "conversational",
    "web_search",
    "creator",
    "fs_manager",
    "backup_manager",
    "inspector",
    "dev_architect",
    "github_search",
    "structured_logger",
    "notion_logger",
    "conversation_memory",
    "reasoner",
]
