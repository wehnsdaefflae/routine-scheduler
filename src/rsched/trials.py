"""MODEL TRIALS — "run this routine on catalog model X for its next N fires" (operator,
2026-10-08): the measurement behind "is this routine's model overkill, or not enough?".

A trial is ordinary config: `trial: {id, models, runs, reason}` in routine.yaml
(`config/trialconf.py`), proposed like any other change — a run files it on an `ask_user` as a
`config_patch` and the operator's one click on the Decisions page applies it; no run ever writes
it. Everything after that click is DERIVED, and nothing writes config again:

- **Active** while fewer than `runs` of the routine's runs have RECORDED it: depth-0 usage
  records, folded per run (`readmodels/usage_stream.usage_runs`), whose `fingerprint.trial` is
  the trial's id (engine/runrecord.py). The count is the durable stream's, so a restart or a
  retention sweep loses none, and a fire that never reached the engine (a run gate's skip, a
  launch that failed) fakes none.
- **Armed per fire.** While active, EVERY fire of the routine — schedule, lane, trigger, manual,
  catch-up — gets `trial.json` (`{id, models, runs}`) in its new run dir before the engine
  exists (`arm`, from `daemon/runner.Runner.fire`, beside the brief), and the runner names the
  trial's models to `engine-run` as `--model role=name` (`overrides`). A resume reads the run's
  OWN `trial.json`, so a run keeps the trial it started under, even past the trial's end.
- **Finished** once `runs` runs recorded it: the trial simply stops applying. The field stays
  in routine.yaml as history until the operator's next accepted change clears it
  (`{"trial": null}`) or replaces it; the routine page links the results (`#/changes/<slug>`).
- **Ignored** while a model it names is not in the catalog (`catalog_problem`): applied, the
  run would die on its first turn resolving a model nobody serves. The routine page and
  `rsched validate` say so; the PATCH edge and the filing check refuse such a trial outright.

`rsched run-once` is a developer's run and arms nothing — it takes `--model` itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import RoutineConfig, ServerConfig, TrialConfig
from .engine.runrecord import TRIAL_FILE
from .paths import atomic_write_json, read_json
from .readmodels.incarnations import live_runs
from .readmodels.usage_stream import usage_runs

log = logging.getLogger("rsched.trials")

ACTIVE, FINISHED, IGNORED = "active", "finished", "ignored"


def catalog_problem(server: ServerConfig, trial: TrialConfig | None) -> str:
    """Why `trial` cannot be applied on this instance, or "" — every name must be a catalog
    model. One clause, which each caller finishes with what it costs THERE (a 400, a filing
    refusal, a trial ignored at every fire — `ignored_problem`).
    """
    if trial is None:
        return ""
    missing = [f"models.{role} {name!r}" for role, name in sorted(trial.models.items())
               if name not in server.models]
    if not missing:
        return ""
    verb = "is" if len(missing) == 1 else "are"
    return f"trial {trial.id}: {', '.join(missing)} {verb} not in the model catalog"


def ignored_problem(server: ServerConfig, trial: TrialConfig | None) -> str:
    """The routine-level problem line for a trial the catalog cannot serve, or ""."""
    problem = catalog_problem(server, trial)
    return (f"{problem} — the trial is ignored at every fire until it names catalog models "
            "or is cleared") if problem else ""


def roles_problem(server: ServerConfig, models: dict[str, str] | None) -> str:
    """The same question as `catalog_problem`, asked of a routine's STANDING model roles:
    which of `models`' names this instance cannot serve, or "".

    Here for the reason this module exists at all — the loader cannot see the catalog, so a
    stored model name is checked only by callers that can. The write edges DO check
    (`web/config_fields.validate_models` refuses a role naming a non-catalog model), but a
    stored name goes stale LATER, when the catalog is renamed underneath a file nobody edited.
    That is F643: `tv-show-tracker-seedbox-manager` and `library-sync` both carried a bare
    `Sonnet` taken nine weeks earlier, when it was a catalog name; after the rename to
    `Sonnet medium`/`Sonnet high` each routine died at boot — `EndpointError: model 'Sonnet' is
    not in the catalog` out of `resolve`, rc=1, no finish, no summary, an `orphaned_run` and
    nothing else. A role is worse than a trial here: a trial that cannot be served is merely
    ignored, while `main` resolves on the first turn of every run.

    The uncensored role is included — it resolves the moment a refusal is referred, which is
    rarer than turn one and so stays broken longer unnoticed.
    """
    missing = [f"models.{role} {name!r}" for role, name in sorted((models or {}).items())
               if isinstance(name, str) and name not in server.models]
    if not missing:
        return ""
    verb = "is" if len(missing) == 1 else "are"
    return (f"{', '.join(missing)} {verb} not in the model catalog — the run dies resolving it "
            "(Settings → Models names what this instance serves)")


def recorded(routines_home: Path, slug: str, trial_id: str) -> int:
    """How many of `slug`'s runs carry `trial_id` in their durable record — runs, not legs, and
    the live routine's own (readmodels/incarnations.py).
    """
    return sum(1 for rec in live_runs(routines_home, usage_runs(routines_home), slug)
               if not rec.get("depth") and isinstance(rec.get("fingerprint"), dict)
               and rec["fingerprint"].get("trial") == trial_id)


def state(server: ServerConfig, cfg: RoutineConfig) -> dict | None:
    """The trial as the routine page reads it — its config plus `recorded` (runs so far),
    `state` (active · finished · ignored) and `problem` — or None when there is none.
    """
    trial = cfg.trial
    if trial is None:
        return None
    problem = ignored_problem(server, trial)
    done = recorded(server.routines_home, cfg.slug, trial.id)
    status = IGNORED if problem else FINISHED if done >= trial.runs else ACTIVE
    return {**trial.model_dump(), "recorded": done, "state": status, "problem": problem}


def arm(server: ServerConfig, cfg: RoutineConfig, run_dir: Path) -> TrialConfig | None:
    """Write `trial.json` into a NEW run dir when the routine's trial is active, and return
    the trial; None (and nothing written) otherwise. Called before the engine exists.
    """
    trial, now = cfg.trial, state(server, cfg)
    if trial is None or now is None:
        return None
    if now["state"] == IGNORED:
        log.warning("trial_ignored routine=%s %s", cfg.slug, now["problem"])
    if now["state"] != ACTIVE:
        return None
    atomic_write_json(Path(run_dir) / TRIAL_FILE,
                      {"id": trial.id, "models": dict(trial.models), "runs": trial.runs})
    log.info("trial_armed routine=%s trial=%s run %d of %d", cfg.slug, trial.id,
             now["recorded"] + 1, trial.runs)
    return trial


def overrides(run_dir: Path) -> dict[str, str]:
    """The role → catalog-name overrides the run dir's `trial.json` carries — what the runner
    names to `engine-run` as `--model`. {} for a run that is no trial run.
    """
    doc = read_json(Path(run_dir) / TRIAL_FILE)
    models = doc.get("models") if isinstance(doc, dict) else None
    return ({str(role): str(name) for role, name in models.items()}
            if isinstance(models, dict) else {})
