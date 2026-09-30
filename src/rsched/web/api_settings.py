"""The settings page's backend: one routine's settings as ONE document, its pattern, its pending
changes, and the single accept that applies them (docs/patterns.md).

The routine page used to save each section with its own button through its own endpoint, so a
person changing four things pressed four buttons and a proposal spanning four sections had
nowhere to live. Here the page reads every value at once (`GET …/settings`), edits a local draft
the person can see highlighted, and sends every change it kept in one request (`POST
…/settings`), which routes each field to the writer that already owns it — the validated PATCH
path for `routine.yaml`, the triggers list, the goal document — so nothing is written two ways.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from .. import triggers as triggers_mod
from ..paths import read_yaml
from ..patterns import drafts, fields, store
from ..patterns.recommend import CHECK_MESSAGE
from .api_routine_patch import RoutinePatch, apply_updates
from .routines_common import _info, _state, guard_not_active, write_routine_config

router = APIRouter(tags=["settings"])

#: The fields a pattern-less routine offers to "Save as new pattern" by default: everything a
#: pattern may govern except the bindings that name THIS routine's resources — its folders, its
#: accounts, its machines, its secret decisions, its clock. A person can still tick them in.
DEFAULT_PATTERN_FIELDS = tuple(k for k in fields.GOVERNABLE if k not in (
    "schedule", "fs_read_roots", "fs_write_roots", "connections", "machines", "grants"))



def _settings_payload(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    server = _state(request).server
    saved = fields.snapshot(info.cfg)
    pattern = store.read(server.libraries_home, info.cfg.pattern) if info.cfg.pattern else None
    draft = drafts.prune(server.routines_home, slug, saved, info.cfg.pattern)
    lane = _lane_managed(server, slug)
    keys = sorted(pattern["settings"]) if pattern else list(DEFAULT_PATTERN_FIELDS)
    # A lane-managed routine's clock is the LANE's: its own schedule value decides nothing, so
    # it can neither depart from a pattern nor distinguish two of them.
    compared = [k for k in keys if not (lane and k == "schedule")]
    candidate = {k: saved[k] for k in compared}
    identical = [p["slug"] for p in store.list_all(server.libraries_home)
                 if sorted(p["settings"]) == keys
                 and fields.matches(candidate, {k: p["settings"][k] for k in compared})]
    overrides = (fields.overrides(saved, {k: v for k, v in pattern["settings"].items()
                                          if k in compared}) if pattern else [])
    return {
        "slug": slug,
        "fields": saved,
        "meta": _meta(),
        "pattern": pattern,
        "pattern_missing": bool(info.cfg.pattern and pattern is None),
        "overrides": overrides,
        "draft": draft,
        # "Save as new pattern" is offered exactly when these values are no pattern yet
        "identical": identical,
        "save_as_enabled": not identical,
        "save_as_fields": keys,
        "lane_managed": lane,
    }


def _meta() -> list[dict]:
    """The field vocabulary — key, group, label, shape — which every settings surface renders
    from, so no page keeps a second copy of the labels.
    """
    return [{"key": f.key, "group": f.group, "label": f.label, "shape": f.shape,
             "governable": f.governable, "ask_first": f.ask_first} for f in fields.FIELDS]


def _lane_managed(server, slug: str) -> dict | None:
    from .. import lanes

    return next(({"id": ln["id"], "name": ln["name"]}
                 for ln in lanes.list_lanes(server.routines_home)
                 if ln["cron"] and slug in lanes.member_slugs(ln)), None)


@router.get("/routines/{slug}/settings")
def get_settings(request: Request, slug: str) -> dict:
    return _settings_payload(request, slug)


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict = {}
    # the pattern to follow once these land: a slug, "" for none, absent to leave it as is
    pattern: str | None = None


@router.post("/routines/{slug}/settings")
def apply_settings(request: Request, slug: str, body: ApplyBody) -> dict:
    """Apply every change the person kept — the page's one "accept changes" button.

    The routine.yaml fields go through the validated PATCH writer in ONE write; the triggers
    list and the goal document through their own owners after it. A change to a field this
    routine already holds is dropped rather than rewritten, so an accept never commits noise.
    Guarded while a run is active, like every multi-field config edit: a live run keeps the
    configuration it booted with; a half-applied proposal would be neither.
    """
    info = _info(request, slug)
    guard_not_active(request, info)
    server = _state(request).server
    saved = fields.snapshot(info.cfg)
    unknown = [k for k in body.changes if k not in fields.BY_KEY]
    if unknown:
        raise HTTPException(422, f"not a setting: {', '.join(unknown)}")
    wanted = {k: v for k, v in body.changes.items() if not fields.equal(k, saved.get(k), v)}
    lane_managed = _lane_managed(server, slug) is not None
    patch: dict = {}
    for key, value in wanted.items():
        if key in ("triggers", "finish_line"):
            continue
        pkey, pvalue = fields.patch_shape(key, value, lane_managed)
        patch[pkey] = pvalue
    if body.pattern is not None and body.pattern != info.cfg.pattern:
        patch["pattern"] = body.pattern
    applied: list[str] = []
    if patch:
        try:
            model = RoutinePatch.model_validate(patch)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        updates = model.model_dump(exclude_none=True)
        if model.run_gate is not None:
            updates["run_gate"] = model.run_gate.model_dump(exclude_unset=True)
        result = apply_updates(request, info, updates,
                               message=f"settings accepted via web ({', '.join(sorted(patch))})")
        applied += result["updated"]
    if "triggers" in wanted:
        _reconcile_triggers(request, slug, wanted["triggers"])
        applied.append("triggers")
    if "finish_line" in wanted:
        from .api_finishline import save as save_finish_line
        save_finish_line(info.cfg.dir, slug, info.cfg.name, dict(wanted["finish_line"] or {}))
        applied.append("finish_line")
    drafts.clear(server.routines_home, slug)
    return {"ok": True, "applied": applied, **_settings_payload(request, slug)}


def _reconcile_triggers(request: Request, slug: str, target: object) -> None:
    """Make the routine's triggers equal `target` (canonical rows: type + bounds, no identity).
    A trigger that is already there keeps its id and — for a webhook — its token, so a URL a
    third party holds keeps working; only a trigger the target lacks is removed, only one it
    adds is created.
    """
    info = _info(request, slug)
    raw = read_yaml(info.cfg.dir / "routine.yaml", {})
    current = [t for t in raw.get("triggers") or [] if isinstance(t, dict)]
    wanted = fields.canonical("triggers", target)
    want = list(wanted) if isinstance(wanted, list) else []
    kept: list[dict] = []
    for entry in current:
        rows = fields.canonical("triggers", [entry])
        row = rows[0] if isinstance(rows, list) and rows else {}
        if row in want:
            want.remove(row)
            kept.append(entry)
    for row in want:
        maker = (triggers_mod.new_report_trigger if row.get("type") == "report"
                 else triggers_mod.new_webhook_trigger)
        entry = maker(cooldown_s=int(row.get("cooldown_s") or (
            triggers_mod.DEFAULT_REPORT_COOLDOWN_S if row.get("type") == "report"
            else triggers_mod.DEFAULT_COOLDOWN_S)))
        entry.update({k: v for k, v in row.items() if k not in ("type", "cooldown_s")})
        kept.append(entry)
    raw["triggers"] = kept
    write_routine_config(request, info, raw, message="triggers set via web (settings)",
                         fields=["triggers"])


@router.delete("/routines/{slug}/settings/draft")
def discard_draft(request: Request, slug: str) -> dict:
    _info(request, slug)
    drafts.clear(_state(request).server.routines_home, slug)
    return {"ok": True}


class FollowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str


@router.post("/routines/{slug}/settings/follow")
def propose_pattern(request: Request, slug: str, body: FollowBody) -> dict:
    """Propose following another pattern: its governed values that differ from this routine's
    become pending changes; nothing is written to the routine until they are accepted.
    """
    info = _info(request, slug)
    server = _state(request).server
    pattern = store.read(server.libraries_home, body.pattern)
    if pattern is None:
        raise HTTPException(404, f"no settings pattern {body.pattern!r}")
    saved = fields.snapshot(info.cfg)
    changes = {k: {"value": row["after"], "reason": f"the value {pattern['title']} carries"}
               for k, row in fields.diff(saved, pattern["settings"],
                                         list(pattern["settings"])).items()}
    drafts.write(server.routines_home, slug, changes=changes, pattern=pattern["slug"],
                 message=f"these changes make it follow {pattern['title']}.",
                 source="follow")
    return _settings_payload(request, slug)


@router.post("/routines/{slug}/settings/recommend")
def recommend_settings(request: Request, slug: str) -> dict:
    """"Recommend for this routine": which pattern fits it now and what it needs beyond that
    pattern — written as pending changes the page shows under "check the changes i recommend.",
    never applied. Slow on purpose (one model call over the routine's recipe); the page shows it
    is working.
    """
    from ..patterns import recommend

    info = _info(request, slug)
    server = _state(request).server
    main = info.cfg.dir / "main.md"
    task = (info.cfg.description + "\n\n"
            + (main.read_text(encoding="utf-8") if main.is_file() else ""))
    current = store.read(server.libraries_home, info.cfg.pattern) if info.cfg.pattern else None
    workflow = (current or {}).get("workflow") or info.cfg.workflow_slug
    chosen = recommend.choose(server, workflow=workflow, name=info.cfg.name, task=task)
    saved = fields.snapshot(info.cfg)
    pattern = store.read(server.libraries_home, chosen) if chosen else None
    changes: dict[str, dict] = {}
    base = dict(saved)
    if pattern is not None and pattern["slug"] != info.cfg.pattern:
        for k, row in fields.diff(saved, pattern["settings"], list(pattern["settings"])).items():
            changes[k] = {"value": row["after"], "reason": f"the value {pattern['title']} carries"}
            base[k] = row["after"]
    changes.update(recommend.recommend(server, slug=slug, saved=base, pattern=pattern,
                                       context=task))
    switch = pattern["slug"] if pattern is not None and pattern["slug"] != info.cfg.pattern \
        else None
    if changes or switch:
        drafts.write(server.routines_home, slug, changes=changes, pattern=switch,
                     message=CHECK_MESSAGE, source="recommend")
    return _settings_payload(request, slug)


class SaveAsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    summary: str
    when: str = ""
    workflow: str = ""
    fields: list[str] | None = None


@router.post("/routines/{slug}/patterns")
def save_as_pattern(request: Request, slug: str, body: SaveAsBody) -> dict:
    """"Save as new pattern": this routine's current values for the chosen fields become a new
    library pattern; the routine follows it from now on. Refused when an existing pattern
    already carries exactly these values — that one is the pattern to follow instead.
    """
    from .. import libgit

    info = _info(request, slug)
    server = _state(request).server
    saved = fields.snapshot(info.cfg)
    keys = [k for k in (body.fields or _settings_payload(request, slug)["save_as_fields"])
            if k in fields.BY_KEY and fields.BY_KEY[k].governable]
    settings = {k: saved[k] for k in keys}
    same = [p["slug"] for p in store.list_all(server.libraries_home)
            if sorted(p["settings"]) == sorted(keys) and fields.matches(settings, p["settings"])]
    if same:
        raise HTTPException(409, f"pattern {same[0]!r} already carries exactly these values — "
                                 "follow it instead of saving a copy")
    new_slug = store.unique_slug(server.libraries_home, body.title)
    workflow = body.workflow or (store.read(server.libraries_home, info.cfg.pattern) or {}).get(
        "workflow") or info.cfg.workflow_slug or "general-task"
    try:
        pattern = store.create(server.libraries_home, new_slug, {
            "title": body.title, "summary": body.summary, "when": body.when,
            "workflow": workflow, "settings": settings, "from_routine": slug})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    libgit.commit(server.libraries_home, f"new settings pattern {new_slug} from {slug} via web",
                  paths=[f"{store.SUBDIR}/{new_slug}.yaml"])
    apply_updates(request, info, {"pattern": new_slug},
                  message=f"follow new settings pattern {new_slug} via web")
    return {"ok": True, "pattern": pattern, **_settings_payload(request, slug)}


@router.get("/patterns")
def list_patterns(request: Request) -> dict:
    server = _state(request).server
    rows = store.list_all(server.libraries_home)
    for p in rows:
        p["followers"] = store.followers(server.routines_home, p["slug"])
    return {"patterns": rows, "meta": _meta()}


@router.get("/patterns/{slug}")
def pattern_detail(request: Request, slug: str) -> dict:
    server = _state(request).server
    pattern = store.read(server.libraries_home, slug)
    if pattern is None:
        raise HTTPException(404, f"no settings pattern {slug!r}")
    return {**pattern, "followers": store.followers(server.routines_home, slug)}


@router.delete("/patterns/{slug}")
def delete_pattern(request: Request, slug: str) -> dict:
    """Delete a pattern. Its followers keep every value they hold — those were always their
    own — and stop naming it, in the same operation.
    """
    from .. import libgit, registry

    server = _state(request).server
    if store.read(server.libraries_home, slug) is None:
        raise HTTPException(404, f"no settings pattern {slug!r}")
    released = []
    for follower in store.followers(server.routines_home, slug):
        info = registry.info(server, server.routines_home, follower)
        if info is None:
            continue
        raw = read_yaml(info.cfg.dir / "routine.yaml", {})
        raw.pop("pattern", None)
        write_routine_config(request, info, raw,
                             message=f"settings pattern {slug} deleted — follows none now",
                             fields=["pattern"])
        released.append(follower)
    store.delete(server.libraries_home, slug)
    libgit.commit(server.libraries_home, f"delete settings pattern {slug} via web",
                  paths=[f"{store.SUBDIR}/{slug}.yaml"])
    return {"ok": True, "released": released}
