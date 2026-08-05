"""Dispatch a validated action to its effect and return the observation dict.

Handles util / read_file / write_file / edit_file / memory_read / memory_write / llm here.
Control-flow kinds (spawn, subruns, kill, wait, finish) live in loop.py — they change the
run's state machine — and the user-facing kinds (ask_user, write_util) in interact.py.
Every observation dict feeds both the transcript event and (via
composer.format_observation) the next user message.
"""

from __future__ import annotations

import json
import logging

from .. import machines, sandbox, utils_lib
from ..endpoints.base import EndpointError
from ..ids import is_slug
from ..oauth import store as oauth_store
from ..utils_lib import USAGE_ERROR_EXIT
from . import outputs
from .fileops import (
    UTIL_DEFAULT_TIMEOUT_S,
    do_edit_file,
    do_memory_read,
    do_memory_write,
    do_read_file,
    do_read_trait,
    do_view_image,
    do_write_file,
)
from .observations import truncate
from .run_context import RunContext

log = logging.getLogger("rsched.engine")

READ_DEFAULT_MAX_LINES = 200
# argparse exits 2 on bad arguments — the deterministic "called with wrong syntax" signal
# for per-util telemetry (a util not using argparse may exit 1 for everything; then its
# usage errors count as plain errors, which is the honest fallback).


def _connection_env(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE OAuth connections resolved to {<PROVIDER>_ACCESS_TOKEN: token},
    passed to run_util as extra_secrets: the config bindings plus this run's one-time
    connection grants (the decision recorded the account in ctx.grant_args). A util only
    sees a token it declares AND the run holds; a missing / needs-reauth binding is simply
    absent (the util then fails for want of a token).
    """
    bound = dict(ctx.routine.connections or {})
    for eid in sorted(ctx.granted_now):
        if eid.startswith("connection:"):
            provider = eid.partition(":")[2]
            bound.setdefault(provider, str(ctx.grant_args.get(eid) or ""))
    if not bound:
        return {}
    env, warnings = oauth_store.tokens_for_routine(bound)
    for w in warnings:                       # a broken binding must not fail SILENTLY
        log.warning("connections: %s", w)
    return env


def _machine_env(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE remote machines (config bindings + one-time machine grants)
    resolved to RSCHED_MACHINES (connection metadata) + RSCHED_MACHINE_KEYS (private-key
    PEMs from the Secrets store), passed to run_util as extra_secrets. Only the reserved
    `remote` util declares these, so only it receives them; an unresolvable binding
    (missing catalog entry / unset key) is simply absent from the maps. A one-time grant
    covers EXEC only — the sshfs share is mounted by the daemon at binding time, so
    mounts come with forever-bindings.
    """
    bound = list(ctx.routine.machines or [])
    bound += [eid.partition(":")[2] for eid in sorted(ctx.granted_now)
              if eid.startswith("machine:") and eid.partition(":")[2] not in bound]
    if not bound:
        return {}
    env, warnings = machines.machines_for_routine(bound, ctx.server.machines)
    for w in warnings:                       # a broken binding must not fail SILENTLY
        log.warning("machines: %s", w)
    return env


def _extra_secrets(ctx: RunContext) -> dict[str, str]:
    """Engine-resolved, per-run secrets a util may receive (still under the declared-only gate):
    OAuth connection access tokens + bound remote-machine details/keys. The var names are
    disjoint, so a plain merge is safe.

    RSCHED_API_TOKEN (R94, operator decision 2026-08-05: ENFORCE): the reserved name a
    util declares to talk to the daemon API resolves to the server's ROUTINE token — the
    read-only tier — and OVERRIDES any secrets-store value for it (extra_secrets win the
    _child_env merge by design), so the primary console token can never reach a util
    subprocess through the store. Config stays honest: the engine reads `routine_token`
    here, it never writes it (bootstrap.ensure_config generates it).
    """
    out = {**_connection_env(ctx), **_machine_env(ctx)}
    routine_token = str(getattr(ctx.server, "routine_token", "") or "")
    if routine_token:
        out["RSCHED_API_TOKEN"] = routine_token
    return out


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
        return {"kind": "util", "name": name, "missing": True,
                "available": [u["name"] for u in utils_lib.list_utils(home)]}
    code, out, err = utils_lib.run_util(
        home, name, args, timeout=int(action.get("timeout_s") or UTIL_DEFAULT_TIMEOUT_S),
        policy=sandbox.policy_for_ctx(ctx),
        extra_secrets=_extra_secrets(ctx), cwd=ctx.routine.dir)
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
        obs["hint"] = (
            f'call shape: every argument goes in `args` as a JSON array of strings, e.g. '
            f'{{"say": "…", "kind": "util", "name": "{name}", "args": ["<argument>", "--json"]}}. '
            + repair)
    return obs


_REFUSAL_MARKERS = (
    "i can't help with that", "i cannot help with that",
    "i can't assist with that", "i cannot assist with that",
    "i can't help you with that", "i cannot help you with that",
    "i'm unable to help with that", "i am unable to help with that",
    "i'm not able to help with that", "i am not able to help with that",
    "i can't provide", "i cannot provide",
    "i can't comply with", "i cannot comply with",
    "i can't fulfill", "i cannot fulfill", "i can't fulfil", "i cannot fulfil",
    "i can't create", "i cannot create",
    "i'm sorry, but i can't", "i'm sorry, but i cannot",
    "i'm sorry, i can't", "i'm sorry, i cannot",
    "i won't be able to help with that", "i must decline",
    "it goes against my guidelines", "against my programming",
)


def _looks_like_refusal(text: str) -> bool:
    """Heuristic: does a free-text tool-call reply read as a content refusal rather than an
    answer? Conservative on purpose — only a marker in the reply's HEAD (first ~200 chars)
    counts, since real refusals open with the decline; this trades recall for precision so we
    don't reroute genuine answers to the uncensored model.
    """
    head = (text or "").strip().lower()[:200]
    return bool(head) and any(m in head for m in _REFUSAL_MARKERS)


def do_llm(action: dict, ctx: RunContext) -> dict:
    messages = []
    if action.get("system"):
        messages.append({"role": "system", "content": action["system"]})
    messages.append({"role": "user", "content": action["prompt"]})
    schema = action.get("response_schema")
    purpose = ("llm · " + str(action.get("say") or "sub-call"))[:80]
    try:
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        completion = endpoint.complete(messages, model=ref.model, schema=schema,
                                       effort=ref.effort, temperature=ref.temperature,
                                       max_tokens=ref.max_tokens, purpose=purpose,
                                       kind="llm_action")
    except EndpointError as exc:
        return {"kind": "llm", "error": str(exc)}
    ctx.add_usage(completion.usage)

    # Refusal referral (opt-in): the tool-call model declined a free-text request and the
    # routine configured an `uncensored` model — re-issue the SAME prompt to it. Only
    # free-text replies (parsed is None) are considered; a schema'd/structured reply is an
    # answer, not a refusal. Referral is silent for routines that leave the role unset.
    endpoint_name, model_name, referred = ref.endpoint, ref.model, False
    if completion.parsed is None and _looks_like_refusal(completion.text):
        target = ctx.registry.for_uncensored(ctx.routine.models)
        if target is not None:
            u_endpoint, u_ref = target
            try:
                u_completion = u_endpoint.complete(messages, model=u_ref.model, schema=schema,
                                                   effort=u_ref.effort,
                                                   temperature=u_ref.temperature,
                                                   max_tokens=u_ref.max_tokens,
                                                   purpose=(purpose + " · referred")[:80],
                                                   kind="llm_action")
            except EndpointError:
                u_completion = None
            if u_completion is not None:
                ctx.add_usage(u_completion.usage)
                completion, referred = u_completion, True
                ctx.referrals += 1
                endpoint_name, model_name = u_ref.endpoint, u_ref.model

    reply = completion.text
    if completion.parsed is not None:
        reply = json.dumps(completion.parsed, ensure_ascii=False, indent=1)
    reply, truncated = truncate(reply)
    out = {"kind": "llm", "endpoint": endpoint_name, "model": model_name,
           "reply": reply, "usage": completion.usage, "truncated": truncated}
    if referred:
        out["referred"] = True
    return out


DISPATCH = {
    "util": do_util,
    "read_file": do_read_file,
    "view_image": do_view_image,
    "write_file": do_write_file,
    "edit_file": do_edit_file,
    "memory_read": do_memory_read,
    "memory_write": do_memory_write,
    "read_trait": do_read_trait,
    "llm": do_llm,
}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
