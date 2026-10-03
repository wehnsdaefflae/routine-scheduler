"""`recipelint._missing_scripts` — which `scripts/<name>.py` mentions are CALLS (F533).

The check's charter (recipelint's module docstring) is "what a machine can be SURE of", and
the F533 false positives were all cases where it was not sure: a recipe TALKING ABOUT a path
rather than calling it. Two shapes are machine-certain and are what these tests pin:

* a path with a parent directory in front of it is not `<this routine>/scripts/<name>.py`,
  which is the only path the `script` action resolves — so it cannot be a call this routine
  makes, however the sentence reads;
* the admission predicate's name is the daemon's in every routine
  (`daemon/gate_prepare.ADMIT`), so a recipe naming it is describing the platform's contract —
  typically a step that installs the predicate INTO ANOTHER routine.

What is deliberately NOT pinned here: a bare `scripts/x.py` inside a sentence whose MEANING is
"the target's own", which stays a finding. A regex cannot read that sentence, and the module
docstring says it has no business guessing — the honest outcome there is a note a person reads.
"""

from __future__ import annotations

import pytest

from rsched.daemon import gate_prepare
from rsched.workflows.recipelint import _missing_scripts


@pytest.fixture
def recipe(tmp_path):
    """A routine dir with a `scripts/` dir holding exactly `real.py`.

    `_missing_scripts` is only asked of a routine that HAS a `scripts/` dir, so the fixture
    must create one or every case would pass for the wrong reason.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "real.py").write_text("print('hi')\n", encoding="utf-8")

    def check(body: str) -> list[str]:
        return _missing_scripts(tmp_path, {"main.md": body})

    return check


def test_a_routine_without_a_scripts_dir_is_never_asked(tmp_path):
    """The docstring's own precondition: no `scripts/` dir means no script tooling at all, and
    a `scripts/…` path in such a recipe belongs to some project the routine works inside.
    """
    assert _missing_scripts(tmp_path, {"main.md": "Run scripts/anything.py."}) == []


def test_a_call_to_a_script_that_exists_is_silent(recipe):
    assert recipe("Each pass runs scripts/real.py over the corpus.") == []


def test_a_call_to_a_script_that_does_not_exist_is_reported(recipe):
    notes = recipe("Each pass runs scripts/absent.py over the corpus.")
    assert len(notes) == 1
    assert "scripts/absent.py" in notes[0]
    assert "write it, or drop the step that calls it" in notes[0]


def test_one_note_per_file_and_name_not_per_mention(recipe):
    """`sorted(set(...))` per file: a recipe naming the same script three times in one file
    has one defect, not three. Two DIFFERENT absent scripts are two defects.
    """
    one = recipe("scripts/absent.py, then scripts/absent.py again, then scripts/absent.py.")
    assert len(one) == 1
    two = recipe("Run scripts/absent.py and then scripts/missing.py.")
    assert len(two) == 2
    assert {"absent", "missing"} == {n.split("scripts/")[1].split(".py")[0] for n in two}


@pytest.mark.parametrize("body", [
    "ls -1 ~/routines/*/scripts/census.py          # who has one",
    "ls -1 /home/mark/routines/*/scripts/census.py",
    "birthday-admin/scripts/census.py is already taken.",
    "Read ../other/scripts/census.py for the shape.",
    "See routines/grants-radar/scripts/census.py.",
])
def test_a_path_with_a_parent_directory_is_not_this_routines_script(recipe, body):
    """F533's measured false positives. `<dir>/scripts/x.py` is by construction not
    `<this routine>/scripts/x.py`, so no step could be calling it here — whatever the prose
    around it says. routine-improver's census of who holds a run-gate predicate is the live
    case: `ls -1 ~/routines/*/scripts/admit.py` asks about OTHER routines by design.
    """
    assert recipe(body) == []


def test_a_bare_path_is_still_a_call_even_next_to_a_globbed_one(recipe):
    """The parent-directory rule must not swallow a real call that shares the line."""
    notes = recipe("ls -1 ~/routines/*/scripts/census.py, then run scripts/absent.py here.")
    assert len(notes) == 1
    assert "scripts/absent.py" in notes[0]


def test_the_daemon_owned_predicate_is_never_demanded(recipe):
    """A recipe step that INSTALLS the admission predicate into another routine cannot satisfy
    "write it, or drop the step": writing the file would create something nothing calls, and
    dropping the step would delete the lens. F533.
    """
    assert recipe(f"Commit scripts/{gate_prepare.ADMIT}.py to the TARGET, never here.") == []


def test_the_excluded_name_is_taken_from_the_daemon_not_restated(recipe):
    """The exclusion must track `gate_prepare.ADMIT` rather than a literal copy of it.

    The name has already moved once: the predicate is `admit` and NOT `gate` precisely because
    three routines already ran a `scripts/gate.py` as an in-run check runner
    (`gate_prepare.py`'s own comment). A test restating the string would keep passing if the
    daemon renamed it again while the check silently went back to demanding the new name —
    so this asserts the live constant is the one being honoured.
    """
    assert recipe(f"Install scripts/{gate_prepare.ADMIT}.py in the target.") == []
    # and the name the daemon deliberately did NOT choose is still an ordinary script
    assert recipe("Run scripts/gate.py as our own checks door.") != []
