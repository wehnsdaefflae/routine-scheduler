"""Python-workflow parsing + rendering.

A library workflow is a single self-contained `.py` file that DEPICTS a routine's control flow —
it is never executed. It carries a `META = {...}` dict literal, a `PHASES` literal, a top-level
`main()` entry function whose body is the per-run control flow, and dummy imports that name the
routine's parameters and the action kinds it uses. We read all of it statically with `ast`
(`literal_eval` on the literals — no import, no code runs), and render the pattern into the
markdown the routine's orchestrator actually reads (materialize / decompose fallback).

There is deliberately no COMPLETION literal: what DONE means is the USER's, and it lives in the
routine's `state/stopping.json` where they can edit it. A second completion text frozen into
main.md could only ever disagree with it.
"""

from __future__ import annotations

import ast

REQUIRED_META = ("name", "slug", "description", "when_to_use", "version")

#: The dummy module a pattern imports its action kinds from. Named here because both the
#: renderer and the linter reason about that import line.
ACTIONS_MODULE = "routine.actions"


def parse_py(source: str) -> dict:
    """Statically parse a Python-workflow file (no execution). Returns a meta dict: the META keys
    plus `phases` (from PHASES), `action_imports` (the names on the `from routine.actions import`
    line) and `has_main`. Raises SyntaxError on invalid Python, ValueError if META is missing /
    not a literal.
    """
    tree = ast.parse(source)                      # SyntaxError on malformed Python
    meta: dict | None = None
    phases = None
    funcs: list[str] = []
    action_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module == ACTIONS_MODULE:
            action_imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                name = target.id if isinstance(target, ast.Name) else None
                if name == "META":
                    meta = ast.literal_eval(node.value)      # ValueError if not a pure literal
                elif name == "PHASES":
                    phases = ast.literal_eval(node.value)
    if not isinstance(meta, dict):
        # ValueError on purpose (not TypeError): callers (lint, generate) catch ValueError
        # as "not a valid pattern file" — changing the type would break that contract.
        raise ValueError("no `META = {...}` dict literal found")  # noqa: TRY004
    out = dict(meta)
    out["phases"] = phases
    out["action_imports"] = action_imports
    out["has_main"] = "main" in funcs
    return out


def _step_order(tree: ast.Module) -> list[str]:
    """The step functions `main()` itself sequences, in call-site order.

    The rendered list is introduced as "the steps … in the order + control flow of `main()`",
    so it must be exactly that. Promoting EVERY module-level function instead put pure Python
    plumbing in front of the orchestrator as work to act out — a materialized routine was told
    to perform `file_exists — Helper to check if a state file exists`. Only what `main()` calls
    directly is a step; everything a step calls in turn is detail, and the full source is
    rendered below the list anyway.
    """
    defined = {node.name for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    main = next((node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == "main"), None)
    if main is None:
        return []
    # By SOURCE POSITION, not ast.walk order: walk is breadth-first, which reorders a branch's
    # calls ahead of ones written before it (`bootstrap` landed after `record`).
    calls = sorted((node.lineno, node.col_offset, node.func.id) for node in ast.walk(main)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in defined and node.func.id != "main")
    order: list[str] = []
    for *_, name in calls:
        if name not in order:
            order.append(name)
    return order


def _docstrings(tree: ast.Module) -> dict[str, str]:
    """Function name → its first docstring line, for every documented top-level function."""
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = (ast.get_docstring(node) or "").strip().split("\n")[0].strip()
            if first:
                out[node.name] = first
    return out


def render_markdown(source: str, meta: dict) -> str:
    """Deterministic Python-pattern → routine main.md BODY (no LLM). The orchestrator reads the
    pattern and acts it out. Produces the `## Run flow` / `## Phases` sections a materialized
    routine must have. Used by `materialize` and `decompose`'s fallback.
    """
    phases = meta.get("phases") or []
    phase_lines = ("\n".join(f"- {p}" for p in phases) if phases
                   else "- steady — no cross-run milestones")
    # Lead with the steps as prose (each step function's docstring first line), so a weak model can
    # follow them without parsing the raw source dump below — which was what tripped up direct runs.
    step_lines = []
    try:
        tree = ast.parse(source)
        docs = _docstrings(tree)
        names = _step_order(tree)
        # A pattern with no main(), or one that inlines everything, still gets a list: falling
        # back to every documented function beats rendering none of them.
        step_lines = [f"- **{name}** — {docs[name]}"
                      for name in (names or [n for n in docs if n != "main"])
                      if name in docs]
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
        f"{phase_lines}\n"
    )
