"""The `create_routine` action handler — a CONVERSATION graduates the work it just
clarified with the user into a real scheduled routine (D58), in TWO steps (D92):
preview first, materialize only after the user has confirmed the draft.

The operator's rule: routine creation is initiated from a conversation ONLY. Instead of a
retired standalone wizard page, the conversation agent (or a `/create_routine` slash
command) clarifies the task WITH the user in the normal chat, then emits this action. The
FIRST call VALIDATES the named workflow against the live library, then stores a DRAFT (slug,
name, instruction, workflow) under the conversation's own
`state/routine-draft.json` and returns a preview observation carrying the pattern catalog —
nothing is created yet; the
agent relays the preview and finishes its reply. When the user answers, the agent calls
`create_routine` again with the SAME fields, and only then does the handler materialize —
through the same `workflows.scaffold` path the retired wizard's build half called
(decompose the chosen workflow into main.md + stages/, adapt rules, write routine.yaml,
init the auto-push git repo — one materializer only).

The confirm gate is structural, not honor-system: a conversation reply runs as its own
engine process, so a draft written by THIS process cannot be confirmed by this process —
the confirming call must come from a LATER leg, which a root conversation only gets after
the user (or a background delivery) speaks. A changed field on the confirming call is a
DESIGN CHANGE, not a confirmation: it replaces the draft and restarts the round-trip.

Structural rule (mirrors `detach`): valid ONLY from a ROOT CONVERSATION (depth 0, dir directly
under conversations_home). A scheduled routine has no user in the loop to design a new routine
with, and a within-reply child must not create routines as a side effect. The engine ALSO only
surfaces the kind to a root conversation (loop.allowed_tools injection), so this is defence in
depth, not the only gate.

Unlike `detach` (which drops an intent for the daemon because an HTTP request cannot block on a
minute-long build), the handler scaffolds SYNCHRONOUSLY: an engine turn already does long async
work and returns an observation, the conversation user is waiting for the routine now, and the
daemon's registry rescan (`registry_rescan_s`) picks the new dir up on its own timer — so no new
daemon manager is needed. The slow step is the workflow decompose inside scaffold, which degrades
to a no-LLM fallback when no endpoint is available (tests, offline).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..ids import now_iso
from ..paths import atomic_write_json, read_json
from .detach import _is_root_conversation
from .run_context import RunContext

# The default pattern when the conversation does not name one — the sane general-purpose
# workflow, same default the spawn/subtask/detach actions use.
DEFAULT_WORKFLOW = "general-task"

#: The choice that is always on the workflow question beside the library patterns: draft one
#: FITTED to this task. No catalog covers every task, and a routine built on a pattern that
#: merely almost fits carries that mismatch for its whole life, so the list is never closed.
#: The user PICKING it is the gate — the `workflows: generate` capability governs a SUBTASK
#: drafting a pattern on its own initiative, where nobody is watching; here someone chose it
#: one screen ago, and the confirming call is the answer to that choice.
GENERATE_SLUG = "generate"

#: Where a conversation's pending routine draft lives (relative to the conversation dir).
#: ONE draft per conversation: a new slug simply replaces the old draft — the flow is a
#: linear chat, not a queue.
DRAFT_RELPATH = Path("state") / "routine-draft.json"


def _draft_path(ctx: RunContext) -> Path:
    return ctx.routine.dir / DRAFT_RELPATH


def _load_draft(ctx: RunContext) -> dict | None:
    path = _draft_path(ctx)
    if not path.is_file():
        return None
    draft = read_json(path)
    return draft if isinstance(draft, dict) and draft.get("slug") else None


#: Patterns tagged `meta` are HARNESSES, not task patterns: `converse` assumes a present user
#: who reads the reply and writes back, which a scheduled routine never has. The tag already
#: existed for exactly this ("keeps it out of spawn-pattern lists and wizard suggestions") —
#: the creation catalog was the one surface that never applied it, so it offered `converse`
#: as a buildable choice against that pattern's own `when_to_use`.
META_TAG = "meta"


def _catalog(server) -> list[dict]:
    """Every BUILDABLE library pattern, one line each, and `generate` LAST: what the draft
    observation shows so the choice is made against the real catalog rather than from memory
    (F383), and against an open list rather than a closed one. Harness patterns (`meta`) are
    excluded — see META_TAG.
    """
    from ..workflows import library

    return [{"slug": w["slug"], "description": w["description"] or w["name"],
             **({"when_to_use": w["when_to_use"]} if w["when_to_use"] else {})}
            for w in library.list_workflows(server.libraries_home)
            if META_TAG not in (w.get("tags") or [])] + [
        {"slug": GENERATE_SLUG,
         "description": "draft a NEW pattern fitted to this task, and build the routine on it",
         "when_to_use": "no pattern above fits this task without stretching it"}]


def _generate_pattern(ctx: RunContext, slug: str, name: str, instruction: str) -> str | dict:
    """Draft the fitted pattern the user picked. Returns its library slug, or an observation
    dict when it could not be drafted.

    Never falls back to a catalog pattern on failure, the way a subtask does: a subtask that
    cannot generate has nobody to tell and a job to get on with, whereas here the user chose
    `generate` OVER every catalog entry — quietly building on one of them would materialize
    the option they rejected, under the name they approved.
    """
    from .subruns import GEN_FLOOR_TOKENS

    def refused(reason: str) -> dict:
        return {"kind": "create_routine", "slug": slug, "workflow": GENERATE_SLUG,
                "rejected": True, "reason": reason}

    remaining = ctx.tokens_remaining()
    if remaining is not None and remaining < GEN_FLOOR_TOKENS:
        return refused("not enough token budget left to draft a new pattern and lint-repair "
                       "it. Nothing was created. Tell the user, and either pick a catalog "
                       "pattern with them or let a later reply do the drafting.")
    try:
        from ..workflows.generate import generate

        new_slug, _problems = generate(ctx.server, instruction, hint=name,
                                       on_usage=ctx.add_usage)
    except Exception as exc:
        return refused(f"drafting a new pattern failed ({exc}). Nothing was created. Say so "
                       "and settle the workflow question with the user again — do NOT "
                       "silently build on a catalog pattern they did not choose.")
    return new_slug


def _unknown_workflow_obs(slug: str, workflow_slug: str, catalog: list[dict]) -> dict:
    """A draft naming a pattern the library does not hold is refused HERE — before the user
    is asked to confirm — instead of at the expensive materialize step (F387/R493).
    """
    return {"kind": "create_routine", "slug": slug, "workflow": workflow_slug,
            "rejected": True, "workflow_catalog": catalog,
            "reason": f"no workflow {workflow_slug!r} exists in the library. Put the choice to "
                      "the user as an ask_user whose options are workflow_catalog below."}


#: The design judgements a draft must have made before it is presented as decided. They are
#: the operator's standing intake rules, and they live HERE because this observation is the
#: only live copy of the intake contract — the `clarify-instruction` pattern that once held a
#: second copy was never executed, so its copy silently went stale (it still described conduct
#: as per-routine "traits" long after rules became one shared library doc).
_DESIGN_CHECKS = (
    "SHAPE — if the task BOTH ingests/processes signal (reads sources, updates state, "
    "computes) AND sends outbound communication (mail, messages, publishing), offer the user "
    "the choice of TWO routines in one group instead of one: grouped members all ingest "
    "first and all communicate after, so one member's outbound can act on another's "
    "freshly-processed state instead of waiting a whole cadence. Their call, not yours. "
    "(Operator standing rule, 2026-08-05.)",
    "MECHANISM — judge which parts of the task are judgment-free and repeated identically "
    "every run (fetching/polling, parsing structured data, arithmetic, filtering/sorting/"
    "dedup, threshold checks, assembling a fixed artifact) and say so in the instruction: "
    "those belong in the routine's OWN scripts/, written once and called thereafter, with "
    "the recipe staying the single interpreter. Genuinely generative work — drafting prose, "
    "weighing fit, deciding what matters — stays in the recipe. A capability other routines "
    "would share too is a util, not this routine's script. (Operator standing rule, "
    "2026-08-12.)",
    "OWNERSHIP — the instruction is the TASK and nothing else. Conduct is general RULES "
    "(one copy each in the library, bound by slug in routine.yaml, read at run time with "
    "read_rule) and capability is user-set PERMISSIONS. Put neither in the instruction, and "
    "never let it assume a rule or permission is present. If the draft mixes conduct into "
    "the task ('message me on discord when…', 'improve your own prompt each run'), do not "
    "copy it in — name it to the user as a rule or permission choice. Conduct baked into "
    "the instruction keeps acting after they change the routine's setup, which takes the "
    "control surface away from them.",
    "SCOPE — schedule and cadence, budgets, working directory, and model/endpoint choices "
    "are routine CONFIG, set in the UI. Never ask about them and never write them into the "
    "instruction. A draft that names a schedule ('every Monday…') is giving you a hint: "
    "phrase the task per-run ('each run, cover what appeared since the last covered point, "
    "tracked in state/') so it holds whatever the cadence turns out to be.",
)


def _preview_obs(draft: dict, catalog: list[dict], *, updated: bool,
                 blocked_same_leg: bool = False) -> dict:
    """The draft/preview observation: what WILL be created, the catalog the choice was made
    against, the design judgements the draft must have made, and the exact next step. The
    teaching copy is the contract — a same-leg confirm attempt gets told why it was held.
    """
    instruction = draft["instruction"]
    obs = {"kind": "create_routine", "slug": draft["slug"], "name": draft["name"],
           "workflow": draft["workflow"], "draft": True, "updated": updated,
           "instruction_chars": len(instruction),
           "instruction_preview": instruction[:600],
           # F383: the pattern catalog rides the observation so the relay compares against
           # what the library actually holds — the choice stops being an unexamined default.
           "workflow_catalog": catalog,
           "design_checks": list(_DESIGN_CHECKS),
           "next": ("Nothing is created yet. Put this draft to the user as DECISIONS, not as "
                    "prose: every point still open goes out as its own `ask_user` carrying "
                    "`options`, which the console renders as numbered picks. A question "
                    "without options is a blank text box that makes the user compose an "
                    "answer you already knew how to offer them. The WORKFLOW question is "
                    "always one of these, and its options are the entries of "
                    "workflow_catalog — which always ends in 'generate', drafting a new "
                    "pattern fitted to this task; never present the workflow as already "
                    "chosen. State what the routine PRODUCES each run and what DONE looks "
                    "like for one run in the user's own words; if either is YOUR inference, "
                    "it is an open point and it goes out as an ask_user with options like the "
                    "rest. Their answer to the DONE question is not prose to paraphrase into "
                    "the instruction: put it in `stopping`, one condition per entry, in their "
                    "words. That becomes the routine's stopping conditions, which every run "
                    "must account for in its finish summary — without them a run is bounded "
                    "only by its budgets, which are a runaway backstop and not a definition of "
                    "done. Omit `stopping` rather than inventing conditions they did not "
                    "state. ASK A SECOND, DIFFERENT QUESTION: is there a state after which "
                    "this routine is FINISHED for good — a thing submitted, a migration "
                    "complete, an event past? Their answer goes in `goal`, verbatim, and it "
                    "has teeth: a met goal stops the scheduler firing the routine. Many "
                    "routines honestly have no such state (a monitor, a digest) and then "
                    "`goal` is omitted — but ASK, because a routine nobody ever asked runs "
                    "forever by default, and where the answer carries a DATE the recipe must "
                    "name it literally. Then finish the reply. Once the user has answered, call "
                    "create_routine again with the SAME fields to materialize it; a call with "
                    "changed fields updates the draft and restarts the confirmation.")}
    if blocked_same_leg:
        obs["held"] = ("This reply already drafted the routine — the confirming call must "
                       "follow the user's answer, in their next message. Show the draft and "
                       "finish the reply.")
    return obs


def _materialize(ctx: RunContext, *, slug: str, name: str, instruction: str,
                 workflow_slug: str, stopping: list[str] | None = None,
                 goal: list[str] | None = None) -> dict:
    """The confirmed half: draft the fitted pattern if that is what the user picked, then build
    the routine from `workflow_slug`. Every failure here is an OBSERVATION the model can act on
    — a conversation run must never die because a build step did.
    """
    from ..workflows.scaffold import scaffold

    if workflow_slug == GENERATE_SLUG:
        drafted = _generate_pattern(ctx, slug, name, instruction)
        if isinstance(drafted, dict):
            return drafted          # refused or failed — the draft stands, nothing created
        workflow_slug = drafted
    from ..workflows.suggest import generate_description
    try:
        # A COMPREHENSIVE generated description (purpose / requirements / side effects /
        # inter-routine dependencies) replaces the old `description = name`; it falls back to
        # the name itself when no endpoint answers, so this never fails the creation.
        description = generate_description(ctx.server, name=name, instruction=instruction,
                                           workflow_slug=workflow_slug)
        routine_dir = scaffold(ctx.server, slug=slug, name=name, instruction=instruction,
                               workflow_slug=workflow_slug, description=description,
                               stopping=stopping, goal=goal)
    except ValueError as exc:
        # bad slug, unknown workflow, or a dir that appeared mid-flight — a teaching rejection,
        # corrected by the model, never a crash
        return {"kind": "create_routine", "slug": slug, "error": str(exc)}
    except OSError as exc:
        # the filesystem shifted under the build (R478: the user deleted the half-made dir
        # while the slow workflow decompose ran; the write that followed hit FileNotFoundError
        # and, uncaught, orphaned the whole conversation run rc=1) — surface it as an
        # actionable error observation instead of crashing the engine
        return {"kind": "create_routine", "slug": slug,
                "error": f"materialization failed mid-build ({exc}); nothing usable was "
                         "created — check the routines home and try again"}
    _draft_path(ctx).unlink(missing_ok=True)
    import contextlib

    import yaml as _yaml

    from ..paths import read_yaml

    adopted = ""
    with contextlib.suppress(OSError, _yaml.YAMLError):
        adopted = str(read_yaml(routine_dir / "routine.yaml", {}).get("template") or "")
    return {"kind": "create_routine", "slug": slug, "name": name,
            "workflow": workflow_slug, "created": True, "dir": str(routine_dir),
            "template": adopted, "url": routine_page_url(ctx.server, slug),
            "rescan_s": ctx.server.registry_rescan_s}


def routine_page_url(server, slug: str) -> str:
    """Where the user can open the routine. An absolute URL when the instance knows its own
    (`public_url`), else the in-app route — a link the reader can act on either way, which is
    the point: "it exists" without "here it is" makes the user go looking for it.
    """
    base = str(server.public_url or "").rstrip("/")
    return f"{base}/#/routine/{slug}" if base else f"#/routine/{slug}"


def _queued_obs(ctx: RunContext, fields: dict) -> dict:
    """A scheduled run's proposal, filed for the operator (F328).

    R353 is the case: routine-improver reached a run with a fully designed, user-approved
    routine ready and could not materialize it, so the design was hand-carried back to the user
    to paste in. The restriction to conversations was right — a scheduled run has no user to
    design WITH — but the consequence was wrong. What was missing is a queue, not permission.
    """
    from ..pending import queue

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


def _for_non_conversation(ctx: RunContext, fields: dict) -> dict | None:
    """What happens when the caller is not a root conversation — None when it IS one.

    Two different answers, and the difference is whether a user could ever have been in the
    loop. A CHILD run is refused outright: a sub-workflow must not create routines as a side
    effect, and a proposal from one traces back to nothing the user reasoned about. A
    top-level SCHEDULED run gets a queue (F328) — it has a user, just not right now.
    """
    if ctx.depth > 0:
        return {"kind": "create_routine", "rejected": True,
                "reason": "create_routine is not available inside a child run — a sub-workflow "
                          "must not create routines as a side effect. Hand the design back in "
                          "your finish summary and let the run that started you decide."}
    if not _is_root_conversation(ctx):
        return _queued_obs(ctx, fields)
    return None


def handle_create_routine(ctx: RunContext, action: dict) -> dict:
    """Store/refresh the draft (first step) or materialize it (confirmed second step) in a
    root conversation; QUEUE the proposal for the operator anywhere else (F328). Returns the
    observation dict the loop records and renders.

    Action fields (all reused from the shared schema — no create_routine-only fields):
      target   — the new routine's kebab-case slug (required)
      name     — its human display name (required)
      prompt   — the clarified task instruction, decomposed into the routine's stages (required)
      workflow — the library workflow pattern to materialize from (optional; DEFAULT_WORKFLOW)
      stopping — what DONE looks like for one run, in the USER's words (optional); seeded into
                 the new routine's state/stopping.json as RUN-scoped conditions.
      goal — the state after which the ROUTINE is finished (optional); seeded into the same
             document as GOAL-scoped conditions, which retire the routine when met.
                 Part of the draft's identity, so
                 changing it restarts the confirmation like any other field.
    """
    slug = str(action.get("target") or "").strip()
    name = str(action.get("name") or "").strip()
    instruction = str(action.get("prompt") or "").strip()
    workflow_slug = str(action.get("workflow") or "").strip() or DEFAULT_WORKFLOW
    raw_stopping = action.get("stopping")
    stopping = [t.strip() for t in raw_stopping
                if isinstance(t, str) and t.strip()] if isinstance(raw_stopping, list) else []
    raw_goal = action.get("goal")
    goal = [t.strip() for t in raw_goal
            if isinstance(t, str) and t.strip()] if isinstance(raw_goal, list) else []
    server = ctx.server

    if (server.routines_home / slug).exists():
        return {"kind": "create_routine", "slug": slug, "already_exists": True}

    # F387: the pattern is checked against the LIVE library at draft time. The old flow
    # stored any string and only failed inside scaffold — after the user had confirmed.
    catalog = _catalog(server)   # library patterns + the always-present `generate` choice
    if workflow_slug not in {w["slug"] for w in catalog}:
        return _unknown_workflow_obs(slug, workflow_slug, catalog)

    fields = {"slug": slug, "name": name, "instruction": instruction,
              "workflow": workflow_slug, "stopping": stopping, "goal": goal}
    # A run with no user in the loop QUEUES instead of creating (F328). It is the same D92
    # draft, with a longer gap before the confirmation: the operator sees it on the Decisions
    # page and one click materializes it through this very scaffold path. Nothing is created
    # here, and the engine still never writes routine.yaml.
    if (elsewhere := _for_non_conversation(ctx, fields)) is not None:
        return elsewhere

    draft = _load_draft(ctx)
    if draft is None or any(draft.get(k) != v for k, v in fields.items()):
        # First step, or a design change: (re)write the draft and ask for the round-trip.
        record = {**fields, "pid": os.getpid(), "created_at": now_iso()}
        atomic_write_json(_draft_path(ctx), record)
        return _preview_obs(record, catalog, updated=draft is not None)
    if draft.get("pid") == os.getpid():
        # Same reply that drafted it — no user has seen the preview yet. Hold, teach.
        return _preview_obs(draft, catalog, updated=False, blocked_same_leg=True)

    # Confirmed: identical fields, a later leg — the user has spoken since the preview.
    return _materialize(ctx, slug=slug, name=name, instruction=instruction,
                        workflow_slug=workflow_slug, stopping=stopping, goal=goal)
