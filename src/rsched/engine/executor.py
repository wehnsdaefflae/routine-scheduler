"""Dispatch a validated action to its effect and return the observation dict.

Handles util / read_file / write_file / memory_read / memory_write / llm here.
Control-flow kinds (spawn, subruns, kill, wait, finish) live in loop.py — they change the
run's state machine — and the user-facing kinds (ask_user, write_util) in interact.py.
Every observation dict feeds both the transcript event and (via
composer.format_observation) the next user message.
"""

from __future__ import annotations

import json

from .. import utils_lib
from ..endpoints.base import EndpointError
from ..ids import is_slug
from ..paths import resolve_rel
from .composer import truncate
from .run_context import RunContext

READ_DEFAULT_MAX_LINES = 200
UTIL_DEFAULT_TIMEOUT_S = 300


def do_util(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    args = [str(a) for a in (action.get("args") or [])]
    home = ctx.server.utils_home
    if name == "list":  # discovery: `gu list` — the catalog is derived live, never in-prompt
        return {"kind": "util", "name": "list", "listing": utils_lib.catalog_text(home)}
    if name == "show":  # read a util's SOURCE — write_util's counterpart (repair needs read)
        target = str(args[0]) if args else ""
        source = utils_lib.read_util(home, target) if target and is_slug(target) else None
        if source is None:
            return {"kind": "util", "name": "show", "target": target, "missing": True,
                    "available": [u["name"] for u in utils_lib.list_utils(home)]}
        content, truncated = truncate(source, cap=24_000)
        return {"kind": "util", "name": "show", "target": target, "source": content,
                "truncated": truncated}
    if not utils_lib.exists(home, name):
        return {"kind": "util", "name": name, "missing": True,
                "available": [u["name"] for u in utils_lib.list_utils(home)]}
    code, out, err = utils_lib.run_util(
        home, name, args, timeout=int(action.get("timeout_s") or UTIL_DEFAULT_TIMEOUT_S))
    stdout, trunc_out = truncate(out)
    # On failure, stderr is the repair material — keep the whole trace where possible
    # (truncate preserves head+tail, so the exception at the traceback's end survives).
    stderr, trunc_err = truncate(err, cap=8000 if code != 0 else 2000)
    obs = {"kind": "util", "name": name, "args": args, "exit": code,
           "stdout": stdout, "stderr": stderr, "truncated": trunc_out or trunc_err}
    if code != 0:
        # A failed call teaches the correct one — and the repair path. Without this nudge
        # the model's rational move is a silent workaround, and the next routine hits the
        # same wall (seen live: page-fetch broken, run fell back to websearch, nobody told).
        entry = next((u for u in utils_lib.list_utils(home) if u["name"] == name), None)
        if entry and entry.get("usage"):
            obs["usage"] = entry["usage"]
        # The repair route depends on the routine's grants: with util authoring, fix it in
        # place; without, escalate — never let it silently work around a broken util.
        if ctx.grants is None or ctx.grants.allows_kind("write_util"):
            repair = (f'If the inputs were right, the util itself may be broken — read it with '
                      f'{{"kind": "util", "name": "show", "args": ["{name}"]}}, fix it, and '
                      f'write_util the corrected script (selftest-gated; the fix benefits every '
                      f'routine). If the environment lacks something no script can install '
                      f'(system packages, hardware), file a deferred ask_user so the operator '
                      f'sees it.')
        else:
            repair = (f'If the inputs were right, the util itself may be broken — read it with '
                      f'{{"kind": "util", "name": "show", "args": ["{name}"]}} to confirm, then '
                      f'file a deferred ask_user naming the util, the failing call, and the '
                      f'error (this routine holds no util-authoring permission, so it cannot '
                      f'revise utils itself). Never silently work around a broken util.')
        obs["hint"] = (
            f'call shape: every argument goes in `args` as a JSON array of strings, e.g. '
            f'{{"say": "…", "kind": "util", "name": "{name}", "args": ["<argument>", "--json"]}}. '
            + repair)
    return obs


def _runs_read_gate(ctx: RunContext, resolved) -> str | None:
    """Backstop for previous-run access (grants.deny handles the relative-path form inside
    the schema-retry cycle; this catches absolute paths and scopes `runs: last`). The
    current run's own tree — status, archived history — is always readable."""
    g = ctx.grants
    if g is None:
        return None
    runs_dir = ctx.routine.dir / "runs"
    try:
        rel = resolved.relative_to(runs_dir)
    except ValueError:
        return None
    if resolved.is_relative_to(ctx.root_run_dir):
        return None
    if g.run_history == "none":
        return ("reading previous runs is not among this routine's permissions "
                "(run-history / run-history-full unlock it)")
    if g.run_history == "last":
        prior = sorted(d.name for d in runs_dir.iterdir()
                       if d.is_dir() and d.name != ctx.root_run_dir.name)
        last = prior[-1] if prior else None
        if not rel.parts or rel.parts[0] != last:
            return (f"this routine's run-history permission covers only the LAST previous "
                    f"run ({'runs/' + last if last else 'none exists yet'}); "
                    f"run-history-full would cover all of them")
    return None


def do_read_file(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.routine.fs_read_roots)
        if err := _runs_read_gate(ctx, path):
            return {"kind": "read_file", "path": action["path"], "error": err}
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError) as exc:
        return {"kind": "read_file", "path": action["path"], "error": str(exc)}
    lines = text.splitlines()
    start = max(1, int(action.get("start_line") or 1))
    max_lines = min(int(action.get("max_lines") or READ_DEFAULT_MAX_LINES), 500)
    window = lines[start - 1 : start - 1 + max_lines]
    content, truncated = truncate("\n".join(window))
    return {"kind": "read_file", "path": action["path"], "start_line": start,
            "end_line": min(start - 1 + max_lines, len(lines)), "total_lines": len(lines),
            "content": content, "truncated": truncated}


def _write_gate(ctx: RunContext, resolved) -> str | None:
    """Backstop for engine-owned and permission-gated writes (grants.deny handles the
    relative-path form; this catches absolute paths into the routine's own dir)."""
    g = ctx.grants
    if g is None:
        return None
    if resolved.is_relative_to(ctx.routine.dir / "runs"):
        return "runs/ is engine-owned and read-only for the run"
    if not g.self_modify:
        from ..grants import RECIPE_PREFIXES

        try:
            rel = resolved.relative_to(ctx.routine.dir)
        except ValueError:
            return None
        rel_s = str(rel)
        if any(rel_s == p.rstrip("/") or rel_s.startswith(p) for p in RECIPE_PREFIXES):
            return ("modifying the routine's own recipe files needs the self-modification "
                    "permission this routine does not hold")
    return None


def do_write_file(action: dict, ctx: RunContext) -> dict:
    try:
        roots = ctx.routine.fs_write_roots
        path = resolve_rel(ctx.routine.dir, action["path"], roots)
        if err := _write_gate(ctx, path):
            return {"kind": "write_file", "path": action["path"], "error": err}
        path.parent.mkdir(parents=True, exist_ok=True)
        data = action["content"]
        if not isinstance(data, str):
            # Structured content arrives as a live JSON value — models need not escape
            # file bodies into strings; we serialize.
            data = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if action.get("append"):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(data)
        else:
            path.write_text(data, encoding="utf-8")
    except (OSError, PermissionError) as exc:
        return {"kind": "write_file", "path": action["path"], "error": str(exc)}
    return {"kind": "write_file", "path": action["path"], "bytes": len(data.encode("utf-8")),
            "append": bool(action.get("append"))}


def _memory_topics(mem_dir) -> list[str]:
    if not mem_dir.is_dir():
        return []
    return sorted(p.stem for p in mem_dir.glob("*.md") if p.name != "INDEX.md")


def _memory_index_upsert(mem_dir, name: str, about: str | None) -> None:
    """INDEX.md is engine-owned: one `- <name>.md: <about>` line per note, updated in the
    same operation as the note itself so the catalog can never drift. about=None removes."""
    index = mem_dir / "INDEX.md"
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    prefix = f"- {name}.md:"
    lines = [ln for ln in lines if not ln.startswith(prefix)]
    if about is not None:
        lines.append(f"{prefix} {about.strip()}")
    index.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def do_memory_read(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = ctx.routine.dir / ".memory"
    path = mem_dir / f"{name}.md"
    if not path.is_file():
        return {"kind": "memory_read", "name": name, "missing": True,
                "topics": _memory_topics(mem_dir)}
    content, truncated = truncate(path.read_text(encoding="utf-8", errors="replace"))
    return {"kind": "memory_read", "name": name, "content": content,
            "lines": len(content.splitlines()), "truncated": truncated}


def do_memory_write(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = ctx.routine.dir / ".memory"
    path = mem_dir / f"{name}.md"
    if action.get("delete"):
        existed = path.is_file()
        if existed:
            path.unlink()
            _memory_index_upsert(mem_dir, name, None)
        return {"kind": "memory_write", "name": name, "deleted": True, "existed": existed}
    mem_dir.mkdir(exist_ok=True)
    created = not path.exists()
    data = str(action["content"]).rstrip() + "\n"
    path.write_text(data, encoding="utf-8")
    _memory_index_upsert(mem_dir, name, str(action["about"]))
    return {"kind": "memory_write", "name": name, "created": created,
            "lines": len(data.splitlines())}


def do_llm(action: dict, ctx: RunContext) -> dict:
    try:
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        messages = []
        if action.get("system"):
            messages.append({"role": "system", "content": action["system"]})
        messages.append({"role": "user", "content": action["prompt"]})
        completion = endpoint.complete(
            messages, model=ref.model, schema=action.get("response_schema"),
            effort=ref.effort, max_tokens=16_384,
        )
    except EndpointError as exc:
        return {"kind": "llm", "error": str(exc)}
    ctx.add_usage(completion.usage)
    reply = completion.text
    if completion.parsed is not None:
        reply = json.dumps(completion.parsed, ensure_ascii=False, indent=1)
    reply, truncated = truncate(reply)
    return {"kind": "llm", "endpoint": ref.endpoint, "model": ref.model,
            "reply": reply, "usage": completion.usage, "truncated": truncated}


DISPATCH = {
    "util": do_util,
    "read_file": do_read_file,
    "write_file": do_write_file,
    "memory_read": do_memory_read,
    "memory_write": do_memory_write,
    "llm": do_llm,
}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
