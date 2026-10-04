"""Observation rendering — the dispatch result of one action, turned into the next user
message (`format_observation`), plus the head+tail truncation every large output rides
through. The transcript renderer's counterpart: observation wording is prompt surface
(docs/prompt-anatomy.md) and lives here in ONE place per kind.
"""

from __future__ import annotations

import json

from . import obs_hold, outputs
from .obs_admin import dialog_reply

OBS_CAP_CHARS = 8_000


def truncate(text: str, cap: int = OBS_CAP_CHARS, keep: str = "head+tail") -> tuple[str, bool]:
    """Cap a large output for its observation. `keep`:
    - "head+tail" (default): keep both ends, elide the MIDDLE — for failure stderr, where
      the traceback's END is the repair material and must survive (test_utils
      keeps_trace_tail pins it).
    - "head": keep the HEAD only, drop the TAIL — for ordered STDOUT that is spilled in
      full to `.util_outputs/`, so a reader continues IN SEQUENCE from the spill file at
      the char the preview stopped (operator AUDIT note R45: mid-truncation breaks
      sequential paging). The marker names that resume offset.
    """
    if len(text) <= cap:
        return text, False
    if keep == "head":
        marker = (f"\n[... output truncated: showing first {cap} of {len(text)} chars — "
                  f"read the spill file from char {cap} for the rest ...]\n")
        return (text[:cap] + marker), True
    head = int(cap * 0.6)
    tail = cap - head
    marker = f"\n[... output truncated: showing {cap} of {len(text)} chars (head+tail) ...]\n"
    return (text[:head] + marker + text[-tail:]), True


#: Keys whose mere PRESENCE means the action did not do what it was asked. Failure is
#: spelled differently per kind — an exit code here, a message there, a refusal somewhere
#: else — and before this every reader of an observation had to know all of them.
_FAILURE_KEYS = ("error", "missing", "rejected", "declined", "declined_secrets",
                 "pending_secrets", "edit_failed", "callers")


def is_failure(obs: dict) -> bool:
    """Did this observation report a failure? The one classifier for a question every
    consumer of an observation eventually asks.

    Deliberately conservative about `exit`: a non-zero exit IS the failure signal for the
    three callable kinds, but `shell` documents a non-zero exit as often being the answer
    rather than a mistake (a grep that found nothing), so it counts here too only because a
    caller asking "did that fail" wants the same answer a reader would give. `lint_ok: False`
    and an empty `problems` list are distinguished: a present-but-empty list is a pass.
    """
    if int(obs.get("exit") or 0) != 0:
        return True
    if obs.get("lint_ok") is False or obs.get("selftest_ok") is False:
        return True
    if any(obs.get(k) for k in _FAILURE_KEYS):
        return True
    # a `paths` batch fails per file, and one bad file in eight is still a failure to report
    return any(isinstance(f, dict) and f.get("error") for f in obs.get("files") or [])


#: The kinds whose own renderer words a REFUSAL — the action never reached the executor, so its
#: observation carries a `reason` instead of a result. Every other kind's renderer reads the
#: fields of a dispatch RESULT (`exit`, `bytes`, `path`, `qid` …), which a refused action never
#: produced: the reserved finish turn's refusal of a non-finish action (`loop.py`) raised
#: KeyError in fifteen of them, and since a resume re-renders every stored observation, that
#: left the run unresumable — a conversation could not take its next message.
_OWN_REFUSALS = frozenset({"spawn", "subtask", "detach", "create_routine", "manage_lane"})


def _not_executed(obs: dict, kind: str) -> str | None:
    """The wording of an observation that reports an action the engine did NOT execute — a
    hold, a finish the gate set aside, a refusal recorded before dispatch. None for a dispatch
    result, which the per-kind renderers below word.
    """
    if kind in obs_hold.RENDERERS:
        # The family a hold belongs to: the action was intercepted BEFORE execution, which is
        # the entire point. Two sources word it differently and both live in obs_hold;
        # engine/hold.py owns the mechanism.
        return obs_hold.RENDERERS[kind](obs)
    if kind == "finish" and obs.get("rejected") and obs.get("message"):
        # A finish the gate set aside (engine/finishgate.py): the deferral message IS what the
        # model read, stored beside the rung's own keys so a resumed leg replays it.
        return str(obs["message"])
    if obs.get("rejected") and obs.get("reason") and kind not in _OWN_REFUSALS:
        return f"OBSERVATION ({kind} REJECTED): {obs['reason']}"
    if obs.get("engine_error"):
        # A handler that RAISED (actionroute.dispatch_action) — worded here, never by the
        # kind's own renderer, which reads keys (an exit code, a name) a crash never set.
        return f"OBSERVATION ({kind} FAILED — engine error): {obs['error']}"
    return None


def _run_body(obs: dict) -> str:
    """The body every EXECUTED command shares — util, script and shell alike: what it printed,
    plus the pointer to whatever the observation could not carry. One copy, so the three
    callable kinds cannot start describing their output differently. Per-kind tails (a util's
    usage line and repair route, its withheld optional secrets) stay with their kind.
    """
    body = obs.get("stdout") or "(no stdout)"
    if obs.get("stderr"):
        body += f"\n[stderr]\n{obs['stderr']}"
    if full := obs.get("full_output"):
        # The pointer rides the observation that lost the middle — the moment of need,
        # so the store needs no index and costs nothing on an untruncated call.
        body += "\n[full output] " + outputs.pointer_line(full)
    return body


def _killed_note(obs: dict) -> str:
    """The tail that turns `exit: -9` into a diagnosis with a next move (F622).

    A negative exit is a SIGNAL, and the engine already knew which one and — for a SIGKILL
    whose peak supports it — that the cgroup OOM killer took the child. All of it went to the
    health stream, i.e. the OPERATOR's surface, while the author got an exit code with empty
    output: indistinguishable from an ordinary crashed command, and a run told that RETRIES
    (c-20261002-194128 re-ran one script at turns 93 and 95 with an identical 5,103,384 kB
    high-water mark). The next move is the point of saying it — an author told the work was
    too big for the memory ceiling chunks it; one told "it crashed" runs it again.

    Empty for every ordinary exit, so nothing is added to the 99% of observations that ran.
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

    A DECLINE never enumerates the names it refused (R17): the refusal must not read as a
    consolation listing of exactly what the user just protected — the model gets a count; the
    transcript dict keeps the names for the user's own surfaces. A PENDING request still names
    them: it is the run's open ask, not a refusal, and the names are the run's working knowledge
    (the util's own `secrets:` declarations). An ask-back on that request is worded the one way
    every blocking decision's is (`obs_admin.dialog_reply`): calling again re-submits it.
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


# One flat renderer on purpose: observation wording is prompt surface (docs/prompt-anatomy.md)
# and lives in ONE place per kind — a dispatch table would only scatter the strings.
def format_observation(obs: dict) -> str:  # noqa: PLR0911
    kind = obs.get("kind")
    if (not_run := _not_executed(obs, str(kind or ""))) is not None:
        return not_run
    if obs.get("background") and obs.get("started"):
        # D118 phase 1: the call was HANDED to a background thread, so it has produced nothing
        # yet — no exit code, no stdout, none of the fields its kind's own renderer reads. It
        # must be rendered here, ABOVE the per-kind chain, for the same reason `_not_executed`
        # is: a renderer that assumes its kind's output shape raises on an observation that
        # has no output yet, and a KeyError in here kills the engine mid-turn (and every later
        # resume, which re-renders the stored observation). The real observation arrives later
        # and is rendered by its kind's own branch, in the ordinary way.
        return (f"OBSERVATION ({kind} STARTED IN THE BACKGROUND as "
                f"`{obs.get('handle')}`): {obs.get('note') or ''}").rstrip()
    if kind == "shell":
        # No advisory tail: a non-zero exit here is usually the answer, not a mistake (do_shell).
        where = f", in {obs['cwd']}" if obs.get("cwd") else ""
        return (f"OBSERVATION (shell, exit {obs['exit']}{where}):\n" + _run_body(obs)
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
            miss = (f"OBSERVATION (util {(obs.get('target') or obs['name'])!r} does not exist). "
                    f"Available: {names}. Pick one of those (run `util name=list` for their "
                    "usage), or write it with write_util, then call it.")
            if obs.get("script_match"):
                # R367: the file exists as a routine-local script — the util action will
                # never run it; teach the one action that does. `script` is a BASE kind
                # (grants.py: no capability, no permission to request), so the one way it is
                # absent from a schema is the recipe's own `tools:` list leaving it out.
                miss += (f" NOTE: {obs['name']!r} exists as a ROUTINE-LOCAL script "
                         f"(scripts/) — run it with the script action: "
                         f'{{"kind": "script", "name": "{obs["name"]}"}}. If "script" is '
                         "not among your action kinds, this recipe's tools: list leaves it "
                         "out — file a report naming the script this step needs.")
            return miss
        if obs.get("declined_secrets") or obs.get("pending_secrets"):
            return _secret_gate(obs, str(kind))
        if err := obs.get("error"):
            # The DECLARATION gates (executor.do_script): the call was refused before
            # anything ran, so there is no exit code to report — the message IS the repair
            # route. Rendered like the secret gate above rather than as a run with a
            # failure, because that is what happened. Without this branch the `exit`
            # lookup below raised, killing the engine mid-turn AND every later resume,
            # since replay re-renders the same stored observation — an unresumable run
            # with an unauthored summary (voice-model-trainer:20260910-030348).
            return f"OBSERVATION ({kind} {obs['name']} NOT run): {err}"
        head = f"OBSERVATION ({kind} {obs['name']}, exit {obs['exit']})"
        body = _run_body(obs)
        if obs.get("usage"):
            body += f"\n[usage] {obs['usage']}"
        if wo := obs.get("withheld_optional"):
            # F290: the call RAN, but optional secrets it declares were not injected.
            # Undecided names are requestable; denied ones stay a count (R17).
            bits = []
            if wo.get("undecided"):
                bits.append(f"{', '.join(wo['undecided'])} (not yet granted — if this call "
                            "actually needed it, request exposure via ask_user with "
                            f"request 'secret:{wo['undecided'][0]}')")
            if wo.get("denied"):
                bits.append(f"{wo['denied']} declined by the user")
            body += ("\n[note] optional secret(s) withheld from this call: "
                     + "; ".join(bits))
        if obs.get("hint"):
            body += f"\n[hint] {obs['hint']}"
        body += _killed_note(obs)
        return f"{head}:\n{body}"
    # Each domain module keeps EVERY string for its own kinds; this only owns the order and
    # the fallback, so a kind's wording is still in exactly one place.
    from .obs_admin import format_admin
    from .obs_children import format_children
    from .obs_files import format_files
    from .obs_library import format_library
    for fmt in (format_files, format_library, format_children, format_admin):
        if (out := fmt(obs, str(kind or ""))) is not None:
            return out
    return f"OBSERVATION ({kind}): {json.dumps(obs, ensure_ascii=False)[:500]}"
