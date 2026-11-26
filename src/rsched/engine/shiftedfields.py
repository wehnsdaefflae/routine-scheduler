"""What a REJECTED action was, when the schema's own problem lines cannot say.

Two readers of one signature:

* `util_rejection_outcome` attributes a rejected util call for per-util telemetry, and refuses
  one whose `name` could not be a util name at all — it would become a permanent phantom util
  in the durable usage stream and on the Stats tab.
* `field_shift_diagnosis` names the whole-object FIELD SHIFT (D146-C) that the validator's
  per-field problem lines describe only as a symptom.

They share `could_be_util` deliberately, so the guard and the diagnosis cannot drift.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import utils_lib
from .actionschema import ACTION_SCHEMA, KINDS, PSEUDO_UTILS


def _catalog_home(loop) -> Path | None:
    """The library home whose `utils/` is this instance's REAL util catalog, or None.

    Best-effort: this runs inside a schema REJECTION, which must never raise, and the loop is
    optional, so a caller with no live run still gets the heuristic.
    """
    try:
        return Path(loop.ctx.server.libraries_home)
    except Exception:
        return None

#: How much of a field's own contract description the correction quotes (as
#: `schemamemo.HELP_CHARS`): a pasted paragraph buries the one line it exists to explain.
UNKIND_HELP_CHARS = 200

#: A name that could BE a util: kebab-case with at least one LETTER (which `ids.is_slug` does
#: not require). A bare timeout number, a path or a paragraph of prose does not match.
_NAMEABLE_UTIL_RE = re.compile(r"^(?=[a-z0-9-]*[a-z])[a-z0-9][a-z0-9-]*$")


def could_be_util(name: str) -> bool:
    """Could `name` name a util at all? A value that cannot is a field shift: never counted
    in per-util telemetry, and named by `field_shift_diagnosis`.
    """
    return bool(_NAMEABLE_UTIL_RE.match(name))


def util_rejection_outcome(obj: dict, allowed_kinds: set[str] | None = None,
                           grants=None) -> tuple[str, str] | None:
    """Classify a REJECTED util action for per-util telemetry (RunContext.count_util):
    returns (util name, "denied" | "rejected") or None when the rejection is not
    attributable to a util. "denied" = a permission refusal (a reserved util switched
    off, the util kind excluded by the workflow's tools:); "rejected" = a malformed call.
    A denial never reaches the executor, so it MUST be counted here at the validation
    seam. The catalog pseudo-utils (PSEUDO_UTILS) are discovery, not execution: skipped.
    """
    name = str(obj.get("name") or "").strip()
    if obj.get("kind") != "util" or not name or name in PSEUDO_UTILS:
        return None
    # …and the name must be able to BE a util: a FIELD SHIFT puts prose, a timeout number or
    # a path in `name`, and counting it would write a permanent phantom util into the durable
    # usage stream. The util NAMING rule, not the slug rule (`ids.is_slug` admits `300`).
    if not could_be_util(name):
        return None
    denied = ((allowed_kinds is not None and "util" not in allowed_kinds)
              or (grants is not None and grants.deny(obj) is not None))
    return name, ("denied" if denied else "rejected")


def field_shift_diagnosis(obj: dict) -> str:
    """Name a whole-object FIELD SHIFT when the schema's own problem lines cannot (D146-C).

    A field shift is a degraded model's characteristic failure — values sliding one key over —
    and most of a shifted action stays schema-VALID, so the validator reports whichever field
    happened to carry a constraint: the symptom, not the fault.

    The signature is `could_be_util`, shared with the telemetry guard: a `kind: "util"` whose
    `name` could not be a util name is a misplaced value. Returns "" when there is nothing
    specific to say — a diagnosis that fires on ordinary mistakes teaches the model to ignore it.
    """
    if str(obj.get("kind") or "") != "util":
        return ""
    name = str(obj.get("name") or "").strip()
    if not name or could_be_util(name):
        return ""
    shown = name if len(name) <= 80 else name[:77] + "…"
    return (f"LOOK AT THE WHOLE OBJECT, not only the field named above: `name` holds "
            f"{shown!r}, which cannot be a util name (utils are kebab-case and contain a "
            f"letter). That is the signature of a FIELD SHIFT — your values are one key out "
            f"of place, and most of the object is still schema-valid, so the problem listed "
            f"above is a symptom rather than the fault. Rebuild the action from scratch: "
            f"`name` is the util's name, `args` is a list of strings, `timeout_s` is a number "
            f"of seconds. Do not patch the one field you were told about.")


def unkind_diagnosis(obj: dict, loop=None) -> str:
    """What a `kind` that is NOT a kind really was — "" when nothing specific can be said.

    `kind` is the one field whose rejection the enum cannot answer, so the correction names
    the OTHER place that word belongs (`renamedkinds.hint` covers a retired kind):

    * a FIELD used as a kind (`kind: "note"`) — the intent was a rider, not an action.
    * a UTIL used as a kind (`kind: "code-search"`) — the shape is `kind="util", name=…`.

    The util branch asks the INSTANCE'S OWN CATALOG first (`utils_lib.exists`), and only falls
    back to the hyphen rule — NO action kind contains a hyphen — for a name no util carries;
    many utils have no hyphen. The field branch reads `ACTION_SCHEMA` at call time, and a word
    that is both a real kind and a field name (`delete`) is never diagnosed. Returns "" for
    anything else.

    The three diagnoses cannot overlap, so `completion.diagnose` composes them in reading
    order: a retired kind is in `renamedkinds.RETIRED`, an unkind is NOT in `KINDS`, and a
    field shift requires `kind == "util"`.
    """
    kind = str(obj.get("kind") or "").strip()
    if not kind or kind in KINDS:
        return ""
    if (prop := ACTION_SCHEMA.get("properties", {}).get(kind)) is not None:
        text = str((prop or {}).get("description") or "").strip()
        head = text.split(". ")[0].rstrip(".")
        quoted = (head if len(head) <= UNKIND_HELP_CHARS
                  else head[: UNKIND_HELP_CHARS - 1].rstrip() + "…")
        said = f' The contract says of it: "{quoted}".' if quoted else ""
        return (f"`{kind}` IS A FIELD, NOT A KIND. It is a property an action CARRIES, so it "
                f"belongs beside `kind`, never in it.{said} Re-send the action whose kind is "
                f"the thing you are actually doing, with `{kind}` as one of its fields.")
    if could_be_util(kind):
        if (home := _catalog_home(loop)) is not None and utils_lib.exists(home, kind):
            return (f"`{kind}` IS A UTIL, NOT A KIND — it is in this instance's catalog. "
                    f'To run it: kind="util", name="{kind}", args=[…]. '
                    f'`util name=list args=["{kind}"]` shows its exact usage first.')
        if "-" in kind:
            return (f"`{kind}` LOOKS LIKE A UTIL, NOT A KIND — no action kind contains a "
                    f'hyphen. To run it: kind="util", name="{kind}", args=[…]. If no util of '
                    f'that name exists, `util name=list args=["{kind}"]` shows its usage, or '
                    f"`util name=search` finds one by need.")
    return ""
