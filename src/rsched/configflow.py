"""Config edited while a run is LIVE — the one table that says what reaches it (F337).

A run reads `routine.yaml` at boot and composes its prompt once. Config edited while it is
running therefore lands on disk with the run unaware of it — except for a handful of fields the
system had grown ad-hoc live paths for (an access-request decision bridges into the live policy
via `engine/requests.py`; the rules picker pushes an added rule through `control.json`). So "I
changed it while it was running" had TWO different meanings depending on which field was
touched, and the run was never told either way.

The fix is not more live paths. It is **one classification, in one place**, plus telling the run:

- `LIVE` — the engine can adopt this at a turn boundary, and does.
- `NEXT_RUN` — it cannot, and says so plainly rather than leaving the operator to guess.

Every field of `RoutinePatch` and `ConversationPatch` must appear in `CLASSIFICATION` below;
`tests/test_configflow.py` fails on a field that does not. That is the whole anti-drift
mechanism: a new config field cannot be added without declaring which half it is in, so the
silent divergence this module exists to end cannot quietly come back.

The delivery seam is the one that already exists for reaching a running run — a signal in the
run's `control.json`, applied at the next turn boundary by `engine/control.apply_config_change`,
which appends an ENGINE NOTE and records a `user_injection` transcript event. So the change is
IN the conversation the model can see, never a second invisible mutation path.
"""

from __future__ import annotations

LIVE = "live"
NEXT_RUN = "next_run"

#: Every PATCH field → (half, why). The `why` is not decoration: it is what the operator reads
#: in the message, so it must say what the run actually does with the field.
CLASSIFICATION: dict[str, tuple[str, str]] = {
    # ---- adopted at the next turn boundary -------------------------------------------------
    "budgets": (LIVE, "the run's remaining window is re-derived from the new ceilings"),
    "deliberation": (LIVE, "the say contract changes from the next turn"),
    "grants": (LIVE, "the policy is rebuilt and the action schema re-projected"),

    # ---- takes effect at the next run ------------------------------------------------------
    "enabled": (NEXT_RUN, "it governs whether the daemon FIRES, not a run already going"),
    "schedule": (NEXT_RUN, "it governs when the next run starts"),
    "models": (NEXT_RUN, "the transport is bound per turn, but swapping a model mid-run is the "
                         "run page's model-switch control, not a config edit"),
    "connections": (NEXT_RUN, "OAuth tokens are injected into the util environment at boot"),
    "machines": (NEXT_RUN, "the machine bindings and share mounts are resolved at boot"),
    "rules": (NEXT_RUN, "prose already in the context cannot be unsaid; the /rules picker "
                        "pushes an ADDED rule to a live run, a config patch does not"),
    "fs_read_roots": (NEXT_RUN, "the sandbox roots are computed at boot and passed to every "
                                "util subprocess; an access-REQUEST decided mid-run does reach "
                                "the run, a config edit does not"),
    "fs_write_roots": (NEXT_RUN, "the sandbox jail is built at boot from these roots and "
                                 "handed to every util subprocess; widening it mid-run would "
                                 "not reach the jails already created"),
    "keep_runs": (NEXT_RUN, "retention is applied after a run, never during one"),
    "improve": (NEXT_RUN, "it is read by the improver, not by the run"),
    "workflow": (NEXT_RUN, "the recipe was decomposed into the prompt at boot"),
    "name": (NEXT_RUN, "a label, not behaviour"),
    "description": (NEXT_RUN, "a label, not behaviour"),
    "tags": (NEXT_RUN, "a label, not behaviour"),
    "title": (NEXT_RUN, "a label, not behaviour"),
    "workdir": (NEXT_RUN, "it is the first write root, computed at boot with the other roots"),
}

#: Only these carry a VALUE the engine adopts; the rest of LIVE would be meaningless to ship.
ADOPTABLE = tuple(f for f, (half, _) in CLASSIFICATION.items() if half == LIVE)


def classify(fields: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split the PATCH's fields into (live, next_run, unknown), preserving order.

    `unknown` should always be empty — the test above guarantees it for declared model fields —
    but it is returned rather than swallowed so an undeclared field reads as UNKNOWN to the run
    instead of silently as "next run", which is the failure mode in miniature.
    """
    live: list[str] = []
    later: list[str] = []
    unknown: list[str] = []
    for f in fields:
        half = CLASSIFICATION.get(f, (None, ""))[0]
        (live if half == LIVE else later if half == NEXT_RUN else unknown).append(f)
    return live, later, unknown


def _render(field: str, values: dict) -> str:
    why = CLASSIFICATION.get(field, ("", "it is not classified — treat it as next-run"))[1]
    val = values.get(field)
    shown = "" if val is None else f" → {val}"
    return f"- {field}{shown}: {why}"


def change_note(fields: list[str], values: dict) -> str:
    """The ENGINE NOTE a live run is given for a config PATCH — what changed, and for each
    field whether it is in effect NOW or at the next run. Empty when nothing was patched.

    Naming every field, not just the adopted ones, is the point: the complaint F337 records is
    not that some fields wait, it is that the run was never told which did.
    """
    live, later, unknown = classify(fields)
    if not (live or later or unknown):
        return ""
    parts = ["the user changed this routine's configuration while you are running."]
    if live:
        parts.append("IN EFFECT NOW, from this turn on:\n"
                     + "\n".join(_render(f, values) for f in live))
    if later:
        parts.append("Saved, but it takes effect at your NEXT RUN — you are still running under "
                     "what you booted with:\n" + "\n".join(_render(f, values) for f in later))
    if unknown:
        parts.append("Changed, but not classified as live or next-run — assume it takes effect "
                     "at your next run: " + ", ".join(unknown))
    return " ".join(parts[:1]) + "\n\n" + "\n\n".join(parts[1:])
