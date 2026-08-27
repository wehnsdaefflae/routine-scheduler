"""Dispatch a validated action to its effect and return the observation dict.

DISPATCH covers util / read_file / view_image / write_file / edit_file / memory_read /
memory_write / read_rule / llm / list_models; `script` lives here too and is called
directly from loop.py. Control-flow kinds (spawn, subruns, kill, wait, finish) live in
loop.py — they change the run's state machine — and the user-facing kinds (ask_user,
write_util, write_rule) in interact.py / authoring.py. Every observation dict feeds
both the transcript event and (via observations.format_observation) the next user message.
"""

from __future__ import annotations

import logging

from .. import sandbox, utils_lib, utils_run
from ..ids import is_slug
from ..utils_lib import USAGE_ERROR_EXIT
from . import outputs
from .exec_env import _extra_secrets, _unbound_connection_request
from .fileops import UTIL_DEFAULT_TIMEOUT_S, do_edit_file, do_read_file, do_write_file
from .llmaction import do_list_models, do_llm
from .mediaops import do_view_image
from .memops import do_memory_read, do_memory_write, do_read_rule
from .observations import truncate
from .run_context import RunContext

log = logging.getLogger("rsched.engine")

READ_DEFAULT_MAX_LINES = 200
# argparse exits 2 on bad arguments — the deterministic "called with wrong syntax" signal
# for per-util telemetry (a util not using argparse may exit 1 for everything; then its
# usage errors count as plain errors, which is the honest fallback).


def do_util(action: dict, ctx: RunContext) -> dict:  # noqa: PLR0911 — list/show dispatch, many small exits
    name = action["name"]
    args = [str(a) for a in (action.get("args") or [])]
    home = ctx.server.libraries_home
    if name == "list":  # discovery: `gu list` — the catalog is derived live, never stale
        # With a util name in args, list ONLY that util's entry (usage + tags + secrets):
        # the full catalog is already in CAPABILITIES, so re-listing everything to learn
        # one util's flags re-buys ~3k tokens of known information.
        target = str(args[0]).lstrip("-") if args else ""
        if target and target != "all":
            entry = next((u for u in utils_lib.list_utils(home) if u["name"] == target), None)
            if entry is None:
                return {"kind": "util", "name": "list", "target": target, "missing": True,
                        "available": [u["name"] for u in utils_lib.list_utils(home)]}
            lines = [f"- {entry['name']} — {entry['summary']}"]
            if entry.get("usage"):
                lines.append(f"    {entry['usage']}")
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
        # D42-A: repairing a big util needs the COMPLETE source — the default head+tail cap
        # made >24k utils unfixable for shell-less routines. --full returns everything;
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
        ctx.count_util(name, "missing")
        obs = {"kind": "util", "name": name, "missing": True,
               "available": [u["name"] for u in utils_lib.list_utils(home)]}
        # R367: the name may be a ROUTINE-LOCAL script, which the util action never
        # resolves — say so, or the caller (told "scripts/ is the place for private
        # helpers") has no path from this miss to actually running the file.
        from .. import scripts
        if scripts.exists(ctx.routine.dir, name):
            obs["script_match"] = True
        return obs
    # F290: optional (`?`-declared) secrets the routine may not see are WITHHELD from the
    # child env instead of blocking the call with an exposure ask — a public call runs
    # prompt-free; the observation names the withheld undecided ones so an auth-needing
    # call learns to request exposure explicitly (denied ones stay unenumerated, R17).
    from .secretgate import secret_state, withheld_optional_secrets
    withheld = withheld_optional_secrets(ctx, name)
    code, out, err = utils_run.run_util(
        home, name, args, timeout=int(action.get("timeout_s") or UTIL_DEFAULT_TIMEOUT_S),
        policy=sandbox.policy_for_ctx(ctx),
        extra_secrets=_extra_secrets(ctx), withhold_secrets=set(withheld),
        cwd=ctx.routine.dir)
    # Per-util reliability telemetry (util_stats → the Stats tab).
    ctx.count_util(name, "ok" if code == 0
                   else ("usage_error" if code == USAGE_ERROR_EXIT else "error"))
    # STDOUT is ordered and spilled in full to .util_outputs/, so tail-truncate (keep the
    # head, drop the tail): the reader continues IN SEQUENCE from the spill file at the char
    # the preview stopped, instead of losing the middle (operator AUDIT note R45).
    stdout, trunc_out = truncate(out, keep="head")
    # On failure, stderr is the repair material — keep the whole trace where possible
    # (head+tail preserves the exception at the traceback's END, the part that teaches).
    stderr, trunc_err = truncate(err, cap=8000 if code != 0 else 2000)
    obs = {"kind": "util", "name": name, "args": args, "exit": code,
           "stdout": stdout, "stderr": stderr, "truncated": trunc_out or trunc_err}
    if withheld:
        # undecided names are requestable and may be enumerated; denied ones are a count
        # only (R17 — a denial enumerates nothing)
        undecided = [s for s in withheld if secret_state(ctx, s) == "undecided"]
        n_denied = len(withheld) - len(undecided)
        obs["withheld_optional"] = {"undecided": undecided, "denied": n_denied}
    # What the observation could not carry is spilled to .util_outputs/ rather than lost:
    # the transcript records THIS (truncated) payload, so the band between the capture cap
    # and the observation cap has no other survivor. Only truncated output is kept.
    if spilled := outputs.spill(ctx, name, out, err,
                                out_truncated=trunc_out, err_truncated=trunc_err):
        obs["full_output"] = spilled
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
        # A missing CONNECTION binding outranks the generic repair route: the call is not
        # broken, it is ungranted, and the fix is one typed request (F321).
        conn_route = _unbound_connection_request(ctx, name)
        obs["hint"] = (
            conn_route
            + f'call shape: every argument goes in `args` as a JSON array of strings, e.g. '
            f'{{"say": "…", "kind": "util", "name": "{name}", "args": ["<argument>", "--json"]}}. '
            + repair)
    return obs


def do_script(action: dict, ctx: RunContext) -> dict:
    """Run one of the routine's OWN scripts/<name>.py helpers: declared-only secrets
    (the util model — only header-declared AND granted names reach the env, engine
    extras like connection tokens included only if declared, resolved transitively over
    the utils the script's `calls:` line declares), the recipe's fs jail, and `gu` on
    PATH only for a script that declares those calls. Same truncation + spill as a util
    call. The loop's secret gate
    (declared-required-undecided → the blocking ask) ran before this.
    """
    from .. import scripts
    from ..secrets import load_secrets
    from .secretgate import secret_state
    name = str(action.get("name") or "")
    args = [str(a) for a in action.get("args") or []]
    if not scripts.exists(ctx.routine.dir, name):
        return {"kind": "script", "name": name, "missing": True,
                "available": [s["name"] for s in scripts.list_scripts(ctx.routine.dir)]}
    if bad := scripts.misdeclared(ctx.routine.dir, name):
        return {"kind": "script", "name": name, "error":
                f"declaration in the wrong place: {', '.join(bad)} — these are engine header "
                "keys, but this script declares them inside the PEP 723 `# /// script` block, "
                "which the engine never reads (the script would run with NO secrets and NO "
                "network). Move them into the module DOCSTRING as header lines, exactly the "
                "util model — e.g.\n    secrets: FTP_SOURCES\n    net: outbound\n— then rerun."}
    if bad := scripts.call_problems(ctx.routine.dir, name, ctx.server.libraries_home):
        return {"kind": "script", "name": name, "error":
                "; ".join(bad) + ". A script reaches the util library ONLY through its "
                "docstring `calls:` line: that declaration is what folds each util's "
                "secrets and network into the shared jail, so an undeclared or unknown "
                "sibling would run without them. Fix the header — e.g.\n"
                "    calls: gmail, ftp\n— then rerun."}
    declared, _net, _opt = scripts.needs(ctx.routine.dir, name,
                                         ctx.server.libraries_home)
    env_secrets = {k: v for k, v in load_secrets().items()
                   if k in declared and secret_state(ctx, k) == "granted"}
    env_secrets |= {k: v for k, v in _extra_secrets(ctx).items() if k in declared}
    code, out, err = scripts.run_script(
        ctx.routine.dir, name, args,
        timeout=int(action.get("timeout_s") or scripts.SCRIPT_TIMEOUT_S),
        policy=sandbox.policy_for_ctx(ctx), libraries_home=ctx.server.libraries_home,
        env_secrets=env_secrets)
    stdout, trunc_out = truncate(out, keep="head")
    stderr, trunc_err = truncate(err, cap=8000 if code != 0 else 2000)
    obs = {"kind": "script", "name": name, "args": args, "exit": code,
           "stdout": stdout, "stderr": stderr, "truncated": trunc_out or trunc_err}
    if spilled := outputs.spill(ctx, f"script-{name}", out, err,
                                out_truncated=trunc_out, err_truncated=trunc_err):
        obs["full_output"] = spilled
    return obs


DISPATCH = {
    "util": do_util,
    "read_file": do_read_file,
    "view_image": do_view_image,
    "write_file": do_write_file,
    "edit_file": do_edit_file,
    "memory_read": do_memory_read,
    "memory_write": do_memory_write,
    "read_rule": do_read_rule,
    "llm": do_llm,
    "list_models": lambda _action, ctx: do_list_models(ctx),
}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
