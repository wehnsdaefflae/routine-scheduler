"""Python-workflow parsing + rendering.

A library workflow is a single self-contained `.py` file that DEPICTS a routine's control flow —
it is never executed. It carries a `META = {...}` dict literal, `PHASES`/`COMPLETION` literals,
a top-level `run()` entry function whose body is the per-run control flow, and dummy imports that
name the routine's parameters. We read all of it statically with `ast` (`literal_eval` on the
literals — no import, no code runs), and render the pattern into the markdown the routine's
orchestrator actually reads (materialize / decompose fallback).
"""

from __future__ import annotations

import ast

REQUIRED_META = ("name", "slug", "description", "when_to_use", "version", "status")


def parse_py(source: str) -> dict:
    """Statically parse a Python-workflow file (no execution). Returns a meta dict: the META keys
    plus `phases` (from PHASES), `completion` (from COMPLETION), `funcs` (top-level def names) and
    `has_run`. Raises SyntaxError on invalid Python, ValueError if META is missing / not a literal."""
    tree = ast.parse(source)                      # SyntaxError on malformed Python
    meta: dict | None = None
    phases = None
    completion = None
    funcs: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                name = target.id if isinstance(target, ast.Name) else None
                if name == "META":
                    meta = ast.literal_eval(node.value)      # ValueError if not a pure literal
                elif name == "PHASES":
                    phases = ast.literal_eval(node.value)
                elif name == "COMPLETION":
                    completion = ast.literal_eval(node.value)
    if not isinstance(meta, dict):
        raise ValueError("no `META = {...}` dict literal found")
    out = dict(meta)
    out["phases"] = phases
    out["completion"] = completion
    out["funcs"] = funcs
    out["has_run"] = "run" in funcs
    out["format"] = "py"
    return out


def render_markdown(source: str, meta: dict) -> str:
    """Deterministic Python-pattern → routine main.md BODY (no LLM). The orchestrator reads the
    pattern and acts it out. Produces the `## Run flow` / `## Phases` / `## Completion criteria`
    sections a materialized routine must have. Used by `materialize` and `decompose`'s fallback."""
    phases = meta.get("phases") or []
    phase_lines = ("\n".join(f"- {p}" for p in phases) if phases
                   else "- steady — no cross-run milestones")
    completion = str(meta.get("completion") or "").strip() or "(see the pattern's finish conditions)"
    fence = "```"
    return (
        "## Run flow\n"
        "Follow the control-flow PATTERN below. It is written as Python for precision — you do NOT\n"
        "execute it; you act it out, one engine action per turn, following its branches, loops and\n"
        "error handling. The dummy imports name this routine's parameters; each function's docstring\n"
        "is that step's detail.\n\n"
        f"{fence}python\n{source.strip()}\n{fence}\n\n"
        "## Phases\n"
        "Track the current phase in `state/phase.json` as `{\"phase\": \"...\", \"note\": \"...\"}`.\n"
        f"{phase_lines}\n\n"
        "## Completion criteria\n"
        f"{completion}\n"
    )
