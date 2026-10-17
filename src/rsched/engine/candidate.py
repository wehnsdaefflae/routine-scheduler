"""A REPLACEMENT CANDIDATE — a routine rebuilt under a new slug, run beside the one it would
replace, swapped in with ONE operator accept.

On 2026-10-17 the operator replaced two routines by hand: freelance-radar (89 applications, zero
interviews, ~2,000 recipe lines, 28 check scripts) and grants-radar (no new programme in 14
runs) gave way to lean outcome-led rebuilds. routine-improver had visited both for months and
could only make "small, reversible" changes — so it added controls to a design that needed
deleting. This module is the mechanism that lets the improver do what the operator did: draft
the replacement, prove it beside the old one, and hand the operator one decision.

`create_routine` with `replaces: <slug>` (a depth-0 run holding the `rebuild_routine`
capability, which only the `routine-rebuild` permission grants) creates the candidate AT ONCE —
no queue — because a candidate has no authority the operator did not already give the routine it
replaces:

- its routine.yaml is the OLD routine's, with its own slug, name and description, switched on,
  on NO schedule and in NO lane — it fires only when someone fires it (the improver's one-shot,
  the operator's run-now);
- and WITHOUT the old routine's `grants`: no secret reaches it until the swap, so it can prove
  its recipe on what it can reach without acting in the principal's name — no mail, no login, no
  application sent twice;
- its recipe starts as a stub the improver writes over (`main.md`, `stages/`, `scripts/`).

The same call files ONE `replace-routine` proposal on the Decisions page. Accepting it is the
SWAP, through the ordinary config writers (web/api_pending.py): the candidate takes the old
routine's place in its lane, its cron and its grants; the old routine is switched off. Discarding
it switches the candidate off and leaves the old routine as it was. Either way the proposer is
told through its inbox. `.control/candidates.json` records which routine is a candidate for
which, so the card, the routine page and the improver read one answer.
"""

from __future__ import annotations

from pathlib import Path

from .. import libgit
from ..ids import is_slug, now_iso
from ..paths import atomic_write_json, atomic_write_yaml, read_json, read_yaml
from .run_context import RunContext

CANDIDATES = Path(".control") / "candidates.json"
CAPABILITY = "rebuild_routine"
STUB = ("# {name}\n\nReplacement candidate for `{old}` — its recipe is being written by "
        "{run}. Until it is, a run of it has nothing to do: finish.\n")
GITIGNORE = "runs/\ninbox/\nquestions/\nmnt/\n.util_outputs/\n.doc_cache/\n.venv/\n"


def load(routines_home: Path) -> dict:
    """`{candidate slug: {replaces, by, created_at, why}}` — every standing candidate."""
    data = read_json(routines_home / CANDIDATES)
    return data if isinstance(data, dict) else {}


def forget(routines_home: Path, slug: str) -> None:
    data = load(routines_home)
    if data.pop(slug, None) is not None:
        atomic_write_json(routines_home / CANDIDATES, data)


def _refused(slug: str, reason: str) -> dict:
    return {"kind": "create_routine", "slug": slug, "rejected": True, "reason": reason}


def _problem(ctx: RunContext, slug: str, old: str) -> str:
    home = ctx.server.routines_home
    if ctx.depth > 0:
        return "a candidate is created by the run that will prove it, never by a child run"
    if CAPABILITY not in ((ctx.routine.capabilities or {}).get("actions") or []):
        return ("creating a replacement candidate needs the routine-rebuild permission, which "
                "this routine does not hold — propose the rebuild in a report instead")
    if not is_slug(slug) or (home / slug).exists():
        return f"{slug!r} is not a free kebab-case slug"
    if not is_slug(old) or not (home / old / "routine.yaml").is_file():
        return f"{old!r} is not a routine to replace"
    if old in load(home):
        return f"{old!r} is itself a candidate — swap or discard it first"
    if any(rec.get("replaces") == old for rec in load(home).values()):
        return f"{old!r} already has a standing candidate — prove or discard that one first"
    return ""


def create(ctx: RunContext, action: dict) -> dict:
    """Create the candidate routine and file the one swap proposal (module docstring)."""
    from ..pending import queue

    slug = str(action.get("target") or "").strip()
    old = str(action.get("replaces") or "").strip()
    if why := _problem(ctx, slug, old):
        return _refused(slug, why)
    name = str(action.get("name") or slug).strip()
    reason = str(action.get("prompt") or "").strip()
    home = ctx.server.routines_home
    raw = read_yaml(home / old / "routine.yaml", {})
    schedule = raw.get("schedule") if isinstance(raw.get("schedule"), dict) else {}
    raw.update(slug=slug, name=name, enabled=True, grants={}, triggers=[],
               description=f"Replacement candidate for {old}: {reason}"[:400],
               schedule={**schedule, "cron": ""})
    raw.pop("trial", None)
    d = home / slug
    d.mkdir(parents=True)
    (d / "state").mkdir()
    (d / "main.md").write_text(STUB.format(name=name, old=old, run=ctx.run_id), encoding="utf-8")
    (d / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    atomic_write_yaml(d / "routine.yaml", raw)
    libgit.init_repo(d, first_commit=f"candidate {slug} to replace {old} ({ctx.run_id})")
    data = load(home)
    data[slug] = {"replaces": old, "by": ctx.run_id, "created_at": now_iso(), "why": reason}
    atomic_write_json(home / CANDIDATES, data)
    rec = queue(home, kind="replace-routine", routine=ctx.routine.slug, run_id=ctx.run_id,
                fields={"candidate": slug, "replaces": old, "why": reason},
                summary=f"replace {old} by candidate {slug}")
    return {"kind": "create_routine", "slug": slug, "candidate": True, "replaces": old,
            "dir": str(d), "id": rec["id"],
            "next": (f"Candidate {slug} exists at {d}, switched on, on no schedule, in no lane, "
                     f"with {old}'s settings and none of its secrets. Write its recipe now — "
                     "main.md, stages/, scripts/ (an outcome script included) — and fire it "
                     "beside the old routine to compare their outcome series. The swap is ONE "
                     f"decision already on the Decisions page ({rec['id']}): accepting it moves "
                     f"{old}'s lane place, schedule and secrets to {slug} and switches {old} "
                     "off. Do not create a second candidate for it.")}
