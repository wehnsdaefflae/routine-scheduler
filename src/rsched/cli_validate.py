"""`rsched validate` — is the config on THIS instance well-formed, coherent and FLYABLE?

Split out of `cli.py` (over the repo's size bar, so it may only shrink — the same split F746
made for `abort`/`drain` into `cli_runctl.py`). It is one command but four independent
questions, which is why it outgrew its home:

1. is every file well-formed (`load_routine`'s own problems)?
2. do the stored MODEL NAMES still exist in the catalog (`trials` — the loader cannot see it)?
3. can each role's chain actually FAIL OVER (`conformance`, R2196)? — and is its claim verifier
   a different model from the one it judges (`verifier_model`, a NOTE)?
4. is the SETUP coherent (`readmodels.surface`) and does the RECIPE still hold together
   (`workflows.recipelint`) — reported, never fatal?

Only a blocking row fails the command: an interrupt, a note or a recipe phrasing is reported and
leaves the exit code green, because a check that turned a phrasing into a failed command would be
switched off inside a week.
"""

from __future__ import annotations

from .config import load_server_config
from .paths import expand


def _routine_dir(server, slug_or_path: str):
    p = expand(slug_or_path)
    if p.is_dir() and (p / "routine.yaml").exists():
        return p
    return server.routines_home / slug_or_path


def cmd_validate(args) -> int:
    from .config import load_routine

    server, sproblems = load_server_config()
    total = list(sproblems)
    for line in total:
        print(f"server config: {line}")
    targets = ([_routine_dir(server, args.routine)] if args.routine else
               sorted(p for p in server.routines_home.iterdir()
                      if p.is_dir() and not p.name.startswith("."))
               if server.routines_home.is_dir() else [])
    from .conformance import chain_problems
    from .readmodels.remedies import surface_lines
    from .readmodels.surface import routine_surface
    from .trials import ignored_problem, roles_problem
    from .verifier_model import shared_verifier_problem
    from .workflows.recipelint import recipe_notes

    for d in targets:
        cfg, problems = load_routine(d)
        # A model trial naming a model the catalog lacks is ignored at every fire — the loader
        # cannot see the catalog, so it is said here (rsched/trials.py)
        if cfg and (trial_problem := ignored_problem(server, cfg.trial)):
            problems.append(trial_problem)
        # And the STANDING roles, for the same reason and from the same module: a stored name
        # goes stale when the catalog is renamed underneath it, and the write edge that checked
        # it is long past (F643 — two runs died at boot on a `Sonnet` that was valid when taken)
        if cfg and (role_problem := roles_problem(server, cfg.models)):
            problems.append(role_problem)
        # …and whether each role's chain can actually FAIL OVER (R2196, rsched/conformance.py):
        # a name that resolves still ends the run if it has no fallback, or if every member of
        # its chain shares one credential — the 2026-09-14 shape, checkable with no network call.
        # REPORTED, NOT COUNTED, like the setup notes and the recipe notes below: whether a
        # single-domain chain is acceptable is a judgement about THIS instance's providers (a
        # one-provider instance has no other domain to reach), and a check that turned that
        # judgement into a failed command would be switched off inside a week. Making it fatal
        # is the operator's call, filed as a decision by the run that built this.
        chains = chain_problems(server, cfg.models) if cfg else []
        # …and whether its claim verifier is a second model at all (rsched/verifier_model.py)
        judge = shared_verifier_problem(server, cfg.models) if cfg else None
        # Setup COHERENCE is a second, independent question from "is the file well-formed":
        # a routine can parse perfectly and still hold a rule that tells it to publish into a
        # directory it cannot write. Only a blocking row fails the command — an interrupt or a
        # note is reported and does not turn the exit code red.
        lines: list[str] = []
        if cfg:
            try:
                lines = surface_lines(routine_surface(server, cfg))
            except (OSError, ValueError) as exc:      # a broken library must not fail validate
                lines = [f"NOTE  setup surface unavailable: {exc}"]
        # And a third question: does the RECIPE still hold together (F516)? The config can be
        # perfect and the setup complete while main.md routes to a stage somebody renamed. It
        # is reported and never counted — a recipe is a hand-tuned document, and a check that
        # turned a phrasing into a failed command would be switched off inside a week.
        recipe: list[str] = []
        if cfg:
            recipe = recipe_notes(d, cfg.capabilities)
        blocking = [ln for ln in lines if ln.startswith("FAIL")]
        status = "ok" if cfg and not problems and not blocking else "PROBLEMS"
        print(f"{d.name}: {status}")
        for pr in problems:
            print(f"  - {pr}")
        for ln in lines:
            print(f"  {ln}")
        for ln in chains:
            print(f"  NOTE  failover · {ln}")
        if judge:
            print(f"  NOTE  verifier · {judge}")
        for ln in recipe:
            print(f"  NOTE  recipe · {ln}")
        total.extend(problems)
        total.extend(blocking)
    for line in _instance_problems(server):
        print(f"instance: {line}")
    return 1 if total else 0


def _instance_problems(server) -> list[str]:
    """Coherence checks that belong to no single routine, so no routine's surface can see them.

    A LANE is the case twice over. A scheduled lane with no members fires nothing on every tick
    of its cron, forever, leaving a `lane_chain_done: 0 member runs` in the health stream that
    reads exactly like a lane whose members all completed. Two of them had been running empty
    for weeks. And a lane naming a slug that is not a routine is a DANGLING REFERENCE: routines
    are deleted out of band, so no cascade removes them from the store, the chain logs a
    warning and skips them, and the only other trace is an `unknown routine(s)` the console
    used to raise over the WHOLE membership (F442).

    Reported, never fatal — an empty lane is a normal intermediate state while you are building
    one; a phantom member is stale bookkeeping.
    """
    from . import lanes as lanes_mod
    from .conformance import system_model_problems

    # The server's own `system_model` is the fallback behind every role a routine leaves unset,
    # so an unresolvable or single-domain one is a FLEET condition no routine's surface can see.
    model = system_model_problems(server)
    try:
        all_lanes = lanes_mod.list_lanes(server.routines_home)
    except OSError:
        return model
    live = ({p.name for p in server.routines_home.iterdir() if (p / "routine.yaml").is_file()}
            if server.routines_home.is_dir() else set())
    empty = [f"lane {ln['name']!r} ({ln['id']}) has a schedule ({ln['cron']!r}) but no members "
             "— it fires nothing on every tick"
             for ln in all_lanes if ln.get("cron") and not lanes_mod.member_slugs(ln)]
    phantom = [f"lane {ln['name']!r} ({ln['id']}) names {slug!r}, which is not a routine — "
               "the chain skips it every fire; remove it from the lane"
               for ln in all_lanes for slug in lanes_mod.member_slugs(ln) if slug not in live]
    return model + empty + phantom
