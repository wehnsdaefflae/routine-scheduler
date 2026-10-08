"""THEN-SCRIPT — a file change and the script that checks it, in ONE action.

NVIDIA SoL-Pi's Action Fusion (arXiv 2609.20519): an agent edits a file, then spends a whole
model turn asking to run the command that tests, builds or renders it. The pair is just as
common here. Of 3,459 `write_file`/`edit_file` actions in 160 fleet runs (2026-09-17..10-08),
1,089 were followed directly by a run of the file just changed — 536 of them a `script` — and
every turn re-reads the whole context (175k tokens on average). So `write_file` and `edit_file`
take an optional `then_script` — `["<name>", *args]` — and the engine runs that script once the
change has landed, returning both results in one observation.

Scoped to `script` on purpose. A routine's script is a BASE kind — no capability, no approval
dial — so the riding call passes exactly the gates it would pass alone, and each is applied to
it AS a script action (`riding`): `validate_action` (the workflow's `tools:` list and the
capability layer), the pre-execution HOLD seam (a reminder or rule on `script:<name> …` holds
the fused action — `hold.before_dispatch` asks about each part) and D39's call-time secret
gate. A util or a shell command would ride past every gate keyed on its own kind, which is why
neither may.

The change runs first and decides alone whether the script runs: a write or edit that failed
skips it, and the observation says so. A script that fails leaves the change in place — the
observation reports both, exactly as two separate actions would have.
"""

from __future__ import annotations

FIELD = "then_script"
#: The observation key that says the script was NOT run, and why.
SKIPPED = "then_script_skipped"
HOSTS = ("write_file", "edit_file")


def riding(action: dict) -> dict | None:
    """The script action riding this one, shaped exactly as if it had been emitted alone —
    which is what every gate it passes reads — or None.
    """
    argv = action.get(FIELD)
    if action.get("kind") not in HOSTS or not isinstance(argv, list) or not argv:
        return None
    return {"say": action.get("say", ""), "kind": "script", "name": str(argv[0]),
            "args": [str(a) for a in argv[1:]]}


def problems(obj: dict, allowed_kinds: set[str] | None, grants) -> list[str]:
    """`validate_action`'s check of the field: its shape, then the riding script validated as
    the script action it is — so a recipe whose `tools:` leave `script` out refuses it here,
    inside the schema-retry cycle, with the reason a bare `script` would get.
    """
    argv = obj.get(FIELD)
    if argv is None:
        return []
    script = riding(obj)
    if script is None or not all(isinstance(a, str) for a in argv) or not argv[0].strip():
        return [(f'kind={obj.get("kind")}: {FIELD} is ["<script-name>", ...args] — the name '
                 "of one of your scripts/ first, then its arguments, every item a string")]
    from .actions import validate_action
    return [f"{FIELD}: {p}" for p in validate_action(script, allowed_kinds, grants)]


def run(loop, action: dict, script: dict, ctx) -> dict:
    """The change, then — only when it landed — `script` (`riding(action)`) behind its
    call-time secret gate.

    The script's observation rides the change's under `FIELD`; a skipped one leaves `SKIPPED`
    instead. Rendering and `is_failure` read both (engine/observations.py).
    """
    from . import executor, secretgate
    from .loopconst import POLL_S
    from .observations import is_failure

    change = executor.dispatch({k: v for k, v in action.items() if k != FIELD}, ctx)
    if is_failure(change):
        return {**change, SKIPPED: f"the {action['kind']} did not land, so the script did not "
                                   "run — fix the change, then run the script"}
    return {**change, FIELD: secretgate.gate_script_secrets(loop, script, poll_s=POLL_S)
            or executor.do_script(script, ctx)}
