"""Python-workflow parsing + rendering.

A library workflow is a single self-contained `.py` file that DEPICTS a routine's control flow —
it is never executed. It carries a `META = {...}` dict literal, `PHASES`/`COMPLETION` literals,
a top-level `main()` entry function whose body is the per-run control flow, and dummy imports that
name the routine's parameters. We read all of it statically with `ast` (`literal_eval` on the
literals — no import, no code runs), and render the pattern into the markdown the routine's
orchestrator actually reads (materialize / decompose fallback).
"""

from __future__ import annotations

import ast

REQUIRED_META = ("name", "slug", "description", "when_to_use", "version")


def parse_py(source: str) -> dict:
    """Statically parse a Python-workflow file (no execution). Returns a meta dict: the META keys
    plus `phases` (from PHASES), `completion` (from COMPLETION), `funcs` (top-level def names) and
    `has_main`. Raises SyntaxError on invalid Python, ValueError if META is missing / not a literal.
    """
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
        # ValueError on purpose (not TypeError): callers (lint, generate) catch ValueError
        # as "not a valid pattern file" — changing the type would break that contract.
        raise ValueError("no `META = {...}` dict literal found")  # noqa: TRY004
    out = dict(meta)
    out["phases"] = phases
    out["completion"] = completion
    out["funcs"] = funcs
    out["has_main"] = "main" in funcs
    out["format"] = "py"
    return out


def render_markdown(source: str, meta: dict) -> str:
    """Deterministic Python-pattern → routine main.md BODY (no LLM). The orchestrator reads the
    pattern and acts it out. Produces the `## Run flow` / `## Phases` / `## Completion criteria`
    sections a materialized routine must have. Used by `materialize` and `decompose`'s fallback.
    """
    phases = meta.get("phases") or []
    phase_lines = ("\n".join(f"- {p}" for p in phases) if phases
                   else "- steady — no cross-run milestones")
    completion = (str(meta.get("completion") or "").strip()
                  or "(see the pattern's finish conditions)")
    # Lead with the steps as prose (each step function's docstring first line), so a weak model can
    # follow them without parsing the raw source dump below — which was what tripped up direct runs.
    step_lines = []
    try:
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "main":
                first = (ast.get_docstring(node) or "").strip().split("\n")[0].strip()
                if first:
                    step_lines.append(f"- **{node.name}** — {first}")
    except SyntaxError:
        pass
    steps_md = ("\nThe steps (act each out as engine actions, in the order + control flow "
                "of `main()`):\n"
                + "\n".join(step_lines) + "\n") if step_lines else ""
    fence = "```"
    return (
        "## Run flow\n"
        "Follow the control-flow PATTERN below. It is written as Python for precision — "
        "you do NOT\n"
        "execute it; you ACT IT OUT one engine action per turn, following its branches, loops and\n"
        "error handling. A function call like `write_file(path, content)` means emit a "
        "`write_file`\n"
        "ACTION with those fields (per the ACTION SCHEMA) — never put a call's arguments "
        "at the top\n"
        "level of the action. The dummy imports name this routine's parameters; each function's\n"
        "docstring is that step's detail.\n"
        f"{steps_md}\n"
        f"{fence}python\n{source.strip()}\n{fence}\n\n"
        "## Phases\n"
        'Track the current phase in `state/phase.json` as `{"phase": "...", "note": "..."}`.\n'
        f"{phase_lines}\n\n"
        "## Completion criteria\n"
        f"{completion}\n"
    )
