"""A queued proposal's VOCABULARY, judged at FILING — and what the filing run is told (D229).

A scheduled run cannot write config, so `create_routine` and `manage_lane` QUEUE a proposal
(`pending.py`) that the Decisions page materializes with one click (`web/api_pending.py`).
Nothing judged the VALUES in that record. F610 measured the consequence: a proposal carrying a
workflow slug the library does not hold, a slug that is not kebab-case, a cron croniter cannot
parse — each read fine on the page and each raised on the operator's ACCEPT click, "three
different fields in one day". The failure reached the one party who cannot fix it, at the one
moment he cannot act on it, and the run that COULD have fixed it had finished days earlier.

So the judgement moves to the filing seam, where `engine/config_bridge.py` already puts the same
class of check for a `config_patch`: the refusal arrives in the observation the filing run reads,
on the turn it filed, naming the field and the vocabulary it must use. The run corrects it and
re-files; the operator never sees a card he can read and not take.

Judged against the LIVE vocabulary, never a copy of one — the discipline
`config_bridge.patch_shape` follows for `RoutinePatch`:

* a workflow slug — `workflows.library.list_workflows`, the library on disk;
* a lane member — `registry.scan`, the routines that exist;
* `on_failure` — `lanes.ON_FAILURE`, the store's own tuple;
* a cron — `lanes._check_cron`, the same croniter call `lanes.create` makes;
* a routine slug's shape — `ids.is_slug`, the function `workflows.scaffold` raises on.

A second hardcoded list here would be the defect in a new place, so there is none.

**Only a value the ACCEPT would refuse is refused here** (`validate.py`'s rule: a filing check
stricter than the accept would refuse a proposal that works). One deliberate exception, named
because it is one: a lane `members` entry naming no routine. `web/api_lanes._validate_members`
and `engine/manage_lane._members_or_error` both refuse it, and `lanes.py` states the rule ("a
lane may only name routines that exist in the registry") — but `api_pending._materialize_lane`
passes members straight to the store, so today the queue path alone builds a lane naming a
ghost. Refusing it at filing makes the three paths agree instead of letting the queue be the
loose one.

This module is the gate for the two kinds that carry vocabulary. The other four queue kinds
(`library-drift`, `goal-reached`, `replace-routine`, `revise-routine`, `unowned-report`) carry
report ids, line prose and slugs of routines that already exist — no fixed vocabulary to judge.
A kind that grows one adds its judge here, beside these two.

It is NOT inside `pending.queue` itself: the judge needs the libraries home and the registry,
which that writer does not take. Both filing paths reach it through the two observation builders
below, which are their only way to the queue.
"""

from __future__ import annotations

#: The lane verbs `web/api_pending._materialize_lane` can actually build. `run` never reaches a
#: queue (`manage_lane` refuses it loudly — a fire is ephemeral), and anything else is a card
#: whose accept answers 400 "not a creation".
LANE_QUEUE_VERBS = ("create", "update", "delete", "set-default")

#: What a refused filing says after the field-level sentence. It is the correction the asking
#: run can act on THIS turn, which is the whole point of judging here instead of at the click.
_REFILE = ("Nothing was queued. Correct the field and file it again this run, or — if it needs "
           "a decision only the operator can make — say so in your finish summary. A proposal "
           "carrying this value would reach his Decisions page as a card he can read and not "
           "take: the accept would refuse it and nothing would be created.")


def problem(server, kind: str, fields: dict) -> str:
    """"" when the accept would take this proposal's field values, else the sentence the filing
    is refused with — the offending field first, then the vocabulary that was allowed instead.
    """
    if kind == "create_routine":
        return _routine_problem(server, fields)
    if kind == "manage_lane":
        return _lane_problem(server, fields)
    return ""


def _routine_problem(server, fields: dict) -> str:
    """The two fields of a queued creation drawn from a fixed vocabulary: the slug's SHAPE and
    the workflow pattern. `name`, `instruction` and the line lists are free text with no
    vocabulary to be wrong against; `pattern` is NOT judged here because an unknown settings
    pattern does not fail the accept — `workflows.scaffold` reads no settings and leaves the
    routine without one, which is a silent-drop defect of its own and not this gate's.
    """
    from .ids import is_slug
    from .workflows import library

    slug = str(fields.get("slug") or "").strip()
    if not is_slug(slug):
        return (f"slug: {slug!r} is not kebab-case. `workflows.scaffold` refuses it on the "
                "operator's accept click, so the proposal could never be built — a slug is "
                "lowercase letters, digits and hyphens, starting with a letter or a digit.")
    workflow = str(fields.get("workflow") or "").strip()
    known = sorted(w["slug"] for w in library.list_workflows(server.libraries_home))
    if workflow not in known:
        why = (" `generate` is the conversation flow's own choice — it DRAFTS a new pattern "
               "while a user watches, and the Decisions page has no way to draft one, so it is "
               "never a queued proposal's workflow."
               if workflow == "generate" else "")
        return (f"workflow: {workflow!r} is not a pattern the library holds.{why} Pick one the "
                f"library has: {', '.join(known)}.")
    return ""


def _lane_problem(server, fields: dict) -> str:
    """Every lane field drawn from a fixed vocabulary, in the order the accept would hit them:
    the verb the materializer dispatches on, the lane id or name that verb needs, the failure
    policy, the cron, then the members.
    """
    from . import lanes, registry

    verb = str(fields.get("verb") or "").strip()
    if verb not in LANE_QUEUE_VERBS:
        return (f"verb: {verb!r} is not a lane change this queue can materialize. Proposable: "
                f"{', '.join(LANE_QUEUE_VERBS)}.")
    # `set-default` REQUIRES the policy (it is the whole change); create/update take it as an
    # optional override, where absent means "inherit the instance default".
    policy = str(fields.get("on_failure") or "").strip()
    if verb == "set-default":
        if policy not in lanes.ON_FAILURE:
            return (f"on_failure: {policy!r} is not a mid-chain-failure policy — "
                    f"{' or '.join(lanes.ON_FAILURE)}. A lane set-default is that value.")
    elif policy and policy not in lanes.ON_FAILURE:
        return (f"on_failure: {policy!r} is not a mid-chain-failure policy — "
                f"{' or '.join(lanes.ON_FAILURE)}, or omit it to inherit the instance default.")
    if verb in ("update", "delete") and not str(fields.get("target") or "").strip():
        return (f"target: a lane {verb} needs the lane id, which `verb=list` returns (an opaque "
                "handle; do not construct one). Without it the accept answers 404.")
    if verb == "create" and not str(fields.get("name") or "").strip():
        return ("name: a lane needs a display name — `lanes.create` refuses one without it on "
                "the accept click.")
    if "cron" in fields:
        try:
            lanes._check_cron(str(fields.get("cron") or ""))
        except ValueError as exc:
            return (f"cron: {exc}. A lane schedule is a five-field cron expression in the "
                    "server's zone, e.g. '0 10 * * *'; '' clears one.")
    members = fields.get("members")
    if isinstance(members, list):
        known = set(registry.scan(server))
        if unknown := sorted({str(m) for m in members} - known):
            return (f"members: {', '.join(repr(m) for m in unknown)} "
                    f"{'is' if len(unknown) == 1 else 'are'} not a routine this instance has. A "
                    "lane may only name routines that exist in the registry; `verb=list` and "
                    "the Routines page name them.")
    return ""


def _refused(kind: str, why: str, extra: dict) -> dict:
    """The refusal the filing run reads — the field named, the vocabulary, the correction."""
    return {"kind": kind, "rejected": True, "queued": False,
            "reason": f"{kind} proposal refused at filing — {why} {_REFILE}", **extra}


def queued_routine_obs(ctx, fields: dict) -> dict:
    """A scheduled run's routine proposal, judged and then filed for the operator (F328).

    R353 is the case the queue exists for: routine-improver reached a run with a fully designed,
    user-approved routine ready and could not materialize it, so the design was hand-carried
    back to the user to paste in. The restriction to conversations was right — a scheduled run
    has no user to design WITH — but the consequence was wrong. What was missing is a queue, not
    permission. D229 adds the gate at the FRONT of it: a proposal whose values the accept would
    refuse never becomes a card.
    """
    from .pending import queue

    if why := problem(ctx.server, "create_routine", fields):
        return _refused("create_routine", why, {"slug": fields.get("slug")})
    proposal = (f"proposed: create routine {fields['slug']!r} from pattern "
                f"{fields['workflow']!r}")
    rec = queue(ctx.server.routines_home, kind="create_routine", routine=ctx.routine.slug,
                run_id=ctx.run_id, fields=fields,
                summary=f"routine {fields['slug']!r} from pattern {fields['workflow']!r}")
    return {"kind": "create_routine", "slug": fields["slug"], "queued": True, "id": rec["id"],
            "proposal": proposal,
            "next": ("Nothing is created yet, and nothing will be until the user approves it — "
                     "you have no user in the loop, so this went to the Decisions page as a "
                     "proposal. Do NOT re-issue it: a second call queues a second proposal. "
                     "Your next run learns the outcome from a message in your inbox. Finish the "
                     "work that does not depend on this routine existing, and say in your "
                     "summary that the creation is awaiting approval.")}


def queued_lane_obs(ctx, action: dict, verb: str, proposal: str) -> dict:
    """A scheduled run's lane proposal, judged and then filed (F328) — the twin of the routine
    one. R353 needed BOTH: a routine plus the lane it belongs in. `proposal` is the caller's own
    one-line reading of the change (`manage_lane._proposal_line`, which has the lane store).
    """
    from .pending import queue

    fields = {k: action[k] for k in ("verb", "target", "name", "members",
                                     "on_failure", "cron", "paused") if k in action}
    fields["verb"] = verb
    if why := problem(ctx.server, "manage_lane", fields):
        return _refused("manage_lane", why, {"verb": verb})
    rec = queue(ctx.server.routines_home, kind="manage_lane", routine=ctx.routine.slug,
                run_id=ctx.run_id, fields=fields, summary=proposal)
    return {"kind": "manage_lane", "verb": verb, "queued": True, "id": rec["id"],
            "proposal": proposal,
            "next": ("Nothing changed yet and nothing will until the user approves it — you "
                     "have no user in the loop, so this went to the Decisions page as a "
                     "proposal. Do NOT re-issue it: a second call queues a second proposal. "
                     "Your next run learns the outcome from a message in your inbox.")}
