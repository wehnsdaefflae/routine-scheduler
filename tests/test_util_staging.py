"""A util candidate is selftested OUT of the live path and installed only on green (F584/D161).

The property these pin is NOT the end state — the shape before this (install, selftest,
restore on failure) got the end state right. It is the WINDOW: while a candidate's selftest
runs, `utils/<name>` must still be what every routine's `gu <name>` was already getting. So
the probe utils here OBSERVE THE LIVE LIBRARY FROM INSIDE THEIR OWN SELFTEST and print what
they found; a test that only read the library afterwards would pass on the defect.

Uses uv, like test_utils.py: a staged candidate is run through the real `run_util`, which is
the point — staging must not cost a util its siblings, its declarations or its jail.
"""

import shutil
from types import SimpleNamespace

import pytest

from rsched import sandbox, utils_lib, utils_run, utils_stage
from rsched.engine import authoring

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv required to run utils")

OFF = sandbox.SandboxPolicy(mode="off")

#: A marker that appears in the CANDIDATE's body and in no installed util, so a probe can say
#: whether what it found at `utils/<name>/main.py` was its own untested text.
MARKER = "CANDIDATE-BODY-F584"

ADDER = '''# /// script
# dependencies = []
# ///
"""adder — add two integers. usage: gu adder A B [--json]
tags: test
net: none
fs: none
"""
import json, sys

if "--selftest" in sys.argv:
    assert 2 + 3 == 5
    print("selftest: ok", file=sys.stderr)
    sys.exit(0)
print(json.dumps({"sum": int(sys.argv[1]) + int(sys.argv[2])}))
'''

#: A REVISION of `adder` whose selftest fails — and which first reports whether the live
#: library already holds it. Under the install-then-test shape this printed `yes`.
PROBE_REVISION = f'''# /// script
# dependencies = []
# ///
"""adder — report what the live library holds, then fail. usage: gu adder A B
tags: test
net: none
fs: none
"""
import os, pathlib, sys

MARKER = "{MARKER}"

if "--selftest" in sys.argv:
    live = pathlib.Path(os.environ["GLOBAL_UTILS_HOME"]) / "utils" / "adder" / "main.py"
    text = live.read_text(encoding="utf-8") if live.is_file() else ""
    print("live-holds-candidate: " + ("yes" if MARKER in text else "no"))
    print("live-exists: " + ("yes" if live.is_file() else "no"))
    sys.exit(3)
print("adder")
'''

#: A CREATION whose selftest fails — the live library must not even have the directory.
PROBE_CREATION = f'''# /// script
# dependencies = []
# ///
"""fresh-util — report whether the live library holds it yet, then fail. usage: gu fresh-util
tags: test
net: none
fs: none
"""
import os, pathlib, sys

MARKER = "{MARKER}"

if "__main__" == __name__ and "--selftest" in sys.argv:
    live = pathlib.Path(os.environ["GLOBAL_UTILS_HOME"]) / "utils" / "fresh-util" / "main.py"
    print("live-exists: " + ("yes" if live.is_file() else "no"))
    sys.exit(4)
'''

#: Calls a sibling through `gu` in its selftest — the case a temporary library home would
#: have broken, and the reason the candidate is staged INSIDE the live library.
SIBLING_CALLER = '''# /// script
# dependencies = []
# ///
"""doubler — double N by calling the adder util. usage: gu doubler N
tags: test
calls: adder
net: none
fs: none
"""
import json, subprocess, sys

def run(n):
    out = subprocess.run(["gu", "adder", str(n), str(n)], capture_output=True, text=True)
    return json.loads(out.stdout)["sum"]

if "--selftest" in sys.argv:
    assert run(4) == 8, "doubler broken"
    print("selftest: ok", file=sys.stderr)
    sys.exit(0)
print(run(int(sys.argv[1])))
'''


def _kw(tmp_path):
    return {"policy": OFF, "routines_home": tmp_path / "routines"}


def _staging_dirs(home):
    return [d.name for d in (home / "utils").iterdir()
            if d.name.startswith(utils_lib.STAGING_PREFIX)]


def _library(tmp_path):
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    return home


def test_live_library_never_holds_a_failing_revision_while_it_is_tested(tmp_path):
    """THE finding (F584): the candidate's own selftest looks at `utils/adder/main.py` in the
    live library and must NOT find itself there. Before the fix it did, for the whole selftest
    window — up to the 120 s timeout plus a dependency prewarm — and any routine's `gu adder`
    in that window ran the untested script.
    """
    home = _library(tmp_path)
    ok, _ = utils_stage.write_selftested(home, "adder", ADDER, message="create adder",
                                         **_kw(tmp_path))
    assert ok

    ok, out = utils_stage.write_selftested(home, "adder", PROBE_REVISION, message="revise",
                                           **_kw(tmp_path))
    assert not ok and "exit 3" in out
    assert "live-holds-candidate: no" in out      # the window the finding named is closed
    assert "live-exists: yes" in out              # …and the working util kept serving callers
    assert utils_lib.read_util(home, "adder") == ADDER
    assert not _staging_dirs(home)                 # nothing left behind


def test_the_probe_does_see_the_candidate_under_the_old_install_first_order(tmp_path):
    """THE RED HALF, kept as a test so the one above can never pass vacuously: run the shape
    that was there before F584 — write `utils/adder` first, THEN selftest, restore on failure
    — and the very same probe reports `live-holds-candidate: yes`. The end state is identical
    either way (`adder` is back to its working text), which is exactly why an end-state
    assertion could not catch this and the probe had to look from inside the selftest.
    """
    home = _library(tmp_path)
    assert utils_stage.write_selftested(home, "adder", ADDER, message="create adder",
                                        **_kw(tmp_path))[0]

    previous = utils_lib.read_util(home, "adder")          # the pre-F584 sequence, verbatim
    utils_lib.write_util_file(home, "adder", PROBE_REVISION)
    ok, out = utils_run.selftest(home, "adder", policy=OFF)
    utils_lib.write_util_file(home, "adder", previous)

    assert not ok
    assert "live-holds-candidate: yes" in out              # the exposure window, observed
    assert utils_lib.read_util(home, "adder") == ADDER     # …and the end state looks clean


def test_live_library_never_holds_a_failing_creation_while_it_is_tested(tmp_path):
    """A creation has no `previous` to restore: the live dir must never exist at all, during
    the selftest as much as after it."""
    home = _library(tmp_path)
    ok, out = utils_stage.write_selftested(home, "fresh-util", PROBE_CREATION,
                                           message="create fresh-util", **_kw(tmp_path))
    assert not ok and "exit 4" in out
    assert "live-exists: no" in out
    assert not utils_lib.util_dir(home, "fresh-util").exists()
    assert not _staging_dirs(home)


def test_a_green_candidate_is_installed_and_committed(tmp_path):
    """The other half of the gate: on green the text IS installed at `utils/<name>` and
    committed, so the transaction still does its job."""
    from rsched import libgit

    home = _library(tmp_path)
    ok, out = utils_stage.write_selftested(home, "adder", ADDER, message="create adder",
                                           **_kw(tmp_path))
    assert ok, out
    assert utils_lib.read_util(home, "adder") == ADDER
    assert libgit.git(home, "log", "-1", "--format=%s").stdout.strip() == "create adder"
    code, stdout, _ = utils_run.run_util(home, "adder", ["2", "3"], policy=OFF)
    assert code == 0 and '"sum": 5' in stdout     # installed under its REAL name, runnable
    assert not _staging_dirs(home)


def test_a_staged_candidate_still_reaches_its_siblings(tmp_path):
    """The defect this fix must not introduce: a candidate's selftest may call siblings
    through `gu`, which resolve from the library the dispatcher lives in. Staged inside the
    live library they still resolve — in a temporary library home of its own, a perfectly
    good util would fail its selftest for the wrong reason."""
    home = _library(tmp_path)
    assert utils_stage.write_selftested(home, "adder", ADDER, message="create adder",
                                        **_kw(tmp_path))[0]
    ok, out = utils_stage.write_selftested(home, "doubler", SIBLING_CALLER,
                                           message="create doubler", **_kw(tmp_path))
    assert ok, out
    assert utils_lib.read_util(home, "doubler") == SIBLING_CALLER


def test_a_candidates_own_declarations_are_what_the_staged_run_uses(tmp_path, monkeypatch):
    """The staged run reads the CANDIDATE's header, not the installed util's (or nothing, for
    a creation): a `net: outbound` candidate gets its PEP 723 prewarm at the staged script
    path, so a heavy dependency tree still cannot spend the selftest timeout on install."""
    home = _library(tmp_path)
    outbound = ADDER.replace("net: none", "net: outbound")
    prewarmed: list[str] = []
    monkeypatch.setattr(utils_run, "prewarm_script_deps",
                        lambda script, policy, _home, **_kw: prewarmed.append(script))
    monkeypatch.setattr(utils_run, "run_util", lambda *a, **k: (0, "", "selftest: ok"))
    ok, _ = utils_stage.write_selftested(home, "adder", outbound, message="create adder",
                                         **_kw(tmp_path))
    assert ok
    assert len(prewarmed) == 1
    assert utils_lib.STAGING_PREFIX in prewarmed[0]   # the candidate's path, not the live one
    assert prewarmed[0].endswith("/main.py")


def test_an_exception_inside_the_selftest_leaves_the_library_untouched(tmp_path, monkeypatch):
    """Every failure path ends the same way, because nothing was installed: a raise inside the
    runner (a timeout's kill, an aborted run, a bug) leaves `utils/<name>` exactly as it was
    and no staging residue. The old shape had to RUN a rollback write to achieve this, which
    could itself fail."""
    home = _library(tmp_path)
    assert utils_stage.write_selftested(home, "adder", ADDER, message="create adder",
                                        **_kw(tmp_path))[0]

    def boom(*_a, **_k):
        raise OSError("the runner died")

    monkeypatch.setattr(utils_run, "selftest", boom)
    with pytest.raises(OSError, match="the runner died"):
        utils_stage.write_selftested(home, "adder", PROBE_REVISION, message="revise",
                                     **_kw(tmp_path))
    assert utils_lib.read_util(home, "adder") == ADDER
    assert not _staging_dirs(home)


def test_a_staged_candidate_is_invisible_to_every_discovery_surface(tmp_path):
    """A candidate is a real util dir while it is tested, so the catalog must not show it:
    `list_utils` is what the catalog, the library impact report and the sandbox's
    private-store subtraction all read."""
    home = _library(tmp_path)
    utils_lib.write_util_file(home, utils_stage.staging_name("adder"), ADDER)
    assert utils_lib.list_utils(home) == []
    assert "adder" not in utils_lib.catalog_text(home)


def test_two_concurrent_authors_cannot_collide_or_see_each_other(tmp_path):
    """Two routines (or two web PUTs in one process) may author at once: the staged slug
    carries the pid AND a random token, so one candidate's temporary state can never be read
    as another's."""
    names = {utils_stage.staging_name("adder") for _ in range(50)}
    assert len(names) == 50
    assert all(n.startswith(utils_lib.STAGING_PREFIX) for n in names)
    from rsched.ids import is_slug
    assert all(is_slug(n) for n in names)        # write_util_file/run_util refuse a non-slug


def test_crash_residue_is_swept_but_a_live_candidate_is_not(tmp_path):
    """A process killed between the staged write and its `finally` leaves a dir behind. The
    next transaction sweeps what is older than the cutoff — and nothing younger, so it can
    never delete a candidate another author is testing right now."""
    home = _library(tmp_path)
    old = utils_stage.staging_name("adder")
    fresh = utils_stage.staging_name("other")
    for staged in (old, fresh):
        utils_lib.write_util_file(home, staged, ADDER)
    import os
    aged = utils_lib.util_dir(home, old)
    stale_stamp = aged.stat().st_mtime - utils_stage.STALE_AFTER_S - 60
    os.utime(aged, (stale_stamp, stale_stamp))

    assert utils_stage.sweep_stale(home) == [old]
    assert not utils_lib.util_dir(home, old).exists()
    assert utils_lib.util_dir(home, fresh).exists()


def test_the_staging_prefix_is_refused_as_a_util_name():
    """The prefix is RESERVED: a util actually named that way would be hidden from the catalog
    and swept by the next author's stale sweep."""
    loop = SimpleNamespace(ctx=SimpleNamespace(depth=0))
    result = authoring.handle_write_util(
        loop, {"kind": "write_util", "name": f"{utils_lib.STAGING_PREFIX}sneaky",
               "content": ADDER}, 0.0)
    assert result["declined"] is True
    assert "reserved" in result["reason"]
