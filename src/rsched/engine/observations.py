"""Observation rendering — the dispatch result of one action, turned into the next user
message (`format_observation`), plus the head+tail truncation every large output rides
through. The transcript renderer's counterpart: observation wording is prompt surface
(docs/prompt-anatomy.md) and lives here in ONE place per kind. It never raises: an error
result every kind can word, and a renderer that breaks costs the run one line, not the run
(`obs_floor`).
"""

from __future__ import annotations

import json

from . import exitattr, obs_floor, obs_hold, obs_secrets, outputs, samestub, thenscript
from .obs_admin import dialog_reply
from .replyactions import unexecuted_note

OBS_CAP_CHARS = 8_000


def truncate(text: str, cap: int = OBS_CAP_CHARS, keep: str = "head+tail") -> tuple[str, bool]:
    """Cap a large output for its observation. `keep`:
    - "head+tail" (default): elide the MIDDLE — for failure stderr, whose traceback END is
      the repair material.
    - "head": drop the TAIL — for ordered STDOUT spilled in full to `.util_outputs/`, so a
      reader continues IN SEQUENCE; the marker names the resume point as `read_file`
      arguments (`resume_at`).
    """
    if len(text) <= cap:
        return text, False
    if keep == "head":
        marker = (f"\n[... output truncated: showing first {cap} of {len(text)} chars — "
                  f"the rest is in the spill file: read_file it with {resume_at(text, cap)} "
                  "...]\n")
        return (text[:cap] + marker), True
    head = int(cap * 0.6)
    tail = cap - head
    marker = f"\n[... output truncated: showing {cap} of {len(text)} chars (head+tail) ...]\n"
    return (text[:head] + marker + text[-tail:]), True


def resume_at(text: str, offset: int) -> str:
    """The `read_file` arguments that land on char `offset` of `text` once it is a file:
    `start_line=L` plus `start_char=C` when the offset falls inside a line.

    Counted with read_file's universal-newline semantics, because tool output is full of lone
    carriage returns (progress bars) that read_file splits on.
    """
    head = text[:offset].replace("\r\n", "\n").replace("\r", "\n")
    line = head.count("\n") + 1
    col = len(head) - (head.rfind("\n") + 1)
    return f"start_line={line}" + (f" start_char={col}" if col else "")


#: Keys whose mere PRESENCE means the action did not do what it was asked (failure is spelled
#: differently per kind).
_FAILURE_KEYS = ("error", "missing", "rejected", "declined", "declined_secrets",
                 "pending_secrets", "edit_failed", "callers")


def is_failure(obs: dict) -> bool:
    """Did this observation report a failure? The one classifier for a question every
    consumer of an observation eventually asks.

    A non-zero `exit` counts for every kind, `shell` included (whose non-zero exit is often
    the answer), because "did that fail" wants the reader's answer. A present-but-empty
    `problems` list is a pass.

    The `*_ok: False` flags are one shape — a gate that refused before the work started — so a
    new `*_ok` flag joins the tuple the day it is written, or its refusal reads as success.
    """
    if int(obs.get("exit") or 0) != 0:
        return True
    if any(obs.get(k) is False for k in ("lint_ok", "selftest_ok", "header_ok")):
        return True
    if any(obs.get(k) for k in _FAILURE_KEYS):
        return True
    # a write's riding script (engine/thenscript.py) failing is the action failing — the same
    # answer the two actions would have given apart
    if isinstance(rode := obs.get(thenscript.FIELD), dict) and is_failure(rode):
        return True
    # a `paths` batch fails per file, and one bad file in eight is still a failure to report
    return any(isinstance(f, dict) and f.get("error") for f in obs.get("files") or [])


#: The kinds whose own renderer words a REFUSAL (a `reason` instead of a result). Every other
#: kind's refusal is worded generically, because its renderer reads result fields a refused
#: action never produced — and a raise there breaks every resume, which re-renders it.
_OWN_REFUSALS = frozenset({"child", "detach", "create_routine", "manage_lane"})


def _not_executed(obs: dict, kind: str) -> str | None:
    """The wording of an observation that reports an action the engine did NOT execute — a
    hold, a finish the gate set aside, a refusal recorded before dispatch. None for a dispatch
    result, which the per-kind renderers below word.
    """
    if kind in obs_hold.RENDERERS:
        # a hold, intercepted BEFORE execution (wording in obs_hold, mechanism in hold.py)
        return obs_hold.RENDERERS[kind](obs)
    if kind == "finish" and obs.get("rejected") and obs.get("message"):
        # a finish the gate set aside: the stored deferral message is what the model read
        return str(obs["message"])
    if obs.get("rejected") and obs.get("reason") and kind not in _OWN_REFUSALS:
        return f"OBSERVATION ({kind} REJECTED): {obs['reason']}"
    if obs.get("root_check"):
        # the root command check held it before dispatch, whatever its kind (engine/rootcheck)
        name = f" {obs['name']}" if obs.get("name") else ""
        return f"OBSERVATION ({kind}{name} NOT run): {obs['error']}"
    if obs.get("engine_error"):
        # a handler that RAISED — never the kind's renderer, which reads keys a crash never set
        return f"OBSERVATION ({kind} FAILED — engine error): {obs['error']}"
    return None


def _run_body(obs: dict) -> str:
    """The body every EXECUTED util, script and shell shares: what it printed, plus the
    pointer to what the observation could not carry. Per-kind tails stay with their kind.
    """
    body = obs.get("stdout") or "(no stdout)"
    if obs.get("stderr"):
        body += f"\n[stderr]\n{obs['stderr']}"
    if full := obs.get("full_output"):
        # the spill pointer rides the truncated observation, so the store needs no index
        body += "\n[full output] " + outputs.pointer_line(full)
    return body


def _killed_note(obs: dict) -> str:
    """The tail that turns `exit: -9` into a diagnosis with a next move (F622).

    A negative exit is a SIGNAL; the run is told which one and whether the OOM killer took the
    child, because a run told only "it crashed" retries unchanged. Empty for an ordinary exit.
    """
    if not obs.get("killed_by"):
        return ""
    if obs.get("kill_cause") == "oom_kill":
        peak = obs.get("children_vm_hwm_kb")
        gb = f"{peak / 1024 / 1024:.1f} GB" if peak else "unknown"
        return ("\n[killed] the child was stopped by "
                f"{obs['killed_by']} and the peak memory across this run's children was "
                f"{gb} — this is the OUT-OF-MEMORY killer, not a fault in the command. "
                "Re-running it unchanged will be killed again: process the work in smaller "
                "chunks, stream instead of loading everything, or raise the memory ceiling.")
    return (f"\n[killed] the child was stopped by {obs['killed_by']} — it did not fail on its "
            "own, something outside it ended it (a supervisor, a deploy, a manual kill). The "
            "command's own correctness is not what this exit code reports.")


def _secret_gate(obs: dict, kind: str) -> str:
    """D39's secret-exposure gate stopped a util or script call: it was NOT run — say why and
    what to do next.

    A DECLINE gives the model only a count, never the names the user just protected (R17); a
    PENDING request names them, being the run's own open ask. An ask-back is worded by
    `obs_admin.dialog_reply`: calling again re-submits it.
    """
    if declined := obs.get("declined_secrets"):
        why = (f"secret exposure declined for {len(declined)} "
               f"secret{'s' if len(declined) != 1 else ''} it declares")
    else:
        why = f"secret exposure pending for {', '.join(obs['pending_secrets'])}"
    head = f"OBSERVATION ({kind} {obs['name']} NOT run — {why}): "
    if obs.get("dialog"):
        return head + dialog_reply(
            obs, "request", f"call {kind} {obs['name']!r} again, your answer in its `say` (the "
            "call re-submits the exposure request)",
            f"The {kind} does not run, and no secret is exposed, until they decide.")
    text = head + str(obs["reason"])
    if obs.get("answer"):
        text += f"\nThe user's verbatim reply: {obs['answer']}"
    return text


def format_observation(obs: dict) -> str:
    """The user message an observation becomes — the same words live and on a resumed leg's
    replay, never raising (`render_observation`).
    """
    return render_observation(obs)[0]


def render_observation(obs: dict) -> tuple[str, str | None]:
    """`format_observation`, plus the DEFECT when the kind's renderer raised (None otherwise).

    On a raise the run reads the raw result instead (a resume re-renders stored observations);
    `live_observation` records the defect on a live turn.
    """
    try:
        if stub := samestub.render(obs):   # an unchanged repeat result (engine/loopshape.py)
            return stub, None
        return (_observation_body(obs) + outputs.observation_tail(obs)
                + unexecuted_note(obs.get("not_executed"),
                                  str(obs.get("chain_stopped") or ""))), None
    except Exception as exc:  # the floor: any renderer defect costs a line, never the run
        return obs_floor.defect_text(obs, exc), obs_floor.describe(exc)


def live_observation(obs: dict, ctx) -> str:
    """The rendering a LIVE turn appends — a renderer defect is recorded here, once; a replay
    calls `format_observation` and renders the same words without recording them again.
    """
    text, defect = render_observation(obs)
    if defect is not None:
        obs_floor.record(ctx, obs, defect)
    return text


# One flat renderer on purpose: observation wording is prompt surface (docs/prompt-anatomy.md)
# and lives in ONE place per kind — a dispatch table would only scatter the strings.
def _observation_body(obs: dict) -> str:  # noqa: PLR0911
    kind = obs.get("kind")
    if (not_run := _not_executed(obs, str(kind or ""))) is not None:
        return not_run
    if obs.get("background") and (obs.get("started") or obs.get("abandoned")
                                  or obs.get("cancelled")):
        # A background call started, abandoned or cancelled has produced no result fields, so
        # it is worded here, ABOVE the per-kind chain whose renderers would raise on it. The
        # real result arrives later and is rendered by its kind's own branch.
        if obs.get("started"):
            return (f"OBSERVATION ({kind} STARTED IN THE BACKGROUND as "
                    f"`{obs.get('handle')}`): {obs.get('note') or ''}").rstrip()
        ended = "CANCELLED" if obs.get("cancelled") else "ABANDONED"
        return (f"OBSERVATION ({kind} background call `{obs.get('handle')}` {ended}, started "
                f"on turn {obs.get('started_turn')}): {obs.get('note') or ''}").rstrip()
    if (failed := obs_floor.failed(obs, str(kind or ""))) is not None:
        return failed   # an error result, for a kind whose own renderer words none
    if kind == "do_nothing":
        # D186: echo the reasoning; this turn established nothing (engine/donothing.py)
        return (f"OBSERVATION (do_nothing — recorded, nothing was done): "
                f"{obs.get('reasoning') or ''}\nThis statement grounds no claim: a top-level "
                f"finish(ok) still needs an action that actually established something.")
    if kind == "shell":
        # No advisory tail: a non-zero exit here is usually the answer, not a mistake (do_shell).
        where = f", in {obs['cwd']}" if obs.get("cwd") else ""
        return (f"OBSERVATION (shell, exit {obs['exit']}{where}{exitattr.note(obs)}):\n"
                + _run_body(obs)
                + _killed_note(obs))
    if kind in ("util", "script"):
        if kind == "util" and obs.get("name") == "search":
            return (f"OBSERVATION (util search {obs.get('query')!r} — closest utils "
                    "by keyword; the full catalog is always in CAPABILITIES):\n"
                    + obs["listing"])
        if obs.get("listing") is not None:
            return "OBSERVATION (util list — available global utils):\n" + obs["listing"]
        if obs.get("source") is not None:
            out = (f"OBSERVATION (util show — source of {obs['target']!r}; revise it with "
                   "write_util: 'content' for a full rewrite, or 'anchor'/'replacement' "
                   "to patch it in place):\n" + obs["source"])
            if obs.get("hint"):
                out += "\n\n[hint] " + obs["hint"]
            return out
        if obs.get("missing"):
            names = ", ".join(obs.get("available") or []) or "(none yet)"
            if kind == "script":
                return (f"OBSERVATION (script {obs['name']!r} does not exist). Available: "
                        f"{names}. Author it first — write_file scripts/{obs['name']}.py, "
                        "a PEP 723 script whose docstring header carries "
                        "'<name> — <summary>', 'net:', 'secrets:' — then call it again. "
                        "Script names are lowercase letters/digits with '-' or '_'.")
            if obs.get("script_match"):
                # a routine-local script: give the EXACT `script` call (absent from a schema
                # only when the recipe's `tools:` list leaves it out)
                call = json.dumps({"kind": "script", "name": obs["name"],
                                   "args": obs.get("args") or []}, ensure_ascii=False)
                return (f"OBSERVATION (util {obs['name']!r} does not exist — it is a ROUTINE-"
                        f"LOCAL script, scripts/{obs['name']}.py). Nothing ran. Call it as: "
                        f"{call}. If \"script\" is not among your action kinds, this recipe's "
                        "tools: list leaves it out — file a report naming the script.")
            return (f"OBSERVATION (util {(obs.get('target') or obs['name'])!r} does not exist). "
                    f"Available: {names}. Pick one of those (run `util name=list` for their "
                    "usage), or write it with write_util, then call it.")
        if obs.get("declined_secrets") or obs.get("pending_secrets"):
            return _secret_gate(obs, str(kind))
        if err := obs.get("error"):
            # a DECLARATION gate refused it before anything ran: no `exit` to report, the
            # message is the repair route
            return f"OBSERVATION ({kind} {obs.get('name')} NOT run): {err}"
        head = f"OBSERVATION ({kind} {obs['name']}, exit {obs['exit']})"
        body = _run_body(obs)
        if obs.get("usage"):
            body += f"\n[usage] {obs['usage']}"
        # withheld secrets, by consent and by verb, said out loud (obs_secrets)
        body += obs_secrets.withheld_notes(obs)
        if obs.get("hint"):
            body += f"\n[hint] {obs['hint']}"
        body += _killed_note(obs)
        return f"{head}:\n{body}"
    # Each domain module keeps EVERY string for its own kinds; this only owns the order and
    # the fallback, so a kind's wording is still in exactly one place.
    from .goalops import format_goal
    from .jobops import format_job
    from .llmaction import format_models
    from .obs_admin import format_admin
    from .obs_children import format_children
    from .obs_files import format_files
    from .obs_library import format_library
    from .obs_retired import format_retired
    from .unknownoutcome import format_unknown
    for fmt in (format_files, format_library, format_children, format_admin, format_retired,
                format_models, format_job, format_goal, format_unknown):
        if (out := fmt(obs, str(kind or ""))) is not None:
            return out
    return f"OBSERVATION ({kind}): {json.dumps(obs, ensure_ascii=False)[:500]}"
