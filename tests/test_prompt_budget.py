"""The ENGINE'S always-on prompt, held to a shrink-only budget — the same ratchet the file-size
bar is (test_file_size.py), over the one body every turn of every run re-sends.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BUDGET = Path(__file__).with_name("prompt_budget.json")
CONTRACT = ("harness", "schema", "example")


def contract_chars(tmp_path: Path) -> dict[str, int]:
    import yaml

    from helpers import run_context, server_config
    from rsched.engine.actionschema import KINDS
    from rsched.engine.composer import state_digest, system_sections
    from rsched.grantpolicy import GrantPolicy

    d = tmp_path / "routines" / "budget"
    (d / "state").mkdir(parents=True)
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"name": "Budget", "slug": "budget", "enabled": True, "description": "fixed",
         "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin"}}),
        encoding="utf-8")
    (d / "main.md").write_text("## Run flow\n", encoding="utf-8")
    ctx = run_context(d, "20261025-070000",
                      server=server_config(libraries_home=tmp_path / "libraries"))
    ctx.grants = GrantPolicy(actions=frozenset(KINDS), reminders="local")
    sections = dict(system_sections(ctx, "## Run flow", "task", state_digest(d, [], []), []))
    # the routine's own path is the one run-specific string the contract quotes: measured
    # without it, the count is the same wherever the measuring directory happens to be
    return {name: len(sections[name].replace(str(tmp_path), "")) for name in CONTRACT}


def test_the_engine_contract_never_grows(tmp_path):
    ceiling = json.loads(BUDGET.read_text(encoding="utf-8"))
    now = contract_chars(tmp_path)
    total = sum(now.values())
    assert total <= ceiling["total"], (
        f"the engine's always-on contract grew to {total} chars (ceiling {ceiling['total']}, "
        f"{now}): every turn of every run re-sends it. Cut something else to pay for it, or "
        "raise the ceiling in tests/prompt_budget.json with the reason in the commit.")
    assert total >= ceiling["total"], (
        f"the contract shrank to {total} chars (ceiling {ceiling['total']}): lock the cut in "
        "with `python tests/test_prompt_budget.py --update`, or it is room to re-grow.")


if __name__ == "__main__" and "--update" in sys.argv:
    import tempfile

    sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1] / "src")]
    with tempfile.TemporaryDirectory() as tmp:
        now = contract_chars(Path(tmp))
    total = sum(now.values())
    prior = json.loads(BUDGET.read_text(encoding="utf-8")) if BUDGET.exists() else {}
    old = prior.get("total")
    if old is not None and total > old:
        sys.exit(f"refusing to raise the ceiling {old} → {total}: edit prompt_budget.json by hand")
    # Carry every key this does NOT compute — `note` holds the reason each past raise was
    # allowed, which is the only record of what the contract was permitted to grow for. The
    # first version wrote `{"total": total, **now}` and silently dropped it: one `--update`
    # erased D212's +1382 and D118's +524 justifications while reporting success.
    carried = {k: v for k, v in prior.items() if k not in {"total", *now}}
    BUDGET.write_text(json.dumps({"total": total, **now, **carried}, indent=1) + "\n",
                      encoding="utf-8")
    sys.stdout.write(f"engine contract ceiling: {total} chars {now}\n")
