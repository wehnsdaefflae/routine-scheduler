"""Recipe hygiene (F516): what `recipe_notes` says about a routine's own main.md + stages/,
and the one promise the wiring makes — `rsched validate` PRINTS the findings and never fails
over them.
"""

from types import SimpleNamespace

import pytest
import yaml

from rsched import cli
from rsched.config import load_server_config
from rsched.workflows.recipelint import recipe_notes

REPO_SEED = cli.Path(__file__).resolve().parents[1] / "library-seed"

MAIN = """# Test recipe

Run flow:

- `orient` → `stages/orient.md`
- `report` → `stages/report.md`
"""


def _recipe(tmp_path, main: str = MAIN, stages: dict[str, str] | None = None) -> object:
    d = tmp_path / "routines" / "testr"
    (d / "stages").mkdir(parents=True)
    (d / "main.md").write_text(main, encoding="utf-8")
    for name, body in (stages or {"orient": "# Orient\n", "report": "# Report\n"}).items():
        (d / "stages" / f"{name}.md").write_text(body, encoding="utf-8")
    return d


def test_clean_recipe_says_nothing(tmp_path):
    assert recipe_notes(_recipe(tmp_path)) == []


def test_no_main_md_is_not_a_recipe(tmp_path):
    d = tmp_path / "routines" / "conv"
    d.mkdir(parents=True)
    assert recipe_notes(d) == []


def test_route_to_a_module_that_does_not_exist(tmp_path):
    d = _recipe(tmp_path, MAIN + "\n- `wrap` → `stages/wrap-up.md`\n")
    (notes,) = recipe_notes(d)
    assert "main.md" in notes and "stages/wrap-up.md" in notes and "does not exist" in notes


def test_stage_nothing_routes_to(tmp_path):
    d = _recipe(tmp_path, stages={"orient": "# Orient\n", "report": "# Report\n",
                                  "leftover": "# Leftover\n"})
    (notes,) = recipe_notes(d)
    assert notes.startswith("stages/leftover.md:") and "nothing routes to it" in notes


def test_a_sibling_stage_counts_as_routing(tmp_path):
    # routine-improver's lenses are reached from a sibling stage, never from main.md
    d = _recipe(tmp_path, MAIN + "- `apply` → `stages/apply-lenses.md`\n",
                stages={"orient": "# Orient\n", "report": "# Report\n",
                        "lens-bugfix": "# Lens\n",
                        "apply-lenses": "read `stages/lens-bugfix.md` and follow it\n"})
    assert recipe_notes(d) == []


def test_frontmatter_module_list_does_not_count_as_routing(tmp_path):
    front = "---\nname: Test\nmodules: [orient, report, leftover]\n---\n" + MAIN
    d = _recipe(tmp_path, front, stages={"orient": "# O\n", "report": "# R\n",
                                         "leftover": "# L\n"})
    (notes,) = recipe_notes(d)
    assert "stages/leftover.md" in notes


def test_step_calling_a_script_the_routine_does_not_have(tmp_path):
    d = _recipe(tmp_path, MAIN + "\nRun `scripts/gate.py` before you push.\n")
    (d / "scripts").mkdir()
    (d / "scripts" / "other.py").write_text("# x\n", encoding="utf-8")
    (notes,) = recipe_notes(d)
    assert "scripts/gate.py" in notes and "does not have" in notes

    (d / "scripts" / "gate.py").write_text("# x\n", encoding="utf-8")
    assert recipe_notes(d) == []


def test_a_script_path_without_a_scripts_dir_is_somebody_elses_tree(tmp_path):
    d = _recipe(tmp_path, MAIN + "\nThe project's own `scripts/build.py` renders the site.\n")
    assert recipe_notes(d) == []


def test_step_naming_a_kind_the_capabilities_do_not_enable(tmp_path):
    d = _recipe(tmp_path, MAIN + "\nWhen the work is large, `schedule_run` a follow-up.\n")
    (notes,) = recipe_notes(d)
    assert "`schedule_run`" in notes and "do not enable" in notes
    assert recipe_notes(d, {"actions": ["schedule_run"]}) == []


def test_prose_words_are_not_action_kinds(tmp_path):
    d = _recipe(tmp_path, MAIN + "\nThree things differ from a shell-driven agent: the "
                                 "filter script is refreshed backup-first.\n")
    assert recipe_notes(d) == []


def test_an_edit_that_left_the_old_block_standing(tmp_path):
    old = ("Close every report you received in the SAME run: triage each untargeted row, "
           "chase the ones an owner drained, and keep your own statuses honest before you "
           "finish the run at all.")
    new = ("Close every report you received in the SAME run: triage each untargeted row, "
           "chase the ones an owner drained, and keep your own statuses honest before you "
           "finish the run at last.")
    d = _recipe(tmp_path, f"{MAIN}\n{old}\n\nSomething else entirely.\n\n{new}\n")
    (notes,) = recipe_notes(d)
    assert "say nearly the same thing" in notes and "lines 8 and 12" in notes


def test_a_stage_restating_main_is_the_normal_shape(tmp_path):
    block = ("Read the module for the current state and follow it exactly, one action per "
             "turn, writing the phase line each module ends with before you hand the turn "
             "back to the loop.")
    d = _recipe(tmp_path, f"{MAIN}\n{block}\n",
                stages={"orient": f"# Orient\n\n{block}\n", "report": "# Report\n"})
    assert recipe_notes(d) == []


# ---- the wiring: `rsched validate` reports, and never fails over, a recipe finding --------


@pytest.fixture
def cli_server(tmp_path, monkeypatch):
    import shutil

    lib = tmp_path / "library"
    for kind in ("workflows", "rules", "permissions"):
        shutil.copytree(REPO_SEED / kind, lib / kind)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": "t", "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(lib)}), encoding="utf-8")
    server, problems = load_server_config(cfg_path)
    assert not problems
    (tmp_path / "routines").mkdir(exist_ok=True)
    monkeypatch.setattr(cli, "load_server_config", lambda: (server, []))
    return server


def test_validate_reports_the_recipe_and_stays_green(cli_server, make_routine, tmp_path, capsys):
    d = make_routine(slug="testr")
    (d / "stages").mkdir()
    (d / "stages" / "orient.md").write_text("# Orient\n", encoding="utf-8")
    (d / "main.md").write_text(MAIN, encoding="utf-8")   # routes to stages/report.md: absent

    assert cli.cmd_validate(SimpleNamespace(routine="testr")) == 0   # a REPORT, not a gate
    out = capsys.readouterr().out
    assert "testr: ok" in out
    assert "NOTE  recipe · main.md: routes to `stages/report.md`" in out
