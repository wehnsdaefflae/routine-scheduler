"""Rendering a live run to a TERMINAL — the `run-once` event stream.

Split out of `cli.py` (F393): dispatching commands and painting a transcript are different jobs,
and this one is pure presentation. It is the only place the engine's event vocabulary is turned
into something a person reads at a prompt rather than in the console, so it has to stay in step
with `engine/transcript.py`'s types (tests/test_cli.py renders every one of them) — an event it
does not know is shown plainly rather than dropped.
"""

from __future__ import annotations

from .engine import child
from .engine.actionschema import BRIEF_FIELD


def _render_event(obj: dict) -> str:  # noqa: PLR0911 — one return per event type
    t = obj.get("type")
    p = obj.get("payload", {})
    if t == "header":
        o = obj.get("orchestrator", {})
        return f"── run {obj.get('run_id')} · {o.get('endpoint')}:{o.get('model')} ──"
    if t == "assistant_action":
        say = p.get("say", "")
        brief = {"util": f"{p.get('name')} {' '.join(p.get('args') or [])}".strip(),
                 "shell": (p.get("command") or "")[:80],
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
        return _render_observation(p)
    if t == "question":
        return f"    ? [{p.get('mode')}] {p.get('question')}"
    if t == "answer":
        return f"    ! answered: {p.get('text', '')[:80]}"
    if t == "user_injection":
        # the CHANNEL, not just the text: an escalation ladder's directive is filed as an
        # injection too (engine/ladder.py, via="oversight") and it is not the operator speaking
        who = f" ↑ {p.get('source') or 'a rung above'}" if p.get("via") == "oversight" else ""
        return f"    + injected{who}: {p.get('text', '')[:80]}"
    if t == "oversight_dispatch":
        return (f"    ↑ rung {p.get('rung')} fired at turn {p.get('turn')} "
                f"({p.get('reason') or 'interval'}), judging turns {p.get('since_turn')}–"
                f"{p.get('turn')} on {p.get('oversight_turns')} turns of its own")
    if t == "oversight_directive":
        return (f"    ↑ rung {p.get('rung')}: {p.get('verdict')} · {p.get('disposition')} · "
                f"next rung in {p.get('next_rung_in')} turns")
    if t == "oversight_skipped":
        rung = f" rung {p['rung']}" if p.get("rung") else ""
        return f"    ↑ oversight{rung} skipped: {p.get('reason') or 'no reason recorded'}"
    if t == "oversight_no_directive":
        return f"    ↑ rung {p.get('rung')} handed back no directive ({p.get('status')})"
    if t == "action_cancelled":
        brief = f" · {p['brief']}" if p.get("brief") else ""
        return f"    ✕ cancelled by the user: {p.get('kind')}{brief}"
    if t == "error":
        return f"    ✗ error ({p.get('where')}): {p.get('message', '')[:120]}"
    if t == "compaction":
        if p.get("before_estimated_tokens") is not None:
            return (f"    ⇣ compacted context ({p['before_estimated_tokens']} → "
                    f"{p['after_estimated_tokens']} estimated tokens)")
        return f"    ⇣ compacted context ({p.get('before_chars')} → {p.get('after_chars')} chars)"
    if t in ("subrun_start", "subrun_end"):
        label = child.mode_short(str(p.get("mode") or ""))
        if t == "subrun_start":
            return f'    ↳ {label} #{p.get('n')} "{p.get('label')}" started ({p.get('workflow')})'
        return f"    ↰ {label} #{p.get('n')} {p.get('status')} — {p.get('turns')} turns"
    if t == "refusal":
        model = f" · {p['model']}" if p.get("model") else ""
        # the operator seam names the model that carries on (engine/refusal.record_operator_flag)
        takeover = f" → {p['takeover_model']} takes over" if p.get("takeover_model") else ""
        return (f"    ⊘ refusal flagged ({p.get('where')}{model}): "
                f"{p.get('message', '')[:120]}{takeover}")
    if t == "stopping_update":
        return f"    — {_accounting(p)} —"
    if t == "stages_skipped":
        entered = ", ".join(p.get("entered") or []) or "none"
        return (f"    ↷ stages skipped: {', '.join(p.get('skipped') or [])} "
                f"(entered: {entered})")
    if t == "finish":
        return f"── finish: {p.get('status')} ──\n{p.get('summary', '')}"
    return f"    · {t}"


def _render_observation(p: dict) -> str:  # noqa: PLR0911 — one return per action kind
    kind = p.get("kind")
    if kind == "util":
        return f"    ← util {p.get('name')}: " + ("missing" if p.get("missing")
                                                  else f"exit {p.get('exit')}")
    if kind == "shell":
        return f"    ← shell: exit {p.get('exit')}"
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
    if isinstance(rode := p.get("then_script"), dict):     # engine/thenscript.py
        return f"    ← {kind} → then_script {rode.get('name')}: exit {rode.get('exit')}"
    if p.get("then_script_skipped"):
        return f"    ← {kind} failed — then_script not run"
    return f"    ← {kind}"


def _accounting(p: dict) -> str:
    """A `stopping_update` in the words the console's transcript uses for it."""
    if p.get("goal_reached"):
        return ("the finish line is reached: scheduling stops; a Decisions card asks to "
                "confirm retiring the routine")
    judged = " · ".join(f"{i} {v}" for i, v in (p.get("judged") or {}).items())
    met = f" · proved {', '.join(p['met'])}" if p.get("met") else ""
    disputed = (f" · a check of the transcript disputed {', '.join(p['disputed'])}"
                if p.get("disputed") else "")
    return f"accounting: {judged or 'nothing owed'}{met}{disputed}"
