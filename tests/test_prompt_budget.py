"""The ENGINE'S always-on prompt, held to a shrink-only budget — the same ratchet the file-size
bar is (test_file_size.py), over the one body every turn of every run re-sends.

Harness² (arXiv 2610.14224) caps its always-on text and tells its editors "if you add always-on
text, cut something else to pay for it": every word there is paid at every step, and the
commonest failure of long runs is reasoning until the budget is gone. On this instance the
always-on body is mostly the engine's own: measured on 2026-10-25 the harness contract, the
action schema and its example were 60-85% of a run's first prompt, identical for every routine,
while the routine's own recipe was a median 5%. Nothing ever counted it, so it only grew.

The budget is the engine CONTRACT — `harness`, `schema` and `example` from
`composer.system_sections` — composed for one fixed routine holding every grantable kind, so a
new kind's prose counts too. A change that grows it fails here; `python tests/test_prompt_budget.py
--update` lowers the ceiling after a cut and REFUSES to raise it. Raising it is a deliberate edit
of `prompt_budget.json` with the reason in the commit — the cost lands on every turn of every
run. A run's own sections (workflow, capabilities, digest) are recorded per run instead
(`quality.prompt_chars`, engine/runrecord.py), because their size is the routine's, not the
engine's.
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
         "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin", "catchup": "skip"}}),
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
    old = json.loads(BUDGET.read_text(encoding="utf-8"))["total"] if BUDGET.exists() else None
    if old is not None and total > old:
        sys.exit(f"refusing to raise the ceiling {old} → {total}: edit prompt_budget.json by hand")
    BUDGET.write_text(json.dumps({"total": total, **now}, indent=1) + "\n", encoding="utf-8")
    sys.stdout.write(f"engine contract ceiling: {total} chars {now}\n")
