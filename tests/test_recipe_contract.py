"""A recipe's contract names only the task (P2) — at the two places a recipe is judged.

1. At GENERATION: the materialization pipeline refuses a main or a stage that asks its run for
   bookkeeping, the way it refuses a malformed one, so the retry writes a clean recipe.
2. On the LIVE recipe: `recipelint.recipe_errors` names every such line and `rsched validate`
   FAILS over it — the one recipe finding that is a contract defect rather than a phrasing.
"""

from __future__ import annotations

from types import SimpleNamespace

from helpers import declare_outcome
from rsched import cli
from rsched.endpoints.base import Completion
from rsched.engine import donewhen
from rsched.workflows import pipeline
from rsched.workflows.recipelint import recipe_errors, recipe_notes

MAIN = """# Recipe

## Run flow

1. **work** — `stages/work.md`

## Done when

- d1 · work — the watchlist file holds every verified model
- d2 · work — exactly one LEDGER.md entry is appended and the finish summary gives the tally
- d3 · work — each model's key is in state/seen.json and one LEDGER entry names the run
"""

STAGE = "# work\n\nVerify each model.\n\nThen append one entry to `LEDGER.md` naming the models.\n"


def _recipe(root, main=MAIN, stage=STAGE):
    d = root / "testr"
    (d / "stages").mkdir(parents=True, exist_ok=True)
    (d / "main.md").write_text(main, encoding="utf-8")
    (d / "stages" / "work.md").write_text(stage, encoding="utf-8")
    return d


def test_recipe_errors_name_the_process_only_line_and_every_ledger_write(tmp_path):
    found = recipe_errors(_recipe(tmp_path))
    assert any(f.startswith("main.md: Done when d2: names only a LEDGER entry") for f in found)
    assert not any("Done when d1" in f or "Done when d3" in f for f in found)
    # d2 and d3 both ask for a ledger entry, and so does the stage
    assert sum(f.startswith("main.md: line") for f in found) == 2
    assert any(f.startswith("stages/work.md: line 5: tells the run to write") for f in found)


def test_a_mixed_line_is_a_note_not_an_error(tmp_path):
    notes = recipe_notes(_recipe(tmp_path))
    assert any("Done when d3 also asks for a LEDGER entry" in n for n in notes)
    assert not any("Done when d2 also" in n for n in notes)       # d2 is an error, not a note


def test_a_task_only_recipe_has_no_contract_error(tmp_path):
    main = MAIN.split("- d2", maxsplit=1)[0]
    assert recipe_errors(_recipe(tmp_path, main=main, stage="# work\n\nVerify each model.\n")) == []


def test_validate_fails_over_a_contract_defect(cli_server, make_routine, capsys):
    d = make_routine(slug="testr")
    _recipe(d.parent)
    declare_outcome(d)

    assert cli.cmd_validate(SimpleNamespace(routine="testr")) == 1
    out = capsys.readouterr().out
    assert "testr: PROBLEMS" in out
    assert "FAIL  recipe · main.md: Done when d2: names only a LEDGER entry" in out


# ---- generation: the pipeline asks again -----------------------------------------------------

def test_the_pipeline_regenerates_a_main_that_asks_for_bookkeeping(monkeypatch):
    outline = {"stages": [{"name": "work", "scope": "do it", "inputs": "-", "outputs": "-"}]}
    head = "## Run flow\n\n1. **work** — `stages/work.md`\n\n## Done when\n\n"
    bad = head + "- d1 — the page is published\n- d2 — the finish summary names every page\n"
    good = head + "- d1 — the page is published and read back\n"
    answers = iter([outline, {"main": bad}, {"main": good},
                    {"body": "Append one LEDGER entry.\nThen stop."},
                    {"body": "Publish the page.\nRead it back."}])
    endpoint = SimpleNamespace(complete=lambda *_a, **_k: Completion(
        parsed=next(answers), text=""))
    ref = SimpleNamespace(model="m", effort="", temperature=None, max_tokens=0)
    monkeypatch.setattr(pipeline, "DECOMPOSE_ATTEMPTS", 2)
    out = pipeline._pipeline(lambda: (endpoint, ref), "raw", "task", pins=[], done_when=[],
                             never=[], slug="general-task")
    assert [ln["id"] for ln in donewhen.parse(out["main"])] == ["d1"]
    assert "LEDGER" not in out["stages"]["work"]
