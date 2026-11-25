"""Dispatch a validated action to its effect and return the observation dict.

DISPATCH covers the EFFECT kinds: util and shell (the util runner and the one-off command
live here), the file kinds (read_file / write_file / edit_file — fileops.py; delete / move /
mkdir — fsops.py), view_image (mediaops.py), the name-addressed stores (memory_read /
memory_write / read_rule — memops.py) and list_models (llmaction.py, whose `llm` handler left
with D288: reaching a model is a util capability, so no EFFECT kind buys inference any more).
`script`, the third way a run executes code, is here but reached through
actionroute.py, which puts the call-time secret gate in front of it and of `util`. Every other
kind has its own handler there — the child-run kinds in subruns.py (`wait` in waitop.py),
ask_user in interact.py, the library authoring kinds in authoring.py — and `finish` never leaves
the loop. Each observation dict feeds the transcript event and the next user message.
"""

from __future__ import annotations

from pathlib import Path

from .. import sandbox, shellrun, utils_lib, utils_run
from ..ids import is_slug
from ..paths import expand
from ..utils_lib import USAGE_ERROR_EXIT
from .control import cancelled_for_turn
from .exec_env import _extra_secrets, _unbound_connection_request
from .fileops import UTIL_DEFAULT_TIMEOUT_S, do_edit_file, do_read_file, do_write_file
from .fsops import do_delete, do_mkdir, do_move
from .jailnotes import jail_notes as _jail_notes
from .llmaction import do_list_models
from .mediaops import do_view_image
from .memops import do_memory_read, do_memory_write, do_read_rule
from .observations import truncate
from .output_compression import command_output
from .reach import routine_root
from .run_context import RunContext, work_dir
from .shellwrite import marks_a_write, record_ungated_write
from .shiftedfields import could_be_util
from .utilspend import run_and_book


def script_home(ctx: RunContext, name: str) -> Path | None:
    """The dir whose `scripts/<name>.py` a `script` action runs (or None): the OPEN task's
    workspace, the run's own dir, then a CHILD's routine. A script runs from the home it was
    found in — its working directory, its venv, what `Path(__file__).parent.parent` names.
    """
    from .. import scripts

    for home in _script_homes(ctx):
        if scripts.exists(home, name):
            return home
    return None


def _script_homes(ctx: RunContext) -> list[Path]:
    # a child's own dir is a workspace with no scripts/, and it holds no `shell` to test with
    return list(dict.fromkeys((work_dir(ctx), ctx.routine.dir, routine_root(ctx))))


def available_scripts(ctx: RunContext) -> list[str]:
    """Every script a `script` action could reach right now, by name."""
    from .. import scripts

    return list(dict.fromkeys(s["name"] for h in _script_homes(ctx)
                              for s in scripts.list_scripts(h)))


def _note_if_killed(ctx: RunContext, kind: str, name: str, code: int) -> dict:
    """A child command the KERNEL stopped: emit `util_killed`, and SAY SO IN THE OBSERVATION.

    Returns the fields the author's observation needs (empty for an ordinary exit), so the one
    measurement reaches both the health stream and the run: a bare `exit: -9` reads like an
    ordinary crash and gets retried, while "killed by SIGKILL, the cgroup OOM killer" gets the
    work chunked instead (F622).

    The verdict comes from `runner_reap.classify_cause` — the SAME classifier the daemon
    applies to a dying engine, deliberately not a second one (a small peak is not called an
    OOM). `children_vm_hwm_kb` is this run's high-water mark across ALL its finished children
    — an upper bound on the one that died.

    Negative must mean a SIGNAL and nothing else, which is why the deadline exits
    `utils_run.TIMEOUT_EXIT` (124, positive) for all three kinds.
    """
    if code >= 0:
        return {}
    import resource
    import signal as signalmod

    from ..daemon.runner_reap import classify_cause
    from ..health_events import log_health_event

    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss or None
    log_health_event(
        ctx.server.routines_home, "util_killed",
        routine=getattr(ctx.routine, "slug", "") or "", run_id=getattr(ctx, "run_id", "") or "",
        detail=f"{kind} {name} was killed by signal {-code} (run turn {ctx.turn})",
        kind=kind, util=name, signal=-code,
        children_vm_hwm_kb=peak)
    try:
        signame = signalmod.Signals(-code).name
    except ValueError:            # a signal number this platform does not name
        signame = f"signal {-code}"
    cause = classify_cause(code, vm_hwm_kb=peak)
    fields: dict = {"killed_by": signame, "signal": -code, "kill_cause": cause}
    if cause == "oom_kill":
        # The peak rides the observation ONLY where it supports the verdict: on a
        # `signal_kill` it says nothing about the death.
        fields["children_vm_hwm_kb"] = peak
    return fields




def _ended_by_abort(ctx: RunContext, code: int) -> bool:
    """Whether the run's abort ended this util or script call (`utils_run.run_jailed`). A
    command that exited `ABORT_EXIT` by itself just as its run was aborted counts as ended by
    the abort — it would have been, a moment later.
    """
    return code == utils_run.ABORT_EXIT and ctx.aborted()


def _ended_by_cancel(code: int) -> bool:
    """Whether a PERSON stopped this call (D160-C). `CANCEL_EXIT` is reported by `run_jailed`
    only when its own `cancelled` callback said so, so the code alone is the evidence. Deadline,
    abort and cancel call for three different next moves, so the observation names which.
    """
    return code == utils_run.CANCEL_EXIT


def _ended_by_a_person(ctx: RunContext, code: int) -> str | None:
    """Which DELIBERATE ending this call had, as the observation field that names it — or None.
    A call a person ended is not a util failure: no reliability tick, no `usage`, no repair route.
    """
    if _ended_by_abort(ctx, code):
        return "aborted"
    if _ended_by_cancel(code):
        return "cancelled"
    return None


def do_util(action: dict, ctx: RunContext) -> dict:  # noqa: PLR0911 — many small exits
    name = action["name"]
    args = [str(a) for a in (action.get("args") or [])]
    home = ctx.server.libraries_home
    if name == "list":  # discovery: `gu list` — the catalog is derived live, never stale
        # With a util name in args, list ONLY that util's entry — the full catalog is
        # already in CAPABILITIES.
        target = next((a for a in args if not a.startswith("-")), "")
        if target and target != "all":
            entry = next((u for u in utils_lib.list_utils(home) if u["name"] == target), None)
            if entry is None:
                return {"kind": "util", "name": "list", "target": target, "missing": True,
                        "available": [u["name"] for u in utils_lib.list_utils(home)]}
            # the WHOLE usage block: a verb-dispatched util's first line names one verb only
            lines = utils_lib.entry_lines(entry, full_usage=True)
            if entry.get("tags"):
                lines.append(f"    tags: {', '.join(entry['tags'])}")
            if entry.get("secrets"):
                lines.append(f"    secrets: {', '.join(entry['secrets'])}")
            return {"kind": "util", "name": "list", "target": target,
                    "listing": "\n".join(lines)}
        return {"kind": "util", "name": "list", "listing": utils_lib.catalog_text(home)}
    if name == "show":  # read a util's SOURCE — write_util's counterpart (repair needs read)
        target = str(args[0]) if args else ""
        source = utils_lib.read_util(home, target) if target and is_slug(target) else None
        if source is None:
            return {"kind": "util", "name": "show", "target": target, "missing": True,
                    "available": [u["name"] for u in utils_lib.list_utils(home)]}
        # Repairing a big util needs the COMPLETE source (D42-A): --full returns everything;
        # --range FIRST LAST pages by 1-based inclusive line numbers.
        flags = [str(a) for a in args[1:]]
        if "--full" in flags:
            return {"kind": "util", "name": "show", "target": target, "source": source,
                    "truncated": False}
        if "--range" in flags:
            i = flags.index("--range")
            try:
                lo, hi = int(flags[i + 1]), int(flags[i + 2])
            except (IndexError, ValueError):
                return {"kind": "util", "name": "show", "target": target,
                        "source": "[bad --range] usage: show <name> --range FIRST LAST "
                                  "(1-based line numbers, inclusive)", "truncated": False}
            src_lines = source.splitlines()
            lo = max(lo, 1)
            window = "\n".join(src_lines[lo - 1:hi])
            return {"kind": "util", "name": "show", "target": target,
                    "source": f"[lines {lo}-{min(hi, len(src_lines))} of {len(src_lines)}]\n"
                              + window,
                    "truncated": lo > 1 or hi < len(src_lines)}
        content, truncated = truncate(source, cap=24_000)
        obs = {"kind": "util", "name": "show", "target": target, "source": content,
               "truncated": truncated}
        if truncated:
            obs["hint"] = (f'the middle is elided — re-run with "args": ["{target}", "--full"] '
                           f'for the complete source, or ["{target}", "--range", "FIRST", '
                           f'"LAST"] for a line window')
        return obs
    if name == "search":  # D52 Phase 3: keyword discovery over the live catalog
        query = " ".join(args).strip()
        if not query:
            return {"kind": "util", "name": "search", "query": "",
                    "listing": 'search needs keywords — e.g. {"kind": "util", "name": '
                               '"search", "args": ["send", "email"]}. The full catalog is '
                               "always in your CAPABILITIES section."}
        return {"kind": "util", "name": "search", "query": query,
                "listing": utils_lib.search_listing(home, query)}
    if not utils_lib.exists(home, name):
        # A schema-valid FIELD SHIFT (`name: "300"`) is not a util and must not mint a Stats row.
        if could_be_util(name):
            ctx.count_util(name, "missing")
        obs = {"kind": "util", "name": name, "missing": True,
               "available": [u["name"] for u in utils_lib.list_utils(home)]}
        # the name may be a ROUTINE-LOCAL script: hand back the exact `script` call, args included
        if script_home(ctx, name) is not None:
            obs.update(script_match=True, args=args)
        return obs
    # A declared secret the routine may not see is WITHHELD from the child env rather than
    # blocking the call — an optional (`?`) one, or a REQUIRED one the user DECLINED (a decline
    # protects the credential, not the util). The observation says what was lost.
    from .secretgate import withheld_for_util, withheld_verb_scoped
    withheld = withheld_for_util(ctx, name)
    # One policy object for the jail AND the dead-root note, so the two cannot disagree.
    policy = sandbox.policy_for_ctx(ctx)
    code, out, err = run_and_book(      # D288: the call AND its spend — see utilspend.py
        ctx, utils_run.run_util, extra_secrets=_extra_secrets(ctx, name), home=home, name=name,
        args=args, timeout=int(action.get("timeout_s") or UTIL_DEFAULT_TIMEOUT_S), policy=policy,
        withhold_secrets=withheld.names, cwd=work_dir(ctx), aborted=ctx.aborted,
        cancelled=cancelled_for_turn(ctx))
    killed = _note_if_killed(ctx, "util", name, code)
    ended = _ended_by_a_person(ctx, code)
    # Per-util reliability telemetry; a call a person ended says nothing about the util.
    if ended is None:
        ctx.count_util(name, "ok" if code == 0
                       else ("usage_error" if code == USAGE_ERROR_EXIT else "error"))
    obs = {"kind": "util", "name": name, "args": args, "exit": code,
           **command_output(ctx, name, out, err, code), **withheld.note(ctx),
           **withheld_verb_scoped(ctx, name, args), **_jail_notes(policy), **killed}
    if ended is not None:
        obs[ended] = True   # not a failure, so no repair route
    elif code != 0:
        # A failed call teaches the correct one — and the repair path, so a broken util is
        # fixed or escalated instead of silently worked around.
        entry = next((u for u in utils_lib.list_utils(home) if u["name"] == name), None)
        if entry and entry.get("usage"):
            obs["usage"] = entry["usage"]
        # With util authoring, fix it in place; without, escalate.
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
        # A missing CONNECTION binding outranks the repair route: ungranted, not broken.
        conn_route = _unbound_connection_request(ctx, name)
        obs["hint"] = (
            conn_route
            + f'call shape: every argument goes in `args` as a JSON array of strings, e.g. '
            f'{{"say": "…", "kind": "util", "name": "{name}", "args": ["<argument>", "--json"]}}. '
            + repair)
    return obs


def do_script(action: dict, ctx: RunContext) -> dict:
    """Run one of the routine's OWN scripts/<name>.py helpers: declared-only secrets (only
    header-declared AND granted names reach the env, transitively over the `calls:` utils),
    the recipe's fs jail, and `gu` on PATH only for a script that declares calls. Same
    truncation + spill as a util call. The loop's secret gate ran before this.
    """
    from .. import scripts
    from ..secrets import load_secrets
    from .secretgate import secret_state, withheld_for_call
    name = str(action.get("name") or "")
    args = [str(a) for a in action.get("args") or []]
    home = script_home(ctx, name)
    if home is None:
        return {"kind": "script", "name": name, "missing": True,
                "available": available_scripts(ctx)}
    if bad := scripts.misdeclared(home, name):
        return {"kind": "script", "name": name, "error":
                f"declaration in the wrong place: {', '.join(bad)} — these are engine header "
                "keys, but this script declares them inside the PEP 723 `# /// script` block, "
                "which the engine never reads (the script would run with NO secrets and NO "
                "network). Move them into the module DOCSTRING as header lines, exactly the "
                "util model — e.g.\n    secrets: FTP_SOURCES\n    net: outbound\n— then rerun."}
    if bad := scripts.call_problems(home, name, ctx.server.libraries_home):
        return {"kind": "script", "name": name, "error":
                "; ".join(bad) + ". A script reaches the util library ONLY through its "
                "docstring `calls:` line: that declaration is what folds each util's "
                "secrets and network into the shared jail, so an undeclared or unknown "
                "sibling would run without them. Fix the header — e.g.\n"
                "    calls: gmail, ftp\n— then rerun."}
    declared, _net, optional = scripts.needs(home, name, ctx.server.libraries_home)
    # Only a GRANTED name is injected; `withheld` reports what was lost.
    withheld = withheld_for_call(ctx, declared, optional)
    env_secrets = {k: v for k, v in load_secrets().items()
                   if k in declared and secret_state(ctx, k) == "granted"}
    env_secrets |= {k: v for k, v in _extra_secrets(ctx).items() if k in declared}
    policy = sandbox.policy_for_ctx(ctx)   # the jail AND the dead-root note (R2260)
    code, out, err = scripts.run_script(
        home, name, args,
        timeout=int(action.get("timeout_s") or scripts.SCRIPT_TIMEOUT_S),
        policy=policy, libraries_home=ctx.server.libraries_home,
        env_secrets=env_secrets, aborted=ctx.aborted,
        cancelled=cancelled_for_turn(ctx), seal_dir=ctx.routine.dir)
    killed = _note_if_killed(ctx, "script", name, code)
    obs = {"kind": "script", "name": name, "args": args, "exit": code,
           **command_output(ctx, f"script-{name}", out, err, code),
           **withheld.note(ctx), **_jail_notes(policy), **killed}
    if ended := _ended_by_a_person(ctx, code):
        obs[ended] = True
    elif code != 0 and (usage := scripts.usage(home, name)):
        obs["usage"] = usage    # its DECLARED call form, as a failed util carries its own
    return obs


def do_shell(action: dict, ctx: RunContext) -> dict:
    """Run ONE ad-hoc command through `bash -c` inside the run's Landlock jail — the escape
    hatch, gated by the `shell` capability. No call-time secret gate: the command is handed NO
    store secret. The jail and empty secret env are `shellrun`'s; capture, timeout and abort
    are the shared runner's (`utils_run.run_jailed`); truncation and the `.util_outputs/`
    spill are the same a util call gets.

    No advisory tail, unlike `util`'s: a non-zero exit here is usually the ANSWER (`grep -q`,
    `test -f`, a failing suite), not a mistake to correct.

    **A shell write is an UNGATED write, deliberately, and the observation says so** (D158).
    Every write protection lives in the FILE-action gate (`fileops._write_gate`); a `bash -c`
    redirect calls none of it — inside the routine's OWN dir the kernel refuses those writes
    regardless (D179, `ownseal`), so what stays ungated is every path OUTSIDE it. Which writes
    a shell line performs is undecidable, so nothing refuses one; instead `ungated_write` marks
    any write-shaped command ("may have written", never "did not"), and the first one per run
    files an `ungated_write` health event (`engine/shellwrite.py`).
    """
    command = str(action.get("command") or "")
    cwd = work_dir(ctx)
    if raw := str(action.get("path") or "").strip():
        candidate = expand(raw)
        cwd = candidate if candidate.is_absolute() else (work_dir(ctx) / candidate)
    policy = sandbox.policy_for_ctx(ctx)   # the jail AND the dead-root note (R2260)
    result = shellrun.run_shell(
        command, policy=policy,
        libraries_home=ctx.server.libraries_home, cwd=cwd,
        timeout=int(action.get("timeout_s") or shellrun.SHELL_DEFAULT_TIMEOUT_S),
        aborted=ctx.aborted, cancelled=cancelled_for_turn(ctx))
    killed = _note_if_killed(ctx, "shell", command[:60], int(result["exit"]))
    obs = {"kind": "shell", "command": command, "exit": result["exit"],
           **command_output(ctx, "shell", result["stdout"], result["stderr"], result["exit"]),
           **_jail_notes(policy), **killed}
    obs["truncated"] = obs["truncated"] or result["truncated"]
    if marks_a_write(command):
        # D158: no gate saw this write, so the record (and one health event) is all there is.
        obs["ungated_write"] = True
        record_ungated_write(ctx, command)
    if str(cwd) != str(work_dir(ctx)):
        obs["cwd"] = str(cwd)
    if result["timed_out"]:
        obs["timed_out"] = True
    if result["aborted"]:
        obs["aborted"] = True
    if result.get("cancelled"):
        obs["cancelled"] = True
    return obs


DISPATCH = {
    "util": do_util,
    "shell": do_shell,
    "read_file": do_read_file,
    "view_image": do_view_image,
    "write_file": do_write_file,
    "delete": do_delete,
    "move": do_move,
    "mkdir": do_mkdir,
    "edit_file": do_edit_file,
    "memory_read": do_memory_read,
    "memory_write": do_memory_write,
    "read_rule": do_read_rule,
    "list_models": lambda _action, ctx: do_list_models(ctx),
}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
