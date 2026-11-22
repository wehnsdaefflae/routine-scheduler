"""What a finished run's durable record says about HOW it was made and HOW it went — the two
halves every conclusion about a change is drawn from (readmodels/change_effects.py).

A routine's input changes from run to run, so no single run's output can be scored against a
ground truth. What stays comparable is the PROCESS: the same routine answering for the same
Done when, under the same rules, with the same model, should be about as correct, as complete
and as cheap from one run to the next. So every run records two things beside its usage:

- **`fingerprint`** — everything that can change its behaviour, each a short stable value: the
  engine release, the main model by catalog name and by provider id (`model_id`) with its
  effort, the deliberation level, a hash of the behaviour-relevant config, a hash per held
  rule's text. (The recipe and library commits ride the record already.) A CHANGE is a boundary
  between consecutive runs whose fingerprints differ; nothing has to declare it. Rebuilt records
  carry `source: "reconstructed"` and name what could not be recovered in `unknown`.
- **`quality`** — the run's own counts, read as rates against its peers:
  CORRECTNESS — claims the verifier challenged and the ones that stood disputed, actions a
  caution held, schema retries; COMPLETENESS — accounting lines owed, met, unmet, not due, and
  stages skipped; EFFECTIVENESS — the person's mid-run interventions, wall time, cache reads and
  writes (turns, tokens, cost, status and util outcomes are top-level fields already) — and
  the run's FRICTIONS, each thing that went wrong by its signature (engine/frictions.py), which
  the moment test reads (readmodels/change_frictions.py), `prompt_chars`, the always-on
  system prompt's size per section (harness, schema, workflow, capabilities, digest, …), and
  `overhead` — its landed actions as task, process and friction counts plus `process_share`
  (engine/overhead.py), which the process-overhead invariant reads
  (readmodels/overhead_reading.py).

Both are RUN-cumulative (the counters reseed on a resume, `history.prior_counters`), so the
stream's fold keeps the newest leg's values, like `turns`. Written at depth 0 only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import __version__
from . import accounting, frictions

#: Every RoutineConfig field is in exactly one of these two sets (tests/test_runrecord.py fails
#: on a field in neither, so a new field is DECIDED, never hashed by default). BEHAVIOUR is what
#: a run may use and how far it may go — the config hash covers these and nothing else.
BEHAVIOUR = frozenset({"models", "efforts", "connections", "machines", "grants", "budgets",
                       "permissions", "shared_reminders", "capabilities", "fs_read_roots",
                       "fs_write_roots", "ladder", "chain_actions"})
#: What does not change what a run DOES: identity, WHEN it runs, retention, `improve`, and what
#: the fingerprint measures separately (`deliberation`, `rules` by text, `tuning` as recipe).
#: `outcome` measures a run and changes nothing it may do.
#: `parent` and `lineage` are a conversation's PROVENANCE — which conversation it was forked
#: from and at which turn. Like `slug` and `owner` they say where it came from, never what a run
#: of it may do, so a fork does not read as a behaviour change of its parent or of itself.
NOT_BEHAVIOUR = frozenset({"slug", "dir", "name", "description", "tags", "kind",
                           "owner", "workflow_slug", "workflow_commit", "playbook_slug",
                           "pattern", "enabled", "cron", "tz", "triggers", "run_gate",
                           "keep_runs", "improve", "deliberation", "rules", "tuning", "outcome",
                           "parent", "lineage"})


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


def fingerprint(ctx, orch_ref) -> dict:
    return {
        "engine": __version__,
        "model": orch_ref.name or f"{orch_ref.endpoint}/{orch_ref.model}",
        "model_id": f"{orch_ref.endpoint}/{orch_ref.model}",
        "effort": orch_ref.effort or "",
        "deliberation": str(getattr(ctx, "deliberation", "") or ""),
        "config": config_hash(ctx.routine),
        "rules": rule_hashes(getattr(ctx.server, "rules_home", None), list(ctx.routine.rules)),
    }


def accounting_counts(entries: object) -> dict[str, int]:
    """A finish accounting's Done-when lines and goals (`d<n>`, `b<n>`) by verdict — one
    reading for a live run and a run rebuilt from its status.json (the one-shot backfill).
    """
    verdicts = accounting.parse(entries)
    lines = [v for i, (v, _note) in verdicts.items() if i[0] in "db"]
    return {"owed": len(lines), "met": lines.count("met"), "unmet": lines.count("unmet"),
            "not_due": lines.count("not due")}


def quality(ctx) -> dict:
    usage = ctx.usage_total()
    found = frictions.of_run(ctx.run_dir)
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
        # what went wrong, by signature — absent (unknown) when no transcript could be read
        **({"frictions": found} if found is not None else {}),
        # what the always-on prompt weighed, section by section (composer.system_sections)
        **({"prompt_chars": dict(ctx.prompt_chars)} if ctx.prompt_chars else {}),
        # its landed actions by task/process/friction and the `process_share` — the fleet
        # invariant's input (engine/overhead.py); absent (unknown) with no transcript to read
        **({"overhead": dict(ctx.overhead)} if ctx.overhead else {}),
        # what the post-run review leg did, when it ran (engine/reviewleg.py)
        **({"review": dict(review)} if (review := getattr(ctx, "review", None)) else {}),
    }
