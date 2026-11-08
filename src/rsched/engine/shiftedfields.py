"""What a REJECTED action was, when the schema's own problem lines cannot say.

Split out of `engine/actions.py` (D185 step 1), which sits at its size ceiling: one subject —
reading a malformed action for what it really is — in one file. Two readers of one signature:

* `util_rejection_outcome` attributes a rejected util call for per-util telemetry (F546), and
  refuses to attribute one whose `name` could not be a util name at all, because `count_util`
  would write that string into the durable usage stream and the Stats tab would carry it as a
  permanent phantom util.
* `field_shift_diagnosis` names the whole-object FIELD SHIFT (D146-C) that the validator's
  per-field problem lines describe only as a symptom.

They share `could_be_util` deliberately: the guard and the diagnosis must not drift, or the
engine would count a value it refuses to explain, or explain one it still counts.
"""

from __future__ import annotations

import re

from .actionschema import ACTION_SCHEMA, KINDS, PSEUDO_UTILS

#: How much of a field's own contract description the correction quotes. Same bound and same
#: reason as `schemamemo.HELP_CHARS`: a correction that pastes a paragraph buries the one line
#: it exists to explain. Both read `ACTION_SCHEMA` at CALL time, so neither can drift from the
#: contract — that, not a shared constant, is what keeps the two corrections honest.
UNKIND_HELP_CHARS = 200

#: A name that could BE a util: the library's kebab-case rule, narrowed by the one thing
#: `ids.is_slug` does not require — at least one LETTER. Every util in the catalog satisfies
#: it; a bare timeout number, a path, a quoted JSON fragment and a paragraph of prose do not.
_NAMEABLE_UTIL_RE = re.compile(r"^(?=[a-z0-9-]*[a-z])[a-z0-9][a-z0-9-]*$")


def could_be_util(name: str) -> bool:
    """Could `name` name a util at all? The F546 test every per-util telemetry tick passes —
    a value that cannot is a field shift, and attributing it invents a permanent phantom row
    on the Stats tab — and the signature `field_shift_diagnosis` names.
    """
    return bool(_NAMEABLE_UTIL_RE.match(name))


def util_rejection_outcome(obj: dict, allowed_kinds: set[str] | None = None,
                           grants=None) -> tuple[str, str] | None:
    """Classify a REJECTED util action for per-util telemetry (RunContext.count_util):
    returns (util name, "denied" | "rejected") or None when the rejection is not
    attributable to a util. "denied" = a permission refusal (a reserved util switched
    off, the util kind excluded by the workflow's tools:) — Mark's "permission problem";
    "rejected" = a malformed call (schema/field problems). A denial never reaches the
    executor — it is corrected inside the schema-retry cycle and never becomes a turn —
    so it MUST be counted here at the validation seam or it would never be counted at
    all. The catalog pseudo-utils (PSEUDO_UTILS) are discovery, not execution: skipped.
    """
    name = str(obj.get("name") or "").strip()
    if obj.get("kind") != "util" or not name or name in PSEUDO_UTILS:
        return None
    # …and the name must be able to BE a util. A malformed action is by definition one whose
    # fields cannot be trusted, and the commonest malformation is a FIELD SHIFT — values
    # sliding into the wrong keys, so `name` holds prose, a timeout number or a path. That
    # string is not merely wrong here: count_util writes it to status.json, the DURABLE
    # workflow-usage stream carries it, and readmodels/util_stats builds its rows from
    # `set(catalog) | set(merged)` — so it becomes a permanent phantom util on the Stats tab
    # (~25 by 2026-09-24, incl. a paragraph of prose and a bare `300`; the specimen is
    # weightloss:20260923-220004, served by a fallback model after a 503 and a 402).
    #
    # The test is the util NAMING rule, not the slug rule: `ids.is_slug` admits `300` and
    # `c-20260821-060305` (digits and run ids are legal slugs), and the policy carries no
    # catalog of util names (write_util's create-vs-revise split asks the library per call),
    # so neither separates a name from a shifted value. What every real util name has and no
    # shifted value did: kebab-case with a LETTER in it. Unattributable IS what None means.
    if not could_be_util(name):
        return None
    denied = ((allowed_kinds is not None and "util" not in allowed_kinds)
              or (grants is not None and grants.deny(obj) is not None))
    return name, ("denied" if denied else "rejected")


def field_shift_diagnosis(obj: dict) -> str:
    """Name a whole-object FIELD SHIFT when the schema's own problem lines cannot (D146-C).

    A field shift is a degraded model's characteristic failure — values sliding one key over —
    and most of a shifted action stays schema-VALID, so the validator reports whichever field
    happened to carry a constraint. weightloss:20260923-220004 was told
    `timeout_s: 0 is less than the minimum of 1` eight times while the actual candidates were
    `kind='util'` with `name='300'`, `name='180'` and a whole sentence of prose in `name`. The
    correction described the symptom; the fault went unnamed, and the run concluded the failure
    was its own inability to hold the schema.

    The detectable signature is the same one F546's telemetry guard uses — `could_be_util`,
    the util naming rule — so the two cannot drift: a `kind: "util"` whose `name` could not be
    a util name at all is not a wrong util, it is a misplaced value. Returns "" when there is
    nothing specific to say, because a diagnosis that fires on ordinary mistakes would teach
    the model to ignore it.
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


def unkind_diagnosis(obj: dict) -> str:
    """What a `kind` that is NOT a kind really was — "" when nothing specific can be said.

    `kind` is the one field whose rejection the enum cannot answer: the validator lists all 34
    current kinds and the thing the run actually wanted is not among them, so the correction
    has to name the OTHER place that word belongs. `renamedkinds.hint` already does this for a
    kind that was renamed; these are the two kinds that never existed, measured on 2026-11-07
    in one evening, one turn each:

    * a FIELD used as a kind — `tv-show-tracker-seedbox-manager:20261107-170000` turn 27 sent
      `kind: "note"`. `note` rides any action and costs no turn, so the correction is that the
      intent was a rider, not an action.
    * a UTIL used as a kind — `deficit-coach:20261107-230003` turn 10 sent
      `kind: "code-search"`. The shape is `kind="util", name="<util>"`.

    The util branch rests on a property of the vocabulary, asserted by its test: NO action kind
    contains a hyphen. The field branch reads `ACTION_SCHEMA` at call time, so a field renamed
    in the contract cannot leave this pointing at one that is gone — and a word that is both a
    real kind and a field name (`delete`) is never diagnosed, because the first test here is
    that the kind is unknown. Returns "" for anything else: a diagnosis that fires on ordinary
    mistakes teaches the model to ignore it (`field_shift_diagnosis` says the same).

    WHY THE THREE DIAGNOSES CANNOT OVERLAP, which is what lets `completion.diagnose` compose
    them in reading order rather than choosing between them: a renamed kind is in
    `renamedkinds.RENAMED` (so it IS a known word with a replacement), an unkind is by its first
    test NOT in `KINDS`, and a field shift requires `kind == "util"` — a valid kind. At most one
    of the three can have anything to say about any one object.
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
    if could_be_util(kind) and "-" in kind:
        return (f"`{kind}` LOOKS LIKE A UTIL, NOT A KIND — no action kind contains a hyphen. "
                f'To run it: kind="util", name="{kind}", args=[…]. If no util of that name '
                f'exists, `util name=list args=["{kind}"]` shows its usage, or '
                f"`util name=search` finds one by need.")
    return ""
