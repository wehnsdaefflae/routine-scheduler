"""YAML frontmatter parsing for workflow/fragment markdown files."""

from __future__ import annotations

from pathlib import Path

import yaml


def parse(text: str) -> tuple[dict, str]:
    """Split '---\\n<yaml>\\n---\\n<body>' → (meta, body). No frontmatter → ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 2)
    if len(parts) < 2:
        return {}, text
    try:
        meta = yaml.safe_load(parts[0][3:]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    body = parts[1]
    if len(parts) == 3:
        body = parts[1] + "\n---" + parts[2]  # a later --- belongs to the body
    return meta, body.lstrip("\n")


def load(path: Path) -> tuple[dict, str]:
    return parse(path.read_text(encoding="utf-8"))


def dump(meta: dict, body: str) -> str:
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n\n{body.lstrip()}"
