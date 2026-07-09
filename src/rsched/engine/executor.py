"""Dispatch a validated action to its effect and return the observation dict.

Handles shell / read_file / write_file / llm here. Control-flow kinds (ask_user,
subinstruction, finish) live in loop.py — they change the run's state machine.
Every observation dict feeds both the transcript event and (via composer.format_observation)
the next user message.
"""

from __future__ import annotations

from ..endpoints.base import EndpointError
from ..paths import resolve_rel
from . import shell_guard
from .composer import truncate
from .run_context import RunContext

READ_DEFAULT_MAX_LINES = 200


def do_shell(action: dict, ctx: RunContext) -> dict:
    command = action["command"]
    problems = shell_guard.vet(command, ctx.routine.shell_allowlist)
    if problems:
        return {"kind": "shell", "rejected": True, "problems": problems, "command": command}
    result = shell_guard.run_shell(
        command, cwd=ctx.routine.dir, timeout_s=int(action.get("timeout_s") or shell_guard.DEFAULT_TIMEOUT_S)
    )
    stdout, trunc_out = truncate(result.stdout)
    stderr, trunc_err = truncate(result.stderr, cap=2000)
    return {"kind": "shell", "command": command, "exit": result.exit,
            "stdout": stdout, "stderr": stderr, "duration_s": round(result.duration_s, 2),
            "timed_out": result.timed_out, "truncated": trunc_out or trunc_err}


def do_read_file(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.routine.fs_read_roots)
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError, UnicodeDecodeError) as exc:
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
    role = action.get("role") or "subcall"
    try:
        endpoint, ref = ctx.registry.for_role(role, ctx.routine.roles)
        messages = []
        if action.get("system"):
            messages.append({"role": "system", "content": action["system"]})
        messages.append({"role": "user", "content": action["prompt"]})
        completion = endpoint.complete(
            messages, model=ref.model, schema=action.get("response_schema"),
            effort=ref.effort, max_tokens=16_384,
        )
    except EndpointError as exc:
        return {"kind": "llm", "role": role, "error": str(exc)}
    ctx.add_usage(completion.usage)
    reply = completion.text
    if completion.parsed is not None:
        import json

        reply = json.dumps(completion.parsed, ensure_ascii=False, indent=1)
    reply, truncated = truncate(reply)
    return {"kind": "llm", "role": role, "endpoint": ref.endpoint, "model": ref.model,
            "reply": reply, "usage": completion.usage, "truncated": truncated}


DISPATCH = {
    "shell": do_shell,
    "read_file": do_read_file,
    "write_file": do_write_file,
    "llm": do_llm,
}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
