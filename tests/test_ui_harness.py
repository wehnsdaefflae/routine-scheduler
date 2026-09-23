"""How the browser suite is COLLECTED and DISTRIBUTED — pinned by fast tests.

Every fact here is decided before a browser exists: which markers the tests/ui directory
carries, and which xdist scheduler a selection containing them runs under. So it is guarded on
the two-minute gate rather than inside the twenty-minute one it exists to keep honest — which
is where these assertions used to live, and therefore never ran when they mattered.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(relative: str, name: str) -> types.ModuleType:
    """Import a conftest by PATH under its own name — the hooks under test are plain module
    functions, and reaching them through the plugin manager would only hold for the runs that
    happen to have collected that directory."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module     # a @dataclass in the module resolves its own module by name
    spec.loader.exec_module(module)
    return module


UI_CONFTEST = _load("tests/ui/conftest.py", "_ui_conftest_under_test")
ROOT_CONFTEST = _load("tests/conftest.py", "_root_conftest_under_test")


class _Item:
    """The two attributes the marking hook reads, and the one it calls."""

    def __init__(self, path: Path):
        self.path = path
        self.fspath = str(path)
        self.markers: list = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


def _mark(item: _Item, name: str):
    return next(m for m in item.markers if m.name == name)


def test_ui_items_carry_group_ui_and_rerun_markers():
    item = _Item(ROOT / "tests" / "ui" / "test_flows.py")
    UI_CONFTEST.pytest_collection_modifyitems([item])

    assert {m.name for m in item.markers} == {"ui", "xdist_group", "flaky"}
    assert _mark(item, "xdist_group").args == (UI_CONFTEST.BROWSER_GROUP,)
    flaky = _mark(item, "flaky")
    assert flaky.kwargs["reruns"] == 4
    # a wedged sidecar fails identically on every attempt; spending four reruns per test on it
    # turns a one-line diagnosis into a gate-length one
    assert "wedged" in flaky.kwargs["rerun_except"]


def test_fast_suite_items_are_left_unmarked():
    item = _Item(ROOT / "tests" / "test_loop.py")
    UI_CONFTEST.pytest_collection_modifyitems([item])
    assert item.markers == []


def test_marking_runs_before_xdists_own_group_hook():
    """xdist's worker-side hook turns `xdist_group` into the nodeid suffix its scheduler groups
    on. That hook is a plain hookimpl, so only `tryfirst` guarantees the marker is already there
    when it reads: passing an explicit path argument — the form the release gate's partitions
    use — makes this conftest an INITIAL conftest, registered before xdist's worker hook, which
    pluggy would then run first. The grouping would silently degrade to one unit per test."""
    assert UI_CONFTEST.pytest_collection_modifyitems.pytest_impl["tryfirst"] is True


class _Config:
    def __init__(self, dist: str, markexpr: str):
        self.option = types.SimpleNamespace(dist=dist, markexpr=markexpr, loadgroup=False)


@pytest.mark.parametrize(("markexpr", "expected"), [
    ("not ui", "worksteal"),   # the default fast gate keeps the scheduler it was measured on
    ("", "loadgroup"),         # -m "" — what ships a release
    ("ui", "loadgroup"),       # -m ui — the browser suite alone
])
def test_browser_selection_distributes_by_group(markexpr, expected):
    config = _Config("worksteal", markexpr)
    ROOT_CONFTEST.pytest_configure(config)
    assert config.option.dist == expected


def test_worker_is_told_to_write_the_group_into_the_nodeid():
    """A worker re-parses the command line rather than inheriting the controller's options, so
    the controller's `--dist loadgroup` never reaches it; the hook that suffixes the nodeid
    reads `loadgroup` instead. Measured: without this half every browser test still spread
    across all three workers, with the grouping a silent no-op."""
    config = _Config("no", "")
    config.workerinput = {}
    ROOT_CONFTEST.pytest_configure(config)
    assert config.option.loadgroup is True
    assert config.option.dist == "no"    # a worker runs what it is handed


def test_serial_runs_stay_undistributed():
    config = _Config("no", "")
    ROOT_CONFTEST.pytest_configure(config)
    assert config.option.dist == "no"
    assert config.option.loadgroup is False


class _Reporter:
    """The two things the summary hook touches: the config's mark expression, and write_line."""

    def __init__(self, markexpr: str):
        self.config = _Config("worksteal", markexpr)
        self.lines: list[str] = []

    def write_sep(self, _sep, title, **_kw) -> None:
        self.lines.append(title)

    def write_line(self, line, **_kw) -> None:
        self.lines.append(line)


@pytest.mark.parametrize(("markexpr", "expected"), [
    ("not ui", True),     # the default gate — the suite it skipped must be named
    ("", False),          # -m "" ran it
    ("ui", False),        # -m ui ran only it
])
def test_a_skipped_browser_suite_says_so(markexpr, expected):
    """The loudness contract CLAUDE.md leans on: a suite that silently does not run reads
    exactly like a suite that passes, which is how the console shipped a stray `null`, a dock
    left in the page flow and a table-of-contents nobody could see. Nothing else exercises
    this hook, and it keys on a string match over the mark expression."""
    reporter = _Reporter(markexpr)
    ROOT_CONFTEST.pytest_terminal_summary(reporter)
    said = any("the browser suite did not run" in line for line in reporter.lines)
    assert said is expected, reporter.lines
    if expected:   # and it names an invocation that actually works
        assert any("RSCHED_TEST_CDP" in line for line in reporter.lines), reporter.lines


def test_addopts_name_reruns_and_deselect_the_browser_suite():
    """`-rR` names every rerun (`RERUN <nodeid>`) in the summary. Without it an absorbed flake
    is only COUNTED, and the culprit test cannot be named — the self-audit watched an unnamed
    rerun for four runs. `-m "not ui"` is what keeps the default gate at two minutes."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = cfg["tool"]["pytest"]["ini_options"]["addopts"]
    assert "-rR" in addopts, addopts
    assert addopts[addopts.index("-m") + 1] == "not ui", addopts
