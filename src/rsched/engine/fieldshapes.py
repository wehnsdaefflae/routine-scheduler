"""A field written in an EVIDENTLY-INTENDED shape, put into its one canonical shape before the
validator judges it.

Measured on the wrong-turn stream (`wrong_turn_detected`, 2026-11-10..18): every shape below cost
turns across several routines and models, each a schema rejection — and a rejected element of a
chained reply stops the chain, so the actions written after it were NOT EXECUTED too. Each repair
is LOSSLESS: the canonical value says exactly what the written one did, and nothing is guessed.

- a memory note named `X.md` — how `.memory/INDEX.md` lists every note — is note `X`;
- an integer field written as a numeric string (`"n": "4"`) is that integer;
- `supersedes` / `settles` given ONE report id as a string is the one-element list;
- `verified` given a bare date is `{"by": "run", "at": <date>}` — the run attesting its own check;
- a `remind` with no `op` and no `id` can only be an add;
- a `remind_feedback` written as a one-element list, as `{<id>: <label>}`, or with its label under
  another key (`verdict`, `outcome`, `feedback`, or a label value in `disposition`) is the one
  `{id, label}` object. Several labels in one field stay REFUSED (`remindfields`): the canonical
  field is one label, and each further one rides another action of the reply.

The call site is `actions.normalize_action`, the seam that already strips grammar debris; the
storage never sees a second convention.
"""

from __future__ import annotations

import re

from ..reminders import ID_RE, LABELS
from .actionschema import ACTION_SCHEMA
from .notemeta import is_index_name

_INTEGERS = frozenset(k for k, v in ACTION_SCHEMA["properties"].items()
                      if isinstance(v, dict) and v.get("type") == "integer")
_NUMERIC = re.compile(r"^-?\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_LISTS = ("supersedes", "settles")
#: Keys a label arrives under that are not `label` (`disposition` only with a LABEL value).
_LABEL_KEYS = ("verdict", "outcome", "feedback", "result", "disposition")


def canonical(action: dict) -> dict:
    """`action` with every evidently-intended shape put into its canonical one."""
    out = dict(action)
    if out.get("kind") in ("memory_read", "memory_write"):
        name = out.get("name")
        if isinstance(name, str) and name.lower().endswith(".md") and not is_index_name(name):
            out["name"] = name[:-3]
    for key in _INTEGERS & out.keys():
        if isinstance(out[key], str) and _NUMERIC.match(out[key].strip()):
            out[key] = int(out[key])
    for key in _ID_LISTS:
        if isinstance(out.get(key), str) and out[key].strip():
            out[key] = [out[key]]
    if isinstance(out.get("verified"), str) and _DATE.match(out["verified"].strip()):
        out["verified"] = {"by": "run", "at": out["verified"].strip()}
    remind = out.get("remind")
    if isinstance(remind, dict) and not remind.get("op") and not remind.get("id"):
        out["remind"] = {**remind, "op": "add"}
    if "remind_feedback" in out:
        out["remind_feedback"] = _feedback(out["remind_feedback"])
    return out


def _is_hook_id(key: object) -> bool:
    """A reminder id, or a rule assist's `<rule>/<id>` key (engine/assist_label.py)."""
    return isinstance(key, str) and (bool(ID_RE.match(key)) or "/" in key)


def _feedback(fb: object) -> object:
    """One label as `{id, label[, disposition]}`; anything else is returned as written."""
    if isinstance(fb, list) and len(fb) == 1:
        fb = fb[0]
    if not isinstance(fb, dict):
        return fb
    if "id" not in fb and len(fb) == 1:
        (key, value), = fb.items()
        if _is_hook_id(key) and value in LABELS:
            return {"id": key, "label": value}
        return fb
    if "label" not in fb:
        found = [k for k in _LABEL_KEYS if fb.get(k) in LABELS]
        if len(found) == 1:
            fb = {**{k: v for k, v in fb.items() if k != found[0]}, "label": fb[found[0]]}
    if fb.get("id") and fb.get("label") in LABELS:
        fb = {k: v for k, v in fb.items() if k in ("id", "label", "disposition")}
    return fb
