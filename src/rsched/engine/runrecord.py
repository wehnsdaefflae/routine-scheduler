"""What a finished run's durable record says about HOW it was made and HOW it went — the two
halves every conclusion about a change is drawn from (readmodels/change_effects.py).

A routine's input changes from run to run, so no single run's output can be scored against a
ground truth. What stays comparable is the PROCESS: the same routine answering for the same
Done when, under the same rules, with the same model, should be about as correct, as complete
and as cheap from one run to the next. So every run records two things beside its usage:

- **`fingerprint`** — everything that can change its behaviour, each a short stable value: the
  engine release, the main model by catalog name with its effort, the deliberation level, a hash
  of the behaviour-relevant config, a hash per held rule's text, and a trial id when the run was
  fired as a model trial. (The recipe and library commits ride the record already.) A CHANGE is
  a boundary between consecutive runs whose fingerprints differ; nothing has to declare it.
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

#: RoutineConfig fields that do not change what a run DOES — who it is, when it fires, how long
#: its runs are kept. Everything else is behaviour: a change there is a change to measure.
#: `trial` is behaviour recorded ELSEWHERE — the run's `trial` id and its `model` — and the field
#: outlives the trial as history (rsched/trials.py), so hashing it would split the runs after a
#: finished trial from the identical runs before it.
_NOT_BEHAVIOUR = frozenset({"name", "slug", "description", "tags", "enabled", "schedule",
                            "retention", "workflow", "playbook", "dir", "deliberation", "trial"})


def _short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def config_hash(cfg) -> str:
    """A short hash of the routine's behaviour-relevant config (models, permissions,
    capabilities, budgets, rules, roots, grants, …) — what an accepted pending change moves.
    """
    dump = cfg.model_dump(mode="json") if hasattr(cfg, "model_dump") else dict(vars(cfg))
    kept = {k: v for k, v in dump.items() if k not in _NOT_BEHAVIOUR}
    return _short(json.dumps(kept, sort_keys=True, default=str))


def rule_hashes(rules_home: Path | None, slugs: list[str]) -> dict[str, str]:
    """`{slug: short hash of its text}` for every held rule; "" for one that is missing — a
    rule revision reaches every holder without moving any recipe, so this is what dates it.
    """
    out = {}
    for slug in slugs:
        try:
            out[slug] = _short((Path(rules_home or "") / f"{slug}.md").read_text(encoding="utf-8"))
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
        "effort": orch_ref.effort or "",
        "deliberation": str(getattr(ctx, "deliberation", "") or ""),
        "config": config_hash(ctx.routine),
        "rules": rule_hashes(getattr(ctx.server, "rules_home", None), list(ctx.routine.rules)),
        **({"trial": tid} if (tid := trial_id(ctx.run_dir)) else {}),
    }


def quality(ctx) -> dict:
    verdicts = accounting.parse(ctx.accounting)
    lines = {i: v for i, (v, _note) in verdicts.items() if i[0] in "db"}
    usage = ctx.usage_total()
    return {
        "owed": len(lines),
        "met": sum(1 for v in lines.values() if v == "met"),
        "unmet": sum(1 for v in lines.values() if v == "unmet"),
        "not_due": sum(1 for v in lines.values() if v == "not due"),
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
