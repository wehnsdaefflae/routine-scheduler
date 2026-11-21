"""A script without the util-library handle is told WHY, and told it about the right file."""

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
