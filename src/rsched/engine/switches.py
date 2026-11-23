"""Turn-boundary SWITCHES — what the user changed while the run was already going.

One discipline for every signal (model, deliberation, bound/unbound rule, config change,
supervise-now): the WEB layer writes `control.json`, the engine only reads it, and an applied-ts
ledger stops a resumed leg re-firing a stale signal. The composed prompt is immutable under the
caching contract, so a switch reaches the model as an APPENDED engine note.
"""

from __future__ import annotations

import logging

from .. import rootmodes
from ..config import DELIBERATION_LEVELS, EFFORT_LEVELS
from ..paths import atomic_write_json, read_json
from . import assist, deliberation, enginenote, rootcheck

log = logging.getLogger("rsched.control")


def _applied_path(loop):
    return loop.ctx.root_run_dir / "control-applied.json"


def load_applied_baselines(loop) -> None:
    """Seed the mid-run-switch edge-triggers from the run's applied ledger. control.json is
    web-owned, so a consumed signal can't be cleared there; without this ledger every RESUME
    leg would re-fire stale signals.
    """
    applied = read_json(_applied_path(loop))
    if isinstance(applied, dict):
        loop._last_switch_ts = str(applied.get("switch_model") or "")
        loop._last_deliberation_ts = str(applied.get("set_deliberation") or "")
        loop._last_rules_ts = str(applied.get("add_rules") or "")
        loop._last_rule_drop_ts = str(applied.get("drop_rules") or "")
        loop._last_config_ts = str(applied.get("config_change") or "")


def _fresh(loop, signal: str, last_ts: str) -> dict | None:
    """The payload of `signal` when control.json carries one other than `last_ts` (the `ts`
    this run last applied), recorded in the applied ledger; else None. The caller advances its
    own `last_ts` from the payload.
    """
    obj = read_json(loop.ctx.root_run_dir / "control.json")
    sw = obj.get(signal) if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or str(sw["ts"]) == last_ts:
        return None
    applied = read_json(_applied_path(loop))
    applied = applied if isinstance(applied, dict) else {}
    applied[signal] = str(sw["ts"])
    atomic_write_json(_applied_path(loop), applied)
    return sw


#: What `ladder.rung_is_due` reports when a PERSON asked for this rung — distinct from the
#: `ladder.PULL_FORWARD` events, so a reader can tell a requested rung from an automatic one.
REQUESTED_BY_PERSON = "person_requested"


def person_asked(loop) -> str | None:
    """`REQUESTED_BY_PERSON` when a person has asked THIS run for a supervisory rung since the
    last ask it honoured, else None — the sixth signal, and the ask is consumed by the read.

    A REQUEST ON THE RUN rather than a second run of the routine (one run per routine; the
    ladder supervises a live run in-process as an OVERSIGHT child, `ladder.escalate`). It needs
    no knob: the ask IS the authorization, so `ladder.at_boundary` reads it before
    `ladder.enabled`. Consuming it on read is safe because every path out of `escalate` writes
    an event; it must never become a MODE that rungs every boundary.

    `_last_supervise_ts` is read via `getattr` so a minimal test loop stand-in still works.
    """
    seen = str(getattr(loop, "_last_supervise_ts", "") or "")
    if (ask := _fresh(loop, "supervise_now", seen)) is None:
        return None
    loop._last_supervise_ts = str(ask["ts"])
    return REQUESTED_BY_PERSON


def apply_model_switch(loop) -> None:
    """Turn-boundary: honour a mid-run model switch written to control.json by the web layer.
    The switch lands on the NEXT completion, since for_model re-resolves ctx.routine.models
    and ctx.routine.efforts every turn — the model, its context size, and effort all
    self-correct. The model and the effort of a role are switched independently.
    """
    ctx = loop.ctx
    if (sw := _fresh(loop, "switch_model", loop._last_switch_ts)) is None:
        return
    loop._last_switch_ts = str(sw["ts"])
    applied = []
    for kind in ("main", "tool_call", "uncensored"):
        name = sw.get(kind)   # a catalog model NAME; roles re-resolve every turn via for_model
        if isinstance(name, str) and name in ctx.server.models:
            ctx.routine.models[kind] = name
            applied.append(f"{kind} → {name}")
        level = sw.get(f"{kind}_effort")   # a reasoning level, switched on its own or with a model
        if level in EFFORT_LEVELS:
            ctx.routine.efforts[kind] = level
            applied.append(f"{kind} effort → {level}")
    if applied:
        enginenote.append(loop, "model switched mid-run: " + "; ".join(applied)
                          + ". Continue the run on the new model.")


def apply_deliberation_switch(loop) -> None:
    """Turn-boundary: honour a mid-run deliberation switch written to control.json by the
    web layer. The composed prompt is immutable (prompt-caching contract), so the new say
    contract reaches the model as an appended engine note instead.
    """
    ctx = loop.ctx
    if (sw := _fresh(loop, "set_deliberation", loop._last_deliberation_ts)) is None:
        return
    loop._last_deliberation_ts = str(sw["ts"])
    level = sw.get("level")
    if level not in DELIBERATION_LEVELS or level == ctx.deliberation:
        return
    note = deliberation.switch_note(ctx.deliberation, level)
    ctx.deliberation = level
    enginenote.append(loop, note)


def apply_config_change(loop) -> None:
    """Turn-boundary: a config PATCH made while this run is LIVE (F337).

    Fields `configflow` classes as LIVE are ADOPTED into the run context; every other patched
    field is named as taking effect at the next run. The note names both halves, so the run
    knows WHICH changes reached it.
    """
    from ..configflow import ADOPTABLE, change_note

    if (sw := _fresh(loop, "config_change", loop._last_config_ts)) is None:
        return
    loop._last_config_ts = str(sw["ts"])
    fields = [str(f) for f in (sw.get("fields") or [])]
    raw_values = sw.get("values")
    values: dict = raw_values if isinstance(raw_values, dict) else {}
    if not fields:
        return
    for field in fields:
        if field in ADOPTABLE:
            _adopt(loop, field, values.get(field))
    if note := change_note(fields, values):
        enginenote.append(loop, note)
    if "budgets" in fields:
        _withdraw_reserve(loop)


def _withdraw_reserve(loop) -> None:
    """A RAISE takes back the reserved finish turn — D244 (review item E1.4).

    Right after `budgets` is adopted the violation is re-asked (the loop's own check ran
    before the boundary). If nothing is over its limit the reserve is given back: flag cleared,
    FULL action schema restored, and an engine note saying so. A raise too small to clear the
    violation changes nothing.
    """
    if not getattr(loop, "_finish_reserved", False) or loop.ctx.budget_spent():
        return
    from .kindsurface import effective_kinds, schema_for_kinds

    loop._finish_reserved = False
    loop._budget_spent = None
    loop.action_schema = schema_for_kinds(effective_kinds(loop.allowed_tools, loop.grants))
    enginenote.append(loop, "the budget you were over has been RAISED, so the reserved finish "
                            "turn is withdrawn: this is no longer your last turn and every "
                            "action kind is available again. Carry on with the work.")


def _adopt(loop, field: str, value: object) -> None:
    """Apply ONE live-classified field to the running context. Best-effort per field: a bad
    value is logged, never a crash mid-run.
    """
    ctx = loop.ctx
    try:
        if field == "deliberation" and isinstance(value, str):
            if value in DELIBERATION_LEVELS and value != ctx.deliberation:
                ctx.deliberation = value
        elif field == "budgets" and isinstance(value, dict):
            # the ledger is derived from these config fields per check, so setting them is
            # enough; unknown keys are ignored
            for name, limit in value.items():
                if hasattr(ctx.budgets, str(name)) and isinstance(limit, int):
                    setattr(ctx.budgets, str(name), limit)
        elif field == "grants" and isinstance(value, dict):
            from .loopsetup import build_base_policy
            from .requests import rebuild_policy

            ctx.routine.grants = value
            build_base_policy(loop, value)   # the SAME builder configure() used — see there
            rebuild_policy(loop)
        elif field == "root_check" and value in rootmodes.MODES:
            rootcheck.adopt(ctx, str(value))
    except (AttributeError, TypeError, ValueError, OSError) as exc:
        log.warning("config_change: could not adopt %r live (%s) — it lands at the next run",
                    field, exc)


def apply_rule_additions(loop) -> None:
    """Turn-boundary: honour general rules the USER bound to a LIVE run from the web layer.

    The web layer records the slug; each added rule's prose arrives here as an appended engine
    note read from the library, and its assists start firing (`assist.rules_bound`).
    """
    from .. import library_docs

    ctx = loop.ctx
    if (sw := _fresh(loop, "add_rules", loop._last_rules_ts)) is None:
        return
    loop._last_rules_ts = str(sw["ts"])
    slugs = [s for s in (sw.get("slugs") or []) if isinstance(s, str)]
    for slug in slugs:
        if slug in ctx.consulted_rules:
            continue
        raw = library_docs.read_doc(ctx.server.rules_home, slug)
        if raw is None:
            continue
        ctx.consulted_rules.add(slug)
        text = library_docs.doc_body(raw).strip()
        note = (f"the user bound the general rule {slug!r} to this routine — it applies from "
                f"now on; every later run holds it too:\n\n{text}")
        enginenote.append(loop, note)
    assist.rules_bound(loop, slugs)


# The only two carriers of a rule's prose in a live thread (the digest lists slugs only).
def _carries_rule(content: str, slug: str) -> bool:
    return (f"the user bound the general rule {slug!r}" in content
            or f"OBSERVATION (read_rule {slug}," in content)


def apply_rule_drop(loop) -> None:
    """Turn-boundary: honour a mid-run UNBIND written to control.json by the web layer.

    Takes effect immediately, symmetric with a bind: the run is told the rule no longer binds,
    and its assists stop (`assist.rules_unbound`).

    `erase` also rewrites the messages carrying the rule's text into a tombstone (content only,
    so the user/assistant alternation survives). That INVALIDATES the prompt cache from the
    first edited message, so it is opt-in — one of the few rewrites the caching contract allows.
    """
    ctx = loop.ctx
    if (sw := _fresh(loop, "drop_rules", loop._last_rule_drop_ts)) is None:
        return
    loop._last_rule_drop_ts = str(sw["ts"])
    slugs = [s for s in (sw.get("slugs") or []) if isinstance(s, str)]
    if not slugs:
        return
    assist.rules_unbound(loop, slugs)
    erase = bool(sw.get("erase"))
    erased = 0
    if erase:
        for i, msg in enumerate(loop.messages):
            content = str(msg.get("content") or "")
            hit = [s for s in slugs if _carries_rule(content, s)]
            if not hit:
                continue
            loop.messages[i] = {**msg, "content":
                                f"[removed: the general rule(s) {', '.join(hit)} were unbound "
                                f"by the user and their text was withdrawn from this context]"}
            erased += 1
    for slug in slugs:
        ctx.consulted_rules.discard(slug)
    named = ", ".join(repr(s) for s in slugs)
    note = (f"the user UNBOUND the general rule(s) {named} — they no longer bind this routine. "
            f"Stop applying them from now on; no later run holds them.")
    if erased:
        note += (f" Their text has been withdrawn from the conversation above "
                 f"({erased} message(s) rewritten).")
    enginenote.append(loop, note)
