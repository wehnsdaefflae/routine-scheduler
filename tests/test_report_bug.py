"""The ungated, default-on `report_bug` action (D29): its schema gate, the engine handler
that appends to <routines_home>/.control/bug-reports.jsonl, and the read side self-audit's
gather-evidence uses. report_bug is one of ALWAYS_KINDS — available to EVERY routine
regardless of the workflow tools allowlist or the capability set."""

from __future__ import annotations

from types import SimpleNamespace

from rsched import bug_reports
from rsched.engine.actions import ALWAYS_KINDS, KIND_EXAMPLES, KINDS, validate_action
from rsched.engine.interact import handle_report_bug
from rsched.grants import GATED_KINDS, GrantPolicy


def _loop(tmp_path, *, slug="some-routine", run_id="some-routine:20260719-161821"):
    home = tmp_path / "routines"
    home.mkdir(parents=True, exist_ok=True)
    ctx = SimpleNamespace(server=SimpleNamespace(routines_home=home),
                          routine=SimpleNamespace(slug=slug), run_id=run_id)
    return SimpleNamespace(ctx=ctx), home


# -- schema gate ------------------------------------------------------------------------


def test_report_bug_is_a_kind_with_an_example():
    assert "report_bug" in KINDS
    assert "report_bug" in ALWAYS_KINDS
    assert "report_bug" not in GATED_KINDS          # ungated: no capability needed
    assert KIND_EXAMPLES["report_bug"]["kind"] == "report_bug"


def test_validate_action_report_bug():
    ok = {"say": "s", "kind": "report_bug", "title": "schedule_run ate my args",
          "detail": "called X, got Y, expected Z"}
    assert validate_action(ok) == []
    # detail is optional
    assert validate_action({"say": "s", "kind": "report_bug", "title": "just a title"}) == []
    # title is required
    assert validate_action({"say": "s", "kind": "report_bug", "detail": "no title"})
    assert validate_action({"say": "s", "kind": "report_bug", "title": "   "})


def test_report_bug_bypasses_allowlist_and_capability_gate():
    obj = {"say": "s", "kind": "report_bug", "title": "a bug"}
    # a restrictive workflow tools: allowlist that omits report_bug still permits it
    assert validate_action(obj, allowed_kinds={"read_file"}) == []
    # a routine with NO gated capabilities still permits it (ungated)
    assert validate_action(obj, grants=GrantPolicy(actions=frozenset())) == []


# -- the engine handler + sink ----------------------------------------------------------


def test_handle_report_bug_appends_to_the_stream(tmp_path):
    loop, home = _loop(tmp_path, slug="bahnbonus-seat-position",
                       run_id="bahnbonus-seat-position:20260719-134640")
    obs = handle_report_bug(loop, {"title": "gmail-body-dump perm-denied",
                                   "detail": "exit 2 creating its output dir under sandbox"})
    assert obs == {"kind": "report_bug", "title": "gmail-body-dump perm-denied", "filed": True}
    path = home / ".control" / "bug-reports.jsonl"
    assert path.is_file()
    reports = bug_reports.read_bug_reports(home)
    assert len(reports) == 1
    rec = reports[0]
    assert rec["routine"] == "bahnbonus-seat-position"
    assert rec["run_id"] == "bahnbonus-seat-position:20260719-134640"
    assert rec["title"] == "gmail-body-dump perm-denied"
    assert rec["detail"].startswith("exit 2")
    assert rec["ts"]


def test_report_bug_appends_do_not_clobber(tmp_path):
    loop, home = _loop(tmp_path)
    handle_report_bug(loop, {"title": "bug one"})
    handle_report_bug(loop, {"title": "bug two", "detail": "second"})
    reports = bug_reports.read_bug_reports(home)
    assert [r["title"] for r in reports] == ["bug one", "bug two"]


def test_read_bug_reports_missing_file_is_empty(tmp_path):
    home = tmp_path / "routines"
    home.mkdir()
    assert bug_reports.read_bug_reports(home) == []
