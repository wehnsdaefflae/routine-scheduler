"""What a finished run's durable record says about HOW it was made and HOW it went — the two
halves every conclusion about a change is drawn from (readmodels/change_effects.py).

A routine's input changes from run to run, so no single run's output can be scored against a
ground truth. What stays comparable is the PROCESS: the same routine answering for the same
Done when, under the same rules, with the same model, should be about as correct, as complete
and as cheap from one run to the next. So every run records two things beside its usage:

- **`fingerprint`** — everything that can change its behaviour, each a short stable value: the
  engine release, the main model by catalog name and by provider id (`model_id`) with its
  effort, the deliberation level, a hash of the behaviour-relevant config, a hash per held
  rule's text, and a trial id when the run was fired as a model trial. (The recipe and library
  commits ride the record already.) A CHANGE is a boundary between consecutive runs whose
  fingerprints differ; nothing has to declare it. Runs from before this record existed are
  REBUILT from the instance's own history (`migrate_runrecords.py`): they carry
  `source: "reconstructed"` and name what could not be recovered in `unknown`.
- **`quality`** — the run's own counts, read as rates against its peers:
  CORRECTNESS — claims the verifier challenged and the ones that stood disputed, actions a
  caution held, schema retries; COMPLETENESS — accounting lines owed, met, unmet, not due, and
  stages skipped; EFFECTIVENESS — the person's mid-run interventions, wall time, cache reads and
  writes (turns, tokens, cost, status and util outcomes are top-level fields already).

Both are RUN-cumulative (the counters reseed on a resume, `history.prior_counters`), so the
stream's fold keeps the newest leg's values, like `turns`. Written at depth 0 only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import __version__
from ..paths import read_json
from . import accounting

#: Written by the runner for a run fired as a MODEL TRIAL (rsched/trials.arm), read once at the
#: record; the runner reads its `models` again on every leg to name them to `engine-run`.
TRIAL_FILE = "trial.json"

#: Every RoutineConfig field is in exactly one of these two sets (tests/test_runrecord.py fails
#: on a field in neither, so a new field is DECIDED, never hashed by default). BEHAVIOUR is what
#: a run may use and how far it may go — the config hash covers these and nothing else.
BEHAVIOUR = frozenset({"models", "connections", "machines", "grants", "budgets", "permissions",
                       "shared_reminders", "capabilities", "fs_read_roots", "fs_write_roots",
                       "ladder"})
#: What does not change what a run DOES: who the routine is and where it shows (identity), WHEN
#: it runs (its clock, its triggers, its gate, its off switch), how long its runs are kept,
#: whether ANOTHER routine may edit it (`improve`) — and what the fingerprint measures as a
#: component of its own: `deliberation`, `rules` (their texts, by slug), `tuning` (tuning.yaml
#: is part of the recipe) and `trial`, which outlives the trial as history (rsched/trials.py) and
#: would split the runs after a finished trial from the identical runs before it.
NOT_BEHAVIOUR = frozenset({"slug", "dir", "name", "description", "tags", "hub_tab", "kind",
                           "owner", "workflow_slug", "workflow_commit", "playbook_slug",
                           "pattern", "enabled", "cron", "tz", "catchup", "triggers", "run_gate",
                           "keep_runs", "improve", "deliberation", "rules", "tuning", "trial"})


def short_hash(text: str) -> str:
    """The one hash every fingerprint component is written in (rebuilt ones too)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def config_hash(cfg) -> str:
    """A short hash of the routine's BEHAVIOUR fields (models, grants, budgets, permissions,
    capabilities, roots, …) — what an accepted pending change moves.
    """
    dump = cfg.model_dump(mode="json") if hasattr(cfg, "model_dump") else dict(vars(cfg))
    kept = {k: v for k, v in dump.items() if k in BEHAVIOUR}
    return short_hash(json.dumps(kept, sort_keys=True, default=str))


def rule_hashes(rules_home: Path | None, slugs: list[str]) -> dict[str, str]:
    """`{slug: short hash of its text}` for every held rule; "" for one that is missing — a
    rule revision reaches every holder without moving any recipe, so this is what dates it.
    """
    out = {}
    for slug in slugs:
        try:
            text = (Path(rules_home or "") / f"{slug}.md").read_text(encoding="utf-8")
            out[slug] = short_hash(text)
        except OSError:
            out[slug] = ""
    return out


def trial_id(run_dir: Path) -> str:
    doc = read_json(Path(run_dir) / TRIAL_FILE)
    return str(doc.get("id") or "") if isinstance(doc, dict) else ""


def fingerprint(ctx, orch_ref) -> dict:
    return {
        "engine": __version__,
        "model": orch_ref.name or f"{orch_ref.endpoint}/{orch_ref.model}",
        "model_id": f"{orch_ref.endpoint}/{orch_ref.model}",
        "effort": orch_ref.effort or "",
        "deliberation": str(getattr(ctx, "deliberation", "") or ""),
        "config": config_hash(ctx.routine),
        "rules": rule_hashes(getattr(ctx.server, "rules_home", None), list(ctx.routine.rules)),
        **({"trial": tid} if (tid := trial_id(ctx.run_dir)) else {}),
    }


def accounting_counts(entries: object) -> dict[str, int]:
    """A finish accounting's Done-when lines and goals (`d<n>`, `b<n>`) by verdict — one
    reading for a live run and a run rebuilt from its status.json (migrate_runrecords.py).
    """
    verdicts = accounting.parse(entries)
    lines = [v for i, (v, _note) in verdicts.items() if i[0] in "db"]
    return {"owed": len(lines), "met": lines.count("met"), "unmet": lines.count("unmet"),
            "not_due": lines.count("not due")}


def quality(ctx) -> dict:
    usage = ctx.usage_total()
    return {
        **accounting_counts(ctx.accounting),
        "stages_skipped": len(ctx.stage_coverage().get("skipped") or []),
        "challenged": ctx.claims_challenged,
        "disputed": ctx.claims_disputed,
        "holds": ctx.holds,
        "schema_retries": ctx.schema_retries,
        "interventions": ctx.interventions,
        "elapsed_s": int(ctx.elapsed_total_s()),
        "cache_read": int(usage.get("cached_in") or 0),
        "cache_write": int(usage.get("cache_write") or 0),
    }
