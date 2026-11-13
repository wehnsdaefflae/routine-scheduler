"""A new routine is born MEASURING what it was created to produce.

D286, the operator's answer verbatim (2026-11-12): *"At creation: scaffold a
`scripts/outcome.py` stub derived from the `produces` lines, so a new routine measures
something from its first run (the reporter's preference)"*.

`create_routine` REQUIRES `produces` before anything is created, so the contract always
existed and the measuring script never did: `drawerrr-irreversible-actions` was created on
2026-11-10 and immediately flagged itself on its own settings page — "recipe:outcome — it
declares an outcome but has no scripts/outcome.py to measure it — every run records an outcome
error instead of a reading". A routine born in a state its own surface calls broken.

The stub is DERIVED from the `produces` lines (`engine/outcome_words.contract`), never guessed:
one measure per line, named by its id. It must satisfy the reader that flags its absence
(`engine/outcome.parse` — exit 0 and one JSON object on the last line carrying a number for
every declared measure), and it must be HONEST about being a stub: a TODO naming, per measure,
what a real measurement would read.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rsched.config import ServerConfig, load_routine
from rsched.engine import outcome as outcome_reader
from rsched.engine.outcome_words import contract
from rsched.workflows.outcome_stub import render
from rsched.workflows.scaffold import scaffold

SEED = Path(__file__).resolve().parents[1] / "library-seed"

PRODUCES = ["principal: Mark",
            "interviews · count · higher — interviews or calls booked with a client",
            "weight_kg · level · lower — his weight on the scale this morning"]


def _server(tmp_path: Path) -> ServerConfig:
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.libraries_home = SEED
    return server


def _declared(lines: list[str] | None = None):
    cfg = contract(lines or PRODUCES)
    assert not isinstance(cfg, str), cfg
    return cfg


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args], capture_output=True,
                          text=True, cwd=script.parent.parent, timeout=60, check=False)


def test_a_new_routine_is_born_measuring_its_declared_outcome(tmp_path):
    """The whole item: the engine's OWN reader takes the stub's output at the first run."""
    server = _server(tmp_path)
    declared = _declared()

    d = scaffold(server, slug="born-measuring", name="Born measuring", instruction="x",
                 workflow_slug="general-task", outcome=declared.model_dump())

    script = d / "scripts" / "outcome.py"
    assert script.is_file(), "the contract was demanded and nothing was scaffolded to measure it"
    proc = _run(script)
    assert proc.returncode == 0, proc.stderr
    reading = outcome_reader.parse(proc.stdout, outcome_reader.declared(load_routine(d)[0]))
    assert "error" not in reading, reading
    assert set(reading["values"]) == {"interviews", "weight_kg"}, reading


def test_the_setup_surface_no_longer_calls_a_newborn_routine_unmeasured(tmp_path):
    """The row that reported the defect. `recipe:outcome` has three gaps; this closes the
    SECOND (no script to measure the declared contract) and must not fake the third.
    """
    from rsched.readmodels import surface_outcome

    server = _server(tmp_path)
    d = scaffold(server, slug="surfaced", name="Surfaced", instruction="x",
                 workflow_slug="general-task", outcome=_declared().model_dump())
    cfg, problems = load_routine(d)
    assert problems == [], problems

    nodes = surface_outcome._outcome_nodes(server, cfg)

    assert not [n for n in nodes if n["state"] == "unmeasured"], nodes


def test_a_routine_that_declares_no_outcome_gets_no_stub(tmp_path):
    """Nothing to derive from, and the engine runs no script for a routine with no contract."""
    server = _server(tmp_path)

    d = scaffold(server, slug="no-contract", name="No contract", instruction="x",
                 workflow_slug="general-task")

    assert not (d / "scripts" / "outcome.py").exists()


def test_the_stub_is_derived_from_the_produces_lines_and_names_every_measure(tmp_path):
    """Derived, not guessed: each measure's id, its label in the person's words, its kind and
    which way is better all come off the line that declared it.
    """
    text = render(_declared())

    assert "interviews" in text and "weight_kg" in text
    assert "interviews or calls booked with a client" in text
    assert "his weight on the scale this morning" in text
    assert "count, higher is better" in text and "level, lower is better" in text
    assert "Mark" in text


def test_the_stub_says_it_is_a_stub_and_what_a_real_measurement_would_read(tmp_path):
    """A stub that reads as finished code is worse than a missing one: the next author has no
    way to know the number is a placeholder. The TODO is part of the deliverable.
    """
    text = render(_declared())

    assert "TODO" in text
    todo = text[text.index("TODO"):]
    assert "interviews" in todo and "weight_kg" in todo


def test_the_stub_reports_what_the_routines_own_records_hold(tmp_path):
    """The bridge that makes the stub worth scaffolding: a stage that writes numbers into
    `state/outcome.json` is measured for real, with no edit to the script at all.
    """
    server = _server(tmp_path)
    d = scaffold(server, slug="recording", name="Recording", instruction="x",
                 workflow_slug="general-task", outcome=_declared().model_dump())
    (d / "state").mkdir(exist_ok=True)
    (d / "state" / "outcome.json").write_text(json.dumps({"interviews": 3, "weight_kg": 84.2}),
                                              encoding="utf-8")

    proc = _run(d / "scripts" / "outcome.py")

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"interviews": 3,
                                                                "weight_kg": 84.2}


def test_a_record_that_is_not_a_number_is_left_out_rather_than_coerced(tmp_path):
    """The outcome reader's own rule: a measure the records cannot support is left out, never
    estimated. Leaving it out costs the reading; coercing a word to a number corrupts the
    series, which is permanent.
    """
    server = _server(tmp_path)
    d = scaffold(server, slug="worded", name="Worded", instruction="x",
                 workflow_slug="general-task", outcome=_declared().model_dump())
    (d / "state").mkdir(exist_ok=True)
    (d / "state" / "outcome.json").write_text(json.dumps({"interviews": "lots"}),
                                              encoding="utf-8")

    proc = _run(d / "scripts" / "outcome.py")

    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert printed["interviews"] == 0, printed
    assert "lots" in proc.stderr


def test_the_stubs_header_is_the_one_the_engine_reads(tmp_path):
    """A routine script's docstring header is its only machine-read surface (rsched/scripts.py):
    a stub born without `net:`/`secrets:` would be a second defect on the same page.
    """
    from rsched.utils_header import parse_header

    header = parse_header(render(_declared()))

    assert header["summary"].startswith("outcome — ")
    assert header["usage"] and header["tags"]
    assert header["net"] == "none" and header["secrets"] == []


def test_the_stub_carries_a_selftest_that_passes(tmp_path):
    """Every results script in this instance has one, and it is what an author edits against."""
    server = _server(tmp_path)
    d = scaffold(server, slug="selftested", name="Selftested", instruction="x",
                 workflow_slug="general-task", outcome=_declared().model_dump())

    proc = _run(d / "scripts" / "outcome.py", "--selftest")

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_label_holding_source_punctuation_still_renders_runnable_python(tmp_path):
    """`produces` is the person's PROSE and the stub is compiled Python: a label holding a
    backslash would make the scaffolded script raise a SyntaxWarning at import — which this
    repo turns into an error (`filterwarnings = error`) — and one holding a triple quote would
    end the docstring early. The reported LABEL keeps the words he wrote, unescaped.
    """
    label = 'papers matching \\d+ in """any""" venue'
    rough = _declared(["principal: Mark", f"papers · count · higher — {label}"])

    text = render(rough)
    # COMPILED IN-PROCESS on purpose: an invalid escape is a SyntaxWarning, which this repo's
    # pytest config escalates to an error here — in a subprocess it would only print to stderr
    # and still exit 0, so running the file could never catch it.
    namespace: dict = {"__name__": "scaffolded_stub",
                       "__file__": str(tmp_path / "scripts" / "outcome.py")}
    exec(compile(text, "<outcome-stub>", "exec"), namespace)  # noqa: S102

    assert namespace["MEASURES"]["papers"] == label, "the person's own words, unescaped"
    assert namespace["measure"]({"papers": 2}) == {"papers": 2}


def test_one_measure_is_as_derivable_as_six(tmp_path):
    """The smallest contract the config allows — one measure — must render a valid script."""
    one = _declared(["principal: Mark", "programmes · count · higher — new programmes found"])

    text = render(one)
    script = tmp_path / "scripts" / "outcome.py"
    script.parent.mkdir(parents=True)
    script.write_text(text, encoding="utf-8")

    proc = _run(script)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"programmes": 0}
