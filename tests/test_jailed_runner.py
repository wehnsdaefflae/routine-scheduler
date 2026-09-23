"""ONE jailed subprocess runner for the three callable kinds (`utils_run.run_jailed`).

A util, a per-routine script and a `shell` command are three jail COMPOSITIONS and one
process problem, and the process problem was solved three times by hand. Two of the copies
had lost a protection the third documents:

- `scripts.run_script` used a plain `subprocess.run(timeout=)`, which never kills the `uv run`
  GRANDCHILD a `calls:`-declaring script spawns through `gu` — the child keeps the pipes open
  past the deadline and the engine turn never returns;
- `shellrun.run_shell` wrote to spool files and then read the WHOLE file back
  (`capped(out_f.read())`), i.e. exactly the materialization its own comment said the spool
  files prevent. That is the 2026-09-14 incident (a 1.5 GB decode, five hours of swap-thrash,
  a physical reset) in a seam `shell` reaches with one `find /`.

So the acceptance test here is structural as well as behavioural: only one module may open a
capture tempfile, and no call site anywhere may read one whole.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from rsched import sandbox, scripts, shellrun, utils_run
from rsched.utils_lib import OUTPUT_CAP

SRC = Path(__file__).resolve().parents[1] / "src" / "rsched"


def _capture_handles(tree: ast.AST) -> set[str]:
    """Names bound to a `tempfile.TemporaryFile(...)` — the capture spool files."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem) and isinstance(node.context_expr, ast.Call):
            call, target = node.context_expr, node.optional_vars
            if (isinstance(call.func, ast.Attribute) and call.func.attr == "TemporaryFile"
                    and isinstance(target, ast.Name)):
                names.add(target.id)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(
                node.value.func, ast.Attribute) and node.value.func.attr == "TemporaryFile":
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


# -- the acceptance test ----------------------------------------------------------------

def test_exactly_one_module_opens_a_capture_tempfile():
    """The runner is ONE function. A second module opening a spool file is a second copy of
    the process handling, which is how both regressions above were introduced.
    """
    owners = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
                    if "tempfile.TemporaryFile" in p.read_text(encoding="utf-8"))
    assert owners == ["utils_run.py"]


def test_no_call_site_reads_a_capture_tempfile_whole():
    """`fh.read()` with no size puts the entire capture back in the daemon's memory — the
    failure the spool files exist to prevent. Every read of a captured stream goes through
    `captured_output.read_capped`, which takes `limit + 1` characters and nothing more.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        handles = _capture_handles(tree)
        offenders += [f"{path.relative_to(SRC).as_posix()}:{node.lineno}"
                      for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "read" and not node.args and not node.keywords
                      and isinstance(node.func.value, ast.Name)
                      and node.func.value.id in handles]
    assert not offenders, ("unbounded .read() on a capture tempfile — use "
                           "captured_output.read_capped: " + ", ".join(offenders))


def test_the_three_callable_kinds_share_the_runner():
    """No kind may grow its own `subprocess.run`: it has neither the process group nor the
    bounded capture, and a grandchild survives its timeout.
    """
    for module in (utils_run, shellrun, scripts):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert not re.search(r"subprocess\.run\(", src), module.__name__


# -- behaviour --------------------------------------------------------------------------

PATH_ENV = {"PATH": "/usr/bin:/bin"}


def test_a_grandchild_that_outlives_the_deadline_is_killed_with_its_group(tmp_path):
    res = utils_run.run_jailed(["bash", "-c", "echo started; sleep 30 & wait"],
                               env=PATH_ENV, cwd=tmp_path, timeout=1, label="the command")
    assert res.timed_out is True
    assert "started" in res.stdout                  # what was printed BEFORE the kill survives
    assert "timed out after 1s" in res.stderr


def test_a_runaway_printer_is_bounded_and_says_so(tmp_path):
    res = utils_run.run_jailed(["bash", "-c", 'printf "F%.0s" {1..400}'],
                               env=PATH_ENV, cwd=tmp_path, timeout=30, cap=200)
    assert len(res.stdout) <= 200
    assert res.stdout.capture_truncated is True
    assert res.returncode == 0


def test_ordinary_output_is_returned_unchanged(tmp_path):
    res = utils_run.run_jailed(["bash", "-c", "echo hello; echo oops >&2; exit 3"],
                               env=PATH_ENV, cwd=tmp_path, timeout=30)
    assert res.returncode == 3
    assert res.stdout.strip() == "hello"
    assert res.stderr.strip() == "oops"
    assert res.stdout.capture_truncated is False


def test_a_command_that_cannot_be_spawned_names_the_callable(tmp_path):
    res = utils_run.run_jailed(["/nonexistent/binary"], env={}, cwd=tmp_path, timeout=5,
                               label="script 'poll'")
    assert res.returncode == 2
    assert "could not run script 'poll'" in res.stderr


def test_the_shell_kind_no_longer_carries_a_cap_of_its_own():
    """One 1 MB envelope serves every kind; the observation layer caps again far below it and
    spills the rest to `.util_outputs/`.
    """
    assert not hasattr(shellrun, "STREAM_CAP")
    assert not hasattr(shellrun, "capped")
    assert OUTPUT_CAP == 1_000_000


# -- the seal the jail cannot express ----------------------------------------------------

def test_a_jailed_command_that_rewrites_routine_yaml_is_named(tmp_path, caplog):
    """Landlock unmasks access UP the path, so a narrower rule on one file beneath an allowed
    directory subtracts nothing — and the routine's own dir must stay writable because it is
    the working directory. The action layer refuses `write_file routine.yaml`; a `shell`
    heredoc is not on that path, so the runner reads the file either side of the call.
    """
    (tmp_path / "routine.yaml").write_text("slug: x\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="rsched.utils_run"):
        res = utils_run.run_jailed(
            ["bash", "-c", "printf 'slug: x\\ncapabilities: {}\\n' > routine.yaml"],
            env=PATH_ENV, cwd=tmp_path, timeout=30, label="the command", config_seal=tmp_path)
    assert "routine.yaml changed while the command ran" in res.stderr
    assert "no run edits it" in res.stderr
    assert "a run never writes its own config" in caplog.text


def test_an_untouched_routine_yaml_says_nothing(tmp_path):
    (tmp_path / "routine.yaml").write_text("slug: x\n", encoding="utf-8")
    res = utils_run.run_jailed(["bash", "-c", "echo fine"], env=PATH_ENV,
                               cwd=tmp_path, timeout=30, config_seal=tmp_path)
    assert res.stderr == ""
    assert res.stdout.strip() == "fine"


def test_run_shell_carries_the_seal(tmp_path, make_routine):
    routine = make_routine(slug="sealed")
    res = shellrun.run_shell("printf 'tampered\\n' >> routine.yaml",
                             policy=sandbox.SandboxPolicy(mode="off", own_dir=routine),
                             libraries_home=tmp_path / "lib", cwd=routine, timeout=30)
    assert res["exit"] == 0
    assert "routine.yaml changed while the command ran" in res["stderr"]


@pytest.mark.parametrize("kind", ["script", "shell"])
def test_both_riders_report_a_timeout_with_what_was_printed(tmp_path, make_routine, kind):
    """The script kind used to throw its capture away on timeout (`return 124, "", …`); both
    kinds now keep what the command printed before the kill.
    """
    routine = make_routine(slug=f"timeout-{kind}")
    policy = sandbox.SandboxPolicy(mode="off", own_dir=routine)
    if kind == "shell":
        res = shellrun.run_shell("echo early; sleep 30", policy=policy,
                                 libraries_home=tmp_path / "lib", cwd=routine, timeout=1)
        code, out, err = res["exit"], res["stdout"], res["stderr"]
    else:
        (routine / "scripts").mkdir(exist_ok=True)
        (routine / "scripts" / "slow.py").write_text(
            '"""slow — sleeps."""\nimport time\nprint("early", flush=True)\ntime.sleep(30)\n',
            encoding="utf-8")
        # the venv is the BUILD phase, not this deadline — point the runner at the
        # interpreter running the tests instead of installing one
        (routine / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        (routine / ".venv" / "bin" / "python").symlink_to(sys.executable)
        code, out, err = scripts.run_script(routine, "slow", [], policy=policy,
                                            libraries_home=tmp_path / "lib", timeout=1)
    assert code == 124
    assert "early" in out
    assert "timed out after 1s" in err
