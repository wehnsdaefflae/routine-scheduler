"""A script without the util-library handle is told WHY, and told it about the right file.

F801, reported from outside by `llmsectest-weekday` (R3052). A helper that shelled out to
`gu tts say` died with a bare `FileNotFoundError: 'gu'`, and the reporter's conclusion was that
this host has no util front end at all — a reasonable reading of the only evidence it had.

The helper HAD declared `calls: tts`. But the engine reads the header of the script it RUNS, and
what ran was a bridge (`scripts/project_run.py`) that declared nothing. Two files were plausible
and only one was consulted, which is the whole failure mode — so the note names THE FILE WHOSE
HEADER WAS READ. "this script declares no callees" would send the author back to the file they
are already looking at, which in a bridge topology is the wrong one.

THE SECOND SHAPE, found by the test that was meant to merely confirm the first. Which failure a
caller meets depends on the host:

1. `gu` is not on PATH at all → a bare OSError naming the handle. What R3052 reported.
2. `gu` IS on some inherited PATH but no `GLOBAL_UTILS_HOME` was folded in → the dispatcher
   itself exits 2 with `gu: library home <default> does not exist — run deploy.sh from the
   claude-utils-library repo`, which reads as a broken INSTALL and sends the author to deploy a
   library that is already deployed. This host produces shape 2, so a note that only covered
   shape 1 would have missed every real caller here.

Both are the same cause — no `calls:` line, so no handle — and the note says so explicitly,
including that the message above it is not a broken install.

This lives in its own file rather than in `tests/test_scripts.py` because that file sits at its
`tests/size_ratchet.json` ceiling and may only shrink.
"""

from __future__ import annotations

from pathlib import Path

from rsched import sandbox, scripts

#: Execs a bare `gu`, so it fails whichever shape this host produces.
GU_CALLER = '''\
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""gu-caller — exec a bare gu and let it fail.

net: none
"""
import subprocess
subprocess.run(["gu", "tts", "say", "hi"], check=True)
'''

#: Declares nothing, touches no util, exits 0.
QUIET = '''\
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""quiet — print and exit.

net: none
"""
print("ok")
'''


def _routine(tmp_path: Path) -> Path:
    d = tmp_path / "r"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "bridge.py").write_text(GU_CALLER, encoding="utf-8")
    (d / "scripts" / "quiet.py").write_text(QUIET, encoding="utf-8")
    declaring = GU_CALLER.replace("net: none", "net: none\ncalls: tts")
    (d / "scripts" / "declared.py").write_text(declaring, encoding="utf-8")
    return d


def test_the_note_names_the_file_whose_header_was_read(tmp_path):
    d = _routine(tmp_path)
    policy = sandbox.SandboxPolicy(mode="off")

    code, _out, err = scripts.run_script(d, "bridge", [], policy=policy,
                                         libraries_home=tmp_path / "lib")

    assert code != 0, err
    assert "[engine]" in err, err
    assert str(d / "scripts" / "bridge.py") in err, "the note must name the header it read"
    assert "calls:" in err
    assert "NOT a broken install" in err, "shape 2 reads as a bad deploy unless the note says so"


def test_a_script_that_declares_its_calls_is_never_told_this(tmp_path):
    """It HAS the handle, so the note would be a lie about why its call failed."""
    d = _routine(tmp_path)
    _code, _out, err = scripts.run_script(d, "declared", [], policy=sandbox.SandboxPolicy(
        mode="off"), libraries_home=tmp_path / "lib")

    assert "[engine]" not in err, err


def test_a_script_that_succeeds_never_carries_it(tmp_path):
    d = _routine(tmp_path)
    code, out, err = scripts.run_script(d, "quiet", [], policy=sandbox.SandboxPolicy(mode="off"),
                                        libraries_home=tmp_path / "lib")

    assert code == 0, err
    assert out.strip() == "ok"
    assert "[engine]" not in err
