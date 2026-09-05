"""Observation rendering — the dispatch result of one action, turned into the next user
message (`format_observation`), plus the head+tail truncation every large output rides
through. The transcript renderer's counterpart: observation wording is prompt surface
(docs/prompt-anatomy.md) and lives here in ONE place per kind.
"""

from __future__ import annotations

import json

from ..reminders import LABEL_HELP
from . import outputs

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


# One flat renderer on purpose: observation wording is prompt surface (docs/prompt-anatomy.md)
# and lives in ONE place per kind — a dispatch table would only scatter the strings.
def format_observation(obs: dict) -> str:  # noqa: PLR0911
    kind = obs.get("kind")
    if kind == "reminder_hold":
        # The consequence-reminder layer's ONE model-facing string (engine/remind.py holds
        # the mechanism). It has to carry four things: that nothing ran, what the caution is,
        # how to proceed anyway, and how to label the outcome — a hold the model cannot act
        # on precisely is a turn spent for nothing.
        cautions = "\n".join(f"- [{r['id']} · {r['scope']}] {r['description']}"
                             for r in obs.get("reminders") or [])
        return (f"ACTION HELD — it did NOT run. `{obs.get('action')}` matches a consequence "
                f"reminder left for exactly this moment:\n{cautions}\n"
                "Decide again with that in front of you. To go ahead anyway, emit the SAME "
                "action again — it runs this time (one hold per action string per run). To "
                "avoid the consequence, do something else instead.\n"
                "Then LABEL what happened: carry `remind_feedback` with the id above and one "
                "of could_not / would_have / did / didnt on the action where you know the "
                "outcome — at once if you are changing course, on the turn AFTER the held "
                f"action ran if you went ahead. {LABEL_HELP}")
    if kind == "shell":
        # No advisory tail: a non-zero exit here is usually the answer, not a mistake (do_shell).
        where = f", in {obs['cwd']}" if obs.get("cwd") else ""
        return f"OBSERVATION (shell, exit {obs['exit']}{where}):\n" + _run_body(obs)
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
                # never run it; teach the one action that does, and the grant to request
                # when that kind is absent from this run's schema.
                miss += (f" NOTE: {obs['name']!r} exists as a ROUTINE-LOCAL script "
                         f"(scripts/) — run it with the script action: "
                         f'{{"kind": "script", "name": "{obs["name"]}"}}. If "script" is '
                         "not among your action kinds it is gated behind the scripts "
                         'permission — request it via ask_user with request: '
                         '"action:script".')
            return miss
        if obs.get("declined_secrets") or obs.get("pending_secrets"):
            # D39 secret-exposure gate: the util was NOT run — say why and what to do next.
            # A DECLINE never enumerates the names it refused (R17): the refusal must not
            # read as a consolation listing of exactly what the user just protected — the
            # model gets a count; the transcript dict keeps the names for the user's own
            # surfaces. A PENDING request still names them: it is the run's open ask, not
            # a refusal, and the names are the run's working knowledge (the util's own
            # `secrets:` declarations).
            if declined := obs.get("declined_secrets"):
                head = (f"secret exposure declined for {len(declined)} "
                        f"secret{'s' if len(declined) != 1 else ''} it declares")
            else:
                head = f"secret exposure pending for {', '.join(obs['pending_secrets'])}"
            text = f"OBSERVATION ({kind} {obs['name']} NOT run — {head}): {obs['reason']}"
            if obs.get("answer"):
                text += f"\nThe user's verbatim reply: {obs['answer']}"
            return text
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
