"""Dispatch a validated action to its effect and return the observation dict.

Handles util / read_file / write_file / llm here. Control-flow kinds (spawn, subruns,
kill, wait, finish) live in loop.py — they change the run's state machine — and the
user-facing kinds (ask_user, write_util) in interact.py. Every observation dict feeds
both the transcript event and (via composer.format_observation) the next user message.
"""

from __future__ import annotations

import json

from .. import utils_lib
from ..endpoints.base import EndpointError
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
    if not utils_lib.exists(home, name):
        return {"kind": "util", "name": name, "missing": True,
                "available": [u["name"] for u in utils_lib.list_utils(home)]}
    code, out, err = utils_lib.run_util(
        home, name, args, timeout=int(action.get("timeout_s") or UTIL_DEFAULT_TIMEOUT_S))
    stdout, trunc_out = truncate(out)
    stderr, trunc_err = truncate(err, cap=2000)
    obs = {"kind": "util", "name": name, "args": args, "exit": code,
           "stdout": stdout, "stderr": stderr, "truncated": trunc_out or trunc_err}
    if code != 0:
        # A failed call teaches the correct one: the util's own usage line plus the exact
        # action shape (weak models often omit `args` or pass it as one string).
        entry = next((u for u in utils_lib.list_utils(home) if u["name"] == name), None)
        if entry and entry.get("usage"):
            obs["usage"] = entry["usage"]
        obs["hint"] = (f'pass every argument in `args` as a JSON array of strings, e.g. '
                       f'{{"say": "…", "kind": "util", "name": "{name}", '
                       f'"args": ["<argument>", "--json"]}}')
    return obs


def do_read_file(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.routine.fs_read_roots)
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


def do_write_file(action: dict, ctx: RunContext) -> dict:
    try:
        roots = ctx.routine.fs_write_roots
        path = resolve_rel(ctx.routine.dir, action["path"], roots)
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
    "llm": do_llm,
}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
