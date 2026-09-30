"""The goal half of the settings-patterns migration — MIGRATION(expires=2026-10-20).

Called per routine by `migrate_settings_patterns._routine`, inside the same commit. The retired
stopping-condition store (`state/stopping.json`) is converted, never left beside its successors:

1. **Goal-scoped conditions become the FINISH LINE** (`engine/finishline.py`), each outcome with
   the judge `FINISH_LINES` names (reports/goal.md §4.5) and the old condition's own words.
2. **The recipe's `## Completion criteria` becomes `## Done when`** — the block drafted for it,
   merged from that section and the routine's run-scoped conditions — followed by a `## Never`
   section where a drafted guardrail has no mechanical home. A section that changed since the
   draft is still replaced (the draft supersedes it); the record says so for review.
3. **Recipe prose and scripts that still rely on the retired model** — stopping conditions,
   domains, templates — are edited in place, one exact replacement at a time; a replacement
   whose text is no longer there is skipped and recorded.
4. **The shared reminder store gets its curator**: rules-review gains the `reminders` stage
   that takes the census, promotes what earned it and routes the rest (`additions`).
5. **What the last run left unmet** under a run-scoped condition reaches the routine's inbox
   once, in that run's own words, so the next run starts from it.

Then `state/stopping.json` is deleted. The drafted texts live in
`migrate_settings_patterns_recipes.json`, which is deleted with this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .paths import atomic_write, read_json

DATA = Path(__file__).with_name("migrate_settings_patterns_recipes.json")
_ID = re.compile(r"s\d+")
_CRITERIA = re.compile(r"^## (completion criteria|definition of done)\s*$",
                       re.IGNORECASE | re.MULTILINE)

#: The finish line each routine's goal conditions become: `(old id or the outcome's own text,
#: judge[, date])`, plus an optional `until`.
_MIZ = ("The MIZ application is submitted and both Mark and Florence know where the steward "
        "page and the final documents live.")
FINISH_LINES: dict[str, dict] = {
    "aisafety-grant-steward": {"outcomes": [("s16", "run"), ("s17", "run"), ("s18", "run")]},
    "ards": {"outcomes": [("s4", "run")]},
    "birthday-admin": {"outcomes": [("s4", "run")], "until": "2027-03-14"},
    "eye-stabilize-folder": {"outcomes": [("s4", "run")]},
    "fau-grant-prep": {"outcomes": [("s4", "run")]},
    "folder-reorg": {"outcomes": [("s4", "you")]},
    "funscript-trainer": {"outcomes": [("s3", "run")]},
    "miz-grant-steward": {"outcomes": [(_MIZ, "run")], "until": "2026-09-27"},
    "nanogeofeld": {"outcomes": [("s4", "you")]},
    "sprind": {"outcomes": [("s4", "date", "2026-08-20")]},
    "suedlink-wlf": {"outcomes": [("s5", "you")]},
    "voice-model-trainer": {"outcomes": [("s3", "you")]},
}
#: A sentence an old condition carried only because no judge field existed; the judge says it now.
_TRIM = (" Mark judges this — report the current distance and wait for his verdict; never mark "
         "it met yourself.")


def convert(rdir: Path, slug: str, *, now: str) -> tuple[list[str], dict]:
    """Convert one routine. Returns (files touched, notes for the migration record)."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    touched: list[str] = []
    notes: dict = {}
    store = rdir / "state" / "stopping.json"
    doc = read_json(store)
    conditions = {str(c.get("id")): c for c in (doc or {}).get("conditions") or []
                  if isinstance(c, dict)} if isinstance(doc, dict) else {}
    if slug in FINISH_LINES:
        touched.append(_finish_line(rdir, FINISH_LINES[slug], conditions, now=now))
    if (residuals := _residuals(conditions)):
        from .engine.inbox import file_message

        file_message(rdir, "This routine's per-run stopping conditions were replaced by its "
                           "recipe's `## Done when`. What the last run left unmet under them, "
                           "in its own words:\n" + "\n".join(f"- {r}" for r in residuals),
                     source="settings-patterns migration", via="report")
        touched.append("inbox/")
    if store.is_file():
        store.unlink()
        touched.append("-state/stopping.json")
    if (drafted := data["done_when"].get(slug)) is not None:
        how = _done_when(rdir, drafted)
        touched.append("main.md")
        if how != "exact":
            notes["done_when"] = how
    skipped = _goal_refs(rdir, data["goal_refs"].get(slug) or [], touched)
    if (added := data["additions"].get(slug)) is not None:
        for rel, text in added["files"].items():
            atomic_write(rdir / rel, text)
            touched.append(rel)
        skipped += _goal_refs(rdir, added["edits"], touched)
    if skipped:
        notes["goal_refs_skipped"] = skipped
    return sorted(dict.fromkeys(touched)), notes


def _finish_line(rdir: Path, spec: dict, conditions: dict, *, now: str) -> str:
    from .engine import finishline

    outcomes = []
    for ref, judge, *date in spec["outcomes"]:
        if _ID.fullmatch(ref):
            text = str((conditions.get(ref) or {}).get("text") or "")
            if not text.strip():
                raise ValueError(f"finish line: no condition {ref} to convert")
        else:
            text = ref
        outcomes.append({"text": text.replace(_TRIM, "").strip(), "judge": judge,
                         "date": date[0] if date else ""})
    finishline.save(rdir, {"outcomes": outcomes, "until": spec.get("until", "")}, now=now)
    return str(finishline.FILE)


def _residuals(conditions: dict) -> list[str]:
    return [f"{str(c.get('text') or '').strip()} — {str(c['note']).strip()}"
            for c in conditions.values()
            if c.get("scope", "run") == "run" and c.get("last_verdict") == "unmet"
            and str(c.get("note") or "").strip()]


def _done_when(rdir: Path, drafted: dict) -> str:
    """Put the drafted block where the completion section was. Returns how: "exact" (the
    drafted section was found verbatim), "changed" (a completion section that changed since the
    draft was replaced), or "appended" (the recipe had none).
    """
    main = rdir / "main.md"
    text = main.read_text(encoding="utf-8")
    block = drafted["block"].rstrip("\n") + "\n"
    if drafted.get("never"):
        block += "\n## Never\n\n" + "\n".join(f"- {line}" for line in drafted["never"]) + "\n"
    old = drafted.get("replaces")
    if old and text.count(old) == 1:
        tail = "\n" if old.endswith("\n\n") else ""
        atomic_write(main, text.replace(old, block + tail))
        return "exact"
    if m := _CRITERIA.search(text):
        rest = text[m.end():]
        nxt = re.search(r"^## ", rest, re.MULTILINE)
        end = m.end() + nxt.start() if nxt else len(text)
        after = text[end:]
        atomic_write(main, text[:m.start()] + block + ("\n" + after if after else ""))
        return "changed"
    atomic_write(main, text.rstrip("\n") + "\n\n" + block)
    return "appended"


def _goal_refs(rdir: Path, edits: list[dict], touched: list[str]) -> list[str]:
    skipped = []
    for edit in edits:
        path = rdir / edit["file"]
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if text.count(edit["old"]) != 1:
            skipped.append(f"{edit['file']}: {edit['old'][:60]!r}")
            continue
        atomic_write(path, text.replace(edit["old"], edit["new"]))
        touched.append(edit["file"])
    return skipped
