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
from . import outputs, refusal
from .fileops import (
    UTIL_DEFAULT_TIMEOUT_S,
    do_edit_file,
    do_memory_read,
    do_memory_write,
    do_read_file,
    do_read_rule,
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
    from .interact import secret_state, withheld_optional_secrets
    withheld = withheld_optional_secrets(ctx, name)
    code, out, err = utils_lib.run_util(
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
        obs["hint"] = (
            f'call shape: every argument goes in `args` as a JSON array of strings, e.g. '
            f'{{"say": "…", "kind": "util", "name": "{name}", "args": ["<argument>", "--json"]}}. '
            + repair)
    return obs


def do_script(action: dict, ctx: RunContext) -> dict:
    """Run one of the routine's OWN scripts/<name>.py helpers: declared-only secrets
    (the util model — only header-declared AND granted names reach the env, engine
    extras like connection tokens included only if declared), the recipe's fs jail, no
    `gu` on PATH. Same truncation + spill as a util call. The loop's secret gate
    (declared-required-undecided → the blocking ask) ran before this.
    """
    from .. import scripts
    from ..secrets import load_secrets
    from .interact import secret_state
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
    declared, _net, _opt = scripts.needs(ctx.routine.dir, name)
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


def do_llm(action: dict, ctx: RunContext) -> dict:
    messages = []
    if action.get("system"):
        messages.append({"role": "system", "content": action["system"]})
    messages.append({"role": "user", "content": action["prompt"]})
    schema = action.get("response_schema")
    purpose = ("llm · " + str(action.get("say") or "sub-call"))[:80]
    # The optional `model` field: a ROLE (main/tool_call; uncensored → the routine's
    # uncensored model) or a CATALOG model NAME (`list_models` shows them) — default
    # tool_call. An unknown value is a teaching error naming the catalog (D81 extended,
    # 2026-08-22).
    role = str(action.get("model") or "tool_call")
    try:
        if role == "uncensored":
            target = ctx.registry.for_uncensored(ctx.routine.models)
            if target is None:
                return {"kind": "llm",
                        "error": "model role 'uncensored' is not configured for this routine "
                                 "— it needs a models.uncensored catalog entry (routine page "
                                 "→ Models). Use the default role, or ask the user to set one."}
            endpoint, ref = target
        elif role in ("main", "tool_call"):
            endpoint, ref = ctx.registry.for_model(role, ctx.routine.models)
        elif role in ctx.server.models:
            endpoint, ref = ctx.registry.for_name(role)
        else:
            avail = ", ".join(sorted(ctx.server.models)) or "none configured"
            return {"kind": "llm",
                    "error": f"model {role!r} is neither a role (main/tool_call/uncensored) "
                             f"nor a catalog model name. Catalog models: {avail}. The "
                             "list_models action shows each one's endpoint and attributes."}
        completion = endpoint.complete(messages, model=ref.model, schema=schema,
                                       effort=ref.effort, temperature=ref.temperature,
                                       max_tokens=ref.max_tokens, purpose=purpose,
                                       kind="llm_action")
    except EndpointError as exc:
        return {"kind": "llm", "error": str(exc)}
    ctx.add_usage(completion.usage)

    reply = completion.text
    if completion.parsed is not None:
        reply = json.dumps(completion.parsed, ensure_ascii=False, indent=1)
    reply, truncated = truncate(reply)
    out = {"kind": "llm", "endpoint": ref.endpoint, "model": ref.model,
           "reply": reply, "usage": completion.usage, "truncated": truncated}
    # Refusal clarification (engine/refusal.py): a free-text reply that a classification
    # subcall (markers only fast-path CONFIRM) judges a content refusal is FLAGGED, its
    # trigger isolated, and ONLY the isolated essence delivered to the uncensored model
    # as a normal call (operator, 2026-08-22: authentic environment, dummy responses
    # managed in the background). Everything ELSE goes back to the PRIMARY model with
    # the essence factored out — "without danger of refusal" — and that answer serves
    # the observation; the refusal record rides beside it. A schema'd reply is an answer
    # by construction, and an explicit `model: uncensored` call is the caller's own
    # probe — neither is clarified.
    if role != "uncensored" and completion.parsed is None \
            and refusal.is_refusal(ctx, completion.text):
        record = refusal.clarify_refusal(
            ctx, task=str(action.get("prompt") or ""), refusal=completion.text,
            where="llm", model=ref.name or ref.model)
        out["refusal"] = record
        essence = record.get("isolated")
        if essence and essence in str(action.get("prompt") or ""):
            sanitized = str(action.get("prompt") or "").replace(
                essence, "[this part is handled separately]")
            try:
                remainder = endpoint.complete(
                    [*messages[:-1], {"role": "user", "content": sanitized}],
                    model=ref.model, schema=schema, effort=ref.effort,
                    temperature=ref.temperature, max_tokens=ref.max_tokens,
                    purpose=(purpose + " · remainder")[:80], kind="llm_action")
            except EndpointError:
                remainder = None
            if remainder is not None and (remainder.text or remainder.parsed is not None):
                ctx.add_usage(remainder.usage)
                r2 = (remainder.text if remainder.parsed is None
                      else json.dumps(remainder.parsed, ensure_ascii=False, indent=1))
                out["reply"], out["truncated"] = truncate(r2)
                out["remainder_processed"] = True
    return out


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


def do_list_models(ctx: RunContext) -> dict:
    """The model DISCOVERY surface (paired with the per-call `model` override,
    2026-08-22): what this run's role bindings resolve to right now, plus every catalog
    model a `model` field may name. Read-only — config stays the user's. A catalog row
    that fails to resolve surfaces as its own error line instead of vanishing
    (failure-visibility).
    """
    roles: dict = {}
    for role in ("main", "tool_call"):
        try:
            _, ref = ctx.registry.for_model(role, ctx.routine.models)
            roles[role] = {"catalog": ref.name, "endpoint": ref.endpoint, "model": ref.model}
        except EndpointError as exc:
            roles[role] = {"error": str(exc)}
    unc = ctx.registry.for_uncensored(ctx.routine.models)
    roles["uncensored"] = ({"catalog": unc[1].name, "endpoint": unc[1].endpoint,
                            "model": unc[1].model} if unc else None)
    models = []
    for name in sorted(ctx.server.models):
        try:
            _, ref = ctx.registry.resolve(name)
            models.append({"name": name, "endpoint": ref.endpoint, "model": ref.model,
                           "multimodal": ref.multimodal, "context_chars": ref.context_chars,
                           "effort": ref.effort,
                           "fallbacks": list(ctx.server.models[name].fallbacks)})
        except EndpointError as exc:
            models.append({"name": name, "error": str(exc)})
    return {"kind": "list_models", "roles": roles, "models": models,
            "note": ("a spawn/subtask/llm action's `model` field takes one of these "
                     "catalog names, or a role (main/tool_call/uncensored); children "
                     "default to main, llm to tool_call")}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
