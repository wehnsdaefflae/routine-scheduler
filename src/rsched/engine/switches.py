"""Turn-boundary SWITCHES — what the user changed while the run was already going.

Split out of `control.py` (F393). One discipline, four signals (model, deliberation, bound
rules, config): the WEB layer writes `control.json`, the engine only ever reads it, and an
applied-ts ledger stops a resumed leg re-firing a stale signal. The composed prompt is
immutable under the caching contract, so a switch reaches the model as an APPENDED engine note
rather than a rewrite — which is also why the run can see, in its own transcript, that the
ground moved under it.
"""

from __future__ import annotations

import logging

from ..config import DELIBERATION_LEVELS
from ..paths import read_json
from . import deliberation

log = logging.getLogger("rsched.control")



def _applied_path(loop):
    return loop.ctx.root_run_dir / "control-applied.json"

def load_applied_baselines(loop) -> None:
    """Seed the mid-run-switch edge-triggers from the run's applied ledger. control.json is
    web-owned (the engine never writes it), so a consumed signal can't be cleared there —
    without this ledger every RESUME leg would re-fire the run's stale switch_model /
    set_deliberation / add_rules signals (re-pinning models the user has since changed
    back, and re-injecting the same engine notes every leg).
    """
    applied = read_json(_applied_path(loop))
    if isinstance(applied, dict):
        loop._last_switch_ts = str(applied.get("switch_model") or "")
        loop._last_deliberation_ts = str(applied.get("set_deliberation") or "")
        loop._last_rules_ts = str(applied.get("add_rules") or "")
        loop._last_config_ts = str(applied.get("config_change") or "")

def _mark_applied(loop, signal: str, ts: str) -> None:
    from ..paths import atomic_write_json

    applied = read_json(_applied_path(loop))
    applied = applied if isinstance(applied, dict) else {}
    applied[signal] = ts
    atomic_write_json(_applied_path(loop), applied)

def apply_model_switch(loop) -> None:
    """Turn-boundary: honour a mid-run model switch written to control.json by the web layer.
    Edge-triggered on the signal's `ts` so the engine never has to write control.json (which
    stays web-owned). The switch lands on the NEXT completion, since for_model re-resolves
    ctx.routine.models every turn — the model, its context size, and effort all self-correct.
    """
    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("switch_model") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_switch_ts:
        return
    loop._last_switch_ts = str(sw["ts"])
    _mark_applied(loop, "switch_model", str(sw["ts"]))
    applied = []
    for kind in ("main", "tool_call", "uncensored"):
        name = sw.get(kind)   # a catalog model NAME; roles re-resolve every turn via for_model
        if isinstance(name, str) and name in ctx.server.models:
            ctx.routine.models[kind] = name
            applied.append(f"{kind} → {name}")
    if applied:
        note = "model switched mid-run: " + "; ".join(applied)
        ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
        loop.messages.append({"role": "user", "content":
            f"ENGINE NOTE: {note}. Continue the run on the new model."})

def apply_deliberation_switch(loop) -> None:
    """Turn-boundary: honour a mid-run deliberation switch written to control.json by the
    web layer. Same edge-trigger discipline as apply_model_switch — the engine never
    writes control.json. The composed prompt is immutable (prompt-caching contract), so
    the new say contract reaches the model as an appended engine note instead.
    """
    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("set_deliberation") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_deliberation_ts:
        return
    loop._last_deliberation_ts = str(sw["ts"])
    _mark_applied(loop, "set_deliberation", str(sw["ts"]))
    level = sw.get("level")
    if level not in DELIBERATION_LEVELS or level == ctx.deliberation:
        return
    note = deliberation.switch_note(ctx.deliberation, level)
    ctx.deliberation = level
    ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
    loop.messages.append({"role": "user", "content": f"ENGINE NOTE: {note}"})

def apply_config_change(loop) -> None:
    """Turn-boundary: a config PATCH made while this run is LIVE (F337).

    Two halves, one signal. The fields `configflow` classes as LIVE are ADOPTED into the run
    context here; every other patched field is named as taking effect at the next run. Naming
    both is the point — the complaint F337 records is not that some fields wait, it is that the
    run was never told WHICH did, so "I changed it while it was running" meant two different
    things depending on the field.

    Same edge-trigger discipline as the model/deliberation/rule switches: the web layer writes
    control.json, the engine only reads it, and the applied-ts ledger stops a resumed leg
    re-firing a stale signal. The composed prompt is immutable (the prompt-caching contract), so
    the change reaches the model as an appended engine note rather than a rewrite.
    """
    from ..configflow import ADOPTABLE, change_note

    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("config_change") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_config_ts:
        return
    loop._last_config_ts = str(sw["ts"])
    _mark_applied(loop, "config_change", str(sw["ts"]))
    fields = [str(f) for f in (sw.get("fields") or [])]
    raw_values = sw.get("values")
    values: dict = raw_values if isinstance(raw_values, dict) else {}
    if not fields:
        return
    for field in fields:
        if field in ADOPTABLE:
            _adopt(loop, field, values.get(field))
    if note := change_note(fields, values):
        ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
        loop.messages.append({"role": "user", "content": f"ENGINE NOTE: {note}"})

def _adopt(loop, field: str, value: object) -> None:
    """Apply ONE live-classified field to the running context. Best-effort per field: a value
    the run cannot use must not cost the run — the note still tells the model it changed, and a
    bad value is the operator's to see on the page, not a crash mid-run.
    """
    ctx = loop.ctx
    try:
        if field == "deliberation" and isinstance(value, str):
            if value in DELIBERATION_LEVELS and value != ctx.deliberation:
                ctx.deliberation = value
        elif field == "budgets" and isinstance(value, dict):
            # `ctx.budgets` carries the CONFIG's own field names (max_turns, …) and the
            # ledger is derived from it per check, so setting the fields is enough — the
            # next boundary's violation/warning test reads the new ceilings. Unknown keys
            # are ignored rather than raising: a stray key must not end a live run.
            for name, limit in value.items():
                if hasattr(ctx.budgets, str(name)) and isinstance(limit, int):
                    setattr(ctx.budgets, str(name), limit)
        elif field == "grants" and isinstance(value, dict):
            from ..policyload import load_policy

            ctx.routine.grants = value
            loop.base_grants = load_policy(
                ctx.server.permissions_home, ctx.routine.permissions, ctx.routine.capabilities,
                current_run_ts=ctx.run_ts, grants_map=value)
            from .requests import rebuild_policy
            rebuild_policy(loop)
    except (AttributeError, TypeError, ValueError, OSError) as exc:
        log.warning("config_change: could not adopt %r live (%s) — it lands at the next run",
                    field, exc)

def apply_rule_additions(loop) -> None:
    """Turn-boundary: honour general rules the USER bound to a LIVE run from the web layer.

    Same edge-trigger discipline as the model/deliberation switches — the engine never writes
    control.json. Recording the slug in routine.yaml is the web layer's job (rules.py); what
    cannot wait is the prose reaching the model, and the composed prompt is immutable
    (prompt-caching contract), so each added rule arrives as an appended engine note read
    straight from the library. From the next run it is an ordinary standing practice.
    """
    from .. import library_docs

    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("add_rules") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_rules_ts:
        return
    loop._last_rules_ts = str(sw["ts"])
    _mark_applied(loop, "add_rules", str(sw["ts"]))
    for slug in sw.get("slugs") or []:
        if not isinstance(slug, str) or slug in ctx.consulted_rules:
            continue
        raw = library_docs.read_doc(ctx.server.rules_home, slug)
        if raw is None:
            continue
        ctx.consulted_rules.add(slug)
        text = library_docs.doc_body(raw).strip()
        note = (f"the user bound the general rule {slug!r} to this routine — it applies from "
                f"now on, and is one of your standing practices from the next run:\n\n{text}")
        ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
        loop.messages.append({"role": "user", "content": f"ENGINE NOTE: {note}"})
