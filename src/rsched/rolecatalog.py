"""Does this instance still SERVE the model names a routine's roles store? — the catalog question
the loader cannot ask, because it cannot see the catalog.

The write edges DO check (`web/config_fields.validate_models` refuses a role naming a
non-catalog model), but a stored name goes stale LATER, when the catalog is renamed underneath a
file nobody edited. That is F643: `tv-show-tracker-seedbox-manager` and `library-sync` both
carried a bare `Sonnet` taken nine weeks earlier, when it was a catalog name; after the rename to
`Sonnet medium`/`Sonnet high` each routine died at boot — `EndpointError: model 'Sonnet' is not
in the catalog` out of `resolve`, rc=1, no finish, no summary, an `orphaned_run` and nothing else.

Three readers ask it: the runner refuses a FIRE on a stale `main` (`daemon/runner.py`,
`FIRE_BLOCKING_ROLES`), `rsched validate` names every stale role (`cli_validate.py`), and the
routine page's problem panel says the same (`config_problems`, `web/api_routines.py`).
"""

from __future__ import annotations

from .config import MODEL_KINDS, RoutineConfig, ServerConfig


def roles_problem(server: ServerConfig, models: dict[str, str] | None) -> str:
    """Which of `models`' names this instance cannot serve, as one line, or "".

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


def config_problems(server: ServerConfig, cfg: RoutineConfig) -> list[str]:
    """What a routine's STANDING model roles cost it on THIS instance, for the routine page's
    problem panel — `[]` when every chat role names a catalog model.

    Only the CHAT roles are judged. `decision`/`decision_media` sit in the same `models:` map but
    resolve against the DECISION catalog (config/decisionconf.py), so measuring them against
    `server.models` would call a working decision model a run-killer — a false alarm on the one
    panel that has to be believed.
    """
    chat = {role: name for role, name in (cfg.models or {}).items() if role in MODEL_KINDS}
    return [line] if (line := roles_problem(server, chat)) else []
