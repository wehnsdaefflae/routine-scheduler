"""THEN-SCRIPT — a file change and the script that checks it, in ONE action.

SoL-Pi's Action Fusion: an edit is commonly followed by a whole model turn spent running the
file just changed. So `write_file` and `edit_file` take an optional `then_script` —
`["<name>", *args]` — run once the change has landed, both results in one observation.

Scoped to `script` on purpose: the riding call passes every gate AS a script action
(`riding`) — `validate_action` (the `tools:` list, the capability layer), the pre-execution
HOLD seam (`hold.before_dispatch` asks about each part) and D39's call-time secret gate. A util
or a shell command would ride past every gate keyed on its own kind.

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
    if obj.get("background"):
        # The background thread dispatches the KIND, not this fusion (which lives in
        # `actionroute._route`), so a deferred pair would drop the script silently with its
        # secret gate never asked. Refused here rather than half-performed in a thread.
        return [(f'kind={obj.get("kind")}: {FIELD} cannot ride a backgrounded change — the '
                 "background thread performs the change only, so the script would be dropped "
                 "without running and its secret gate never asked. Either drop 'background' "
                 "and keep the pair in one action, or defer the change alone and run the "
                 "script as its own action once the write has landed")]
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
