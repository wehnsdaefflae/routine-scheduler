"""The ROOT COMMAND CHECK — a run holding a root login has its calls risk-rated before they run.

A machine whose login has root rights (probed on the box, rsched/machine_privilege.py) turns what
a run sends it into work done as root. While a run holds one — bound in config or granted
mid-run — its routine's `root_check` (rsched/rootmodes.py) picks what is rated first:
`remote`, the `remote` util's exec/submit/push/pull to such a machine (the default); `any`,
every tool call; or `off`. The decision model answers one yes/no question with the FIVE calls
this run last made as context — in `remote` mode the commands sent to that machine. Under
RISK_THRESHOLD the call runs. At or over it, or with no verdict at all, the top-level run
waits on a blocking `command-approval` whose options also switch the mode for this run or
from now on; a child or detached run cannot ask, so its call is refused.

What the check reads — the calls made, the switch chosen at a warning — comes from the run's
own transcript, read incrementally, so a resumed leg rebuilds it. A root machine's KEY reaches
only a direct `remote` call while the check is on (exec_env._machine_env): a script or another
util that `calls: remote` would carry it past the one call this gate sees.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import rootmodes
from ..endpoints.base import EndpointError
from ..endpoints.decisions import DecisionQuestion, decide, pick_decision_model
from ..machine_privilege import ensure_probed, is_root
from ..machine_queue import REMOTE_UTIL
from .askback import still_pending
from .hold import is_hold
from .interact import handle_ask, is_approval
from .loopconst import POLL_S
from .runkind import is_detached_run
from .transcript import read_events

#: The `remote` verbs that run code on the box or move files to or from it, each as the login.
GATED_VERBS = frozenset({"exec", "submit", "push", "pull"})
#: Kinds that only talk to the operator or steer the run itself: never rated, even in `any`.
EXEMPT_KINDS = frozenset({"finish", "ask_user", "report", "wait", "children", "do_nothing",
                          "goal", "kill"})
#: How many earlier calls ride along as context.
PREVIOUS = 5
#: P(risky) at or over this waits for the operator. The decision model's probability is not
#: calibrated for this question, so the bar sits where a coin flip would land.
RISK_THRESHOLD = 0.5
QTYPE = rootmodes.QTYPE
#: The key a refusal carries, so the transcript reader never counts a refused call as made.
REFUSED = "root_check"
_HARM = ("destroying or corrupting data, breaking the system or its services, changing users, "
         "permissions, keys or security settings, or exposing secrets")
QUESTION = {
    "remote": "Would running `command` on this machine, as a login with root rights, risk "
              f"serious harm — {_HARM}? `previous_commands` are what was run there just before "
              "it, oldest first.",
    "any": "This run holds root access to the machines in `root_machines`. Would the tool call "
           f"in `action` risk serious harm — {_HARM}? `previous_actions` are the calls the run "
           "made just before it, oldest first.",
}


@dataclass
class _Seen:
    """What one run's transcript has said so far, read from `offset` on."""

    offset: int = 0
    landed: list[dict] = field(default_factory=list)    # actions not yet observed
    made: list[dict] = field(default_factory=list)      # actions that ran, oldest first
    asked: set[str] = field(default_factory=set)        # qids of this check's warnings
    run_mode: str = ""                                  # a switch chosen at a warning


_seen: dict[Path, _Seen] = {}


def _sync(ctx) -> _Seen:
    path = Path(ctx.transcript.path)
    seen = _seen.setdefault(path, _Seen())
    events, seen.offset = read_events(path, seen.offset)
    for ev in events:
        typ, payload = ev.get("type"), ev.get("payload")
        if not isinstance(payload, dict):
            continue
        if typ == "assistant_action":
            seen.landed.append(payload)
        elif typ == "observation" and seen.landed:
            action = seen.landed.pop(0)
            if not payload.get(REFUSED) and not is_hold(payload):
                seen.made = [*seen.made, action][-50:]
        elif typ == "question" and payload.get("type") == QTYPE:
            seen.asked.add(str(payload.get("qid")))
        elif (typ == "answer" and str(payload.get("qid")) in seen.asked
              and (switch := rootmodes.parse(str(payload.get("text") or "")))):
            seen.run_mode = switch[0]
    return seen


def run_mode(ctx) -> str:
    """The mode this run is under: a switch chosen at a warning, else the routine's own.
    Read with `getattr`: exec_env asks it for every util call, context stand-ins included.
    """
    own = getattr(getattr(ctx, "routine", None), "root_check", rootmodes.DEFAULT)
    if getattr(ctx, "transcript", None) is None:
        return own
    return _sync(ctx).run_mode or own


def adopt(ctx, mode: str) -> None:
    """A mid-run edit of `root_check` (engine/switches.py): the newest decision wins."""
    ctx.routine.root_check = mode
    _sync(ctx).run_mode = ""


def bound_machines(ctx) -> list[str]:
    bound = list(ctx.routine.machines or [])
    return bound + [e.partition(":")[2] for e in sorted(ctx.granted_now)
                    if e.startswith("machine:") and e.partition(":")[2] not in bound]


def _flag(args: list[str], name: str) -> str:
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(name + "="):
            return arg.partition("=")[2]
    return ""


def gated_call(action: dict) -> tuple[str, str] | None:
    """`(machine, command line)` when `action` is a `remote` call that runs code or moves files,
    else None. A push or pull is described as the transfer it makes.
    """
    if action.get("kind") != "util" or action.get("name") != REMOTE_UTIL:
        return None
    args = [str(a) for a in action.get("args") or []]
    if len(args) < 2 or args[0] not in GATED_VERBS:   # a verb and the machine it acts on
        return None
    verb, machine = args[0], args[1]
    if verb in ("push", "pull"):
        line = f"{verb} (SFTP) {_flag(args, '--src')} → {_flag(args, '--dest')}"
    else:
        cwd = _flag(args, "--cwd")
        line = (f"cd {cwd}; " if cwd else "") + (_flag(args, "--command") or " ".join(args[2:]))
    return machine, (line if verb == "exec" else f"[{verb}] {line}")


def describe(action: dict) -> str:
    """One call as the model reads it: a remote command as its line, anything else as JSON."""
    if call := gated_call(action):
        return f"remote {call[0]}: {call[1]}"
    body = {k: v for k, v in action.items() if k not in ("say", "note", "remind")}
    return json.dumps(body, ensure_ascii=False, default=str)[:2000]


def previous(ctx, machine: str | None) -> list[str]:
    """The last PREVIOUS calls this run MADE — refused and held ones are not among them —
    narrowed to the commands sent to `machine` when one is named.
    """
    made = _sync(ctx).made
    if machine is None:
        return [describe(a) for a in made if a.get("kind") not in EXEMPT_KINDS][-PREVIOUS:]
    calls = [c for a in made if (c := gated_call(a)) and c[0] == machine]
    return [line for _, line in calls][-PREVIOUS:]


def _evidence(ctx, mode: str, action: dict, roots: list[str]) -> dict:
    def login(name: str) -> dict:
        mac = ctx.server.machines[name]
        return {"machine": name, "login": f"{mac.user}@{mac.host}",
                "description": mac.description}

    if mode == "remote":
        machine, command = gated_call(action) or ("", "")
        return {**login(machine), "command": command,
                "previous_commands": previous(ctx, machine)}
    return {"root_machines": [login(m) for m in roots], "action": describe(action),
            "previous_actions": previous(ctx, None)}


def rate(ctx, mode: str, evidence: dict) -> tuple[float | None, str]:
    """`(P(risky), what answered)`, or `(None, why there is no verdict)`."""
    question = DecisionQuestion(name="risky", type="yes_no", question=QUESTION[mode])
    try:
        endpoint, ref = pick_decision_model(ctx.server, None, images=False,
                                            roles=ctx.routine.models)
        timeout = ctx.server.decision_endpoints[ref.endpoint].timeout_s
        result = decide(endpoint, ref, evidence, [], [question], timeout=timeout,
                        purpose=f"root command check · {mode}")
    except EndpointError as exc:
        return None, str(exc)
    ctx.add_usage(result.usage)
    answer = result.answers[0] if result.answers else None
    if answer is None or answer.refused or answer.probability is None:
        return None, f"{ref.name} gave no answer"
    return answer.probability, ref.name


def gate(loop, action: dict) -> dict | None:
    """None lets the call run; otherwise the observation of a call that did NOT run."""
    ctx = loop.ctx
    mode = run_mode(ctx)
    if mode == "off" or action.get("kind") in EXEMPT_KINDS:
        return None
    bound = bound_machines(ctx)
    ensure_probed(ctx.server, bound)
    roots = [m for m in bound if is_root(ctx.server, m)]
    call = gated_call(action)
    if not roots or (mode == "remote" and (call is None or call[0] not in roots)):
        return None
    evidence = _evidence(ctx, mode, action, roots)
    p_risky, by = rate(ctx, mode, evidence)
    if p_risky is not None and p_risky < RISK_THRESHOLD:
        return None
    verdict = (f"P(risky) {p_risky:.2f} by {by}" if p_risky is not None
               else f"no risk verdict ({by})")
    return _hold(loop, action, mode, verdict, evidence)


def _hold(loop, action: dict, mode: str, verdict: str, evidence: dict) -> dict | None:
    """Put a flagged call to the operator, or refuse it where no one can be asked."""
    ctx = loop.ctx
    what = describe(action)
    head = f"root command check ({rootmodes.LABELS[mode]}): {verdict}"
    if ctx.depth > 0 or is_detached_run(ctx):
        return _refused(action, head, f"{head}. A child or detached run cannot ask the "
                        "operator, so it did not run — hand it back to the top-level run, or "
                        "do the work without it.")
    before = evidence.get("previous_commands", evidence.get("previous_actions")) or []
    ask = handle_ask(loop, {
        "question": f"Run this call while the run holds a root login "
                    f"({', '.join(bound_machines(ctx))})? {head}.\n\n{what}\n\n"
                    + ("Just before it:\n" + "\n".join(before) if before
                       else "Nothing came before it in this run.")
                    + f"\n\nThis routine checks {rootmodes.LABELS[mode]}; the switch options "
                      "approve this call and change that for this run, or from now on.",
        "mode": "blocking", "options": rootmodes.options(mode),
        "default": "the call does NOT run until approved"}, POLL_S,
        qtype=QTYPE, subject=hashlib.sha256(what.encode()).hexdigest()[:12])
    if not ask.get("answered"):
        words = (f" The operator replied: {json.dumps(ask.get('user_message') or '')} — "
                 "answer it, then make the call again to re-ask." if ask.get("dialog") else
                 " The approval is still open; do other work and retry once it is settled.")
        return _refused(action, head, f"{head}; it did NOT run." + words, **still_pending(ask))
    answer = str(ask["answer"])
    if not is_approval(answer):
        return _refused(action, head, f"{head}; the operator declined it, so it did NOT run. "
                        f"Their reply: {answer:.200}")
    if switch := rootmodes.parse(answer):
        _sync(ctx).run_mode = switch[0]
    return None


def _refused(action: dict, head: str, error: str, **extra) -> dict:
    return {"kind": action.get("kind"), "name": action.get("name"),
            "args": action.get("args") or [], "error": error, REFUSED: head, **extra}
