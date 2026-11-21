"""An `ask_user`'s optional PROOF — checked where the action is, and named back to the run (D220).

`rsched/proofs.py` owns the vocabulary and the state machine, `rsched/proofcheck.py` the predicate
registry and the daily sweep. This module is the ASKING side, the one seam an ask's `proof` field
crosses on its way to `file_question(..., proof=…)`:

- `problems` — the GRAMMAR, called from `askfields` for the single ask and for every entry of a
  `questions` batch. A proof is keyed by option, so an ask without options cannot carry one, and
  `proofs.problems` already says, per option, what a filed proof must name. One check is the
  registry's own: `config_patch_live` reads the `config_patch` the decision proposed, so a
  question carrying no patch would be checked forever negatively — refused here, where the run
  can still fix it, instead of reading as an unexpected event on the operator's page later.
- `note` — what the run is told once the record is filed. A predicate the registry does not
  know is NOT refused (`proofcheck`'s rule: it is a check only that routine can compute, from its
  own state), but the sweep leaves that option's state alone, so the run must hear that nobody
  else will check it. A filed proof whose every predicate is known is said in one clause.
"""

from __future__ import annotations

from .. import proofcheck, proofs

#: The one predicate whose evidence is the decision's own `config_patch`.
_NEEDS_PATCH = "config_patch_live"


def _entries(spec: object) -> list[dict]:
    return [e for e in spec.values() if isinstance(e, dict)] if isinstance(spec, dict) else []


def problems(spec: object, options: object, *, has_patch: bool, where: str) -> list[str]:
    """Why this ask's `proof` may not be filed — [] when it may, or when there is none."""
    if not spec:          # absent, or the `{}` a constrained decoder pads every field with
        return []
    picks = [str(o) for o in options] if isinstance(options, list) else []
    if not picks:
        return [f"{where}: 'proof' is keyed by OPTION — give the question its 'options' first"]
    out = [f"{where}: {p}" for p in proofs.problems(spec, picks)]
    if not has_patch and any(str(e.get("predicate") or "").strip() == _NEEDS_PATCH
                             for e in _entries(spec)):
        out.append(f"{where}: predicate {_NEEDS_PATCH!r} checks that the decision's "
                   "'config_patch' is live — this question carries none")
    return out


def unknown(spec: object) -> list[str]:
    """The predicates this proof names that the instance's registry cannot run, in order."""
    named = [str(e.get("predicate") or "").strip() for e in _entries(spec)]
    return list(dict.fromkeys(p for p in named if p and not proofcheck.known(p)))


def note(spec: object) -> str:
    """The observation's PROOF clause for a filed record, or "" when the ask carried none."""
    if not isinstance(spec, dict) or not spec:
        return ""
    if missing := unknown(spec):
        return (f" PROOF: {', '.join(f'`{p}`' for p in missing)} is not a predicate this "
                f"instance checks (it checks {', '.join(sorted(proofcheck.PREDICATES))}), so the "
                "daily sweep leaves that option's proof alone: once it is chosen, show the work "
                "landed yourself — the decision stays open until a proof is positive or the "
                "operator overrides it.")
    return (" PROOF: filed per option; once answered, the daily sweep checks the chosen "
            "option's predicate and the decision stays open until it holds.")
