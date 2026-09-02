"""Rendering a live run to a TERMINAL — the `run-once` event stream.

Split out of `cli.py` (F393): dispatching commands and painting a transcript are different jobs,
and this one is pure presentation. It is the only place the engine's event vocabulary is turned
into something a person reads at a prompt rather than in the console, so it has to stay in step
with `engine/transcript.py`'s types — an event it does not know is shown plainly rather than
dropped.
"""

from __future__ import annotations

from .engine import child
from .engine.actionschema import BRIEF_FIELD


def _server_tz() -> str:
    from .schedule import server_tz
    return server_tz()

def _render_event(obj: dict) -> str | None:  # noqa: PLR0911 — one return per event type
    t = obj.get("type")
    p = obj.get("payload", {})
    if t == "header":
        o = obj.get("orchestrator", {})
        return f"── run {obj.get('run_id')} · {o.get('endpoint')}:{o.get('model')} ──"
    if t == "assistant_action":
        say = p.get("say", "")
        brief = {"util": f"{p.get('name')} {' '.join(p.get('args') or [])}".strip(),
                 "write_util": p.get("name"),
                 "read_file": p.get("path") or ", ".join(p.get("paths") or []),
                 "write_file": p.get("path"), "edit_file": p.get("path"),
                 "memory_read": p.get("name"),
                 "memory_write": f"{p.get('name')}{' (delete)' if p.get('delete') else ''}",
                 "llm": (p.get("prompt") or "")[:60],
                 "spawn": f"{p.get('label') or ''} [{p.get('workflow') or 'general-task'}]",
                 "subtask": f"{p.get('label') or ''} [{p.get('workflow') or 'general-task'}]",
                 "kill": f"#{p.get('n')}", "wait": "all" if p.get("all") else
                 (f"#{p.get('n')}" if p.get("n") else "any"),
                 "ask_user": (p.get("question") or "")[:60],
                 "finish": f"{p.get('status')}" }.get(
                     p.get("kind"),
                     # any kind without a rich renderer falls back to its BRIEF_FIELD —
                     # a new action kind can never render blank here again
                     str(p.get(BRIEF_FIELD.get(str(p.get("kind")), ""), "") or ""))
        return f"[{obj.get('turn')}] {say}\n    → {p.get('kind')}: {brief}"
    if t == "observation":
        kind = p.get("kind")
        if kind == "util":
            return f"    ← util {p.get('name')}: " + ("missing" if p.get("missing")
                                                      else f"exit {p.get('exit')}")
        if kind == "write_util":
            state = ("pending approval" if p.get("pending_approval") else "declined"
                     if p.get("declined") else "selftest ok" if p.get("selftest_ok")
                     else "selftest failed")
            return f"    ← write_util {p.get('name')}: {state}"
        if kind == "llm":
            return "    ← llm reply" + (" (error)" if p.get("error") else "")
        if kind == "spawn":
            return (f"    ← spawn REJECTED: {p.get('reason')}" if p.get("rejected")
                    else f"    ← sub-workflow #{p.get('n')} started")
        if kind == "subtask":
            if p.get("rejected"):
                return f"    ← subtask REJECTED: {p.get('reason')}"
            return f"    ← subtask #{p.get('n')} started (sequential, background)"
        if kind == "wait":
            done = ", ".join(f"#{f['n']}:{f['status']}" for f in p.get("finished", []))
            return f"    ← wait → {done or ('timeout' if p.get('timed_out') else 'nothing new')}"
        return f"    ← {kind}"
    if t == "question":
        return f"    ? [{p.get('mode')}] {p.get('question')}"
    if t == "answer":
        return f"    ! answered: {p.get('text', '')[:80]}"
    if t == "user_injection":
        return f"    + injected: {p.get('text', '')[:80]}"
    if t == "error":
        return f"    ✗ error ({p.get('where')}): {p.get('message', '')[:120]}"
    if t == "compaction":
        return f"    ⇣ compacted context ({p.get('before_chars')} → {p.get('after_chars')} chars)"
    if t in ("subrun_start", "subrun_end"):
        label = child.mode_short(str(p.get("mode") or ""))
        if t == "subrun_start":
            return f'    ↳ {label} #{p.get('n')} "{p.get('label')}" started ({p.get('workflow')})'
        return f"    ↰ {label} #{p.get('n')} {p.get('status')} — {p.get('turns')} turns"
    if t == "finish":
        return f"── finish: {p.get('status')} ──\n{p.get('summary', '')}"
    return None
