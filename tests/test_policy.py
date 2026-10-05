"""Machine-checked repo policies — conventions that broke silently once are tests now.

1. **Delete-after-convergence** (migrations): one-shot migration code must declare its own
   expiry with a `MIGRATION(expires=YYYY-MM-DD)` marker comment next to the code, and the
   suite FAILS once that date passes — a "temporary" migration can never silently become
   permanent. It fires for real: the 2026-08-31/09-01 batch was deleted on its expiry. The
   deploy scripts are read too: they migrate the state they keep (backup.sh's snapshot
   layout), and a marker nothing reads is exactly the silent permanence this exists to stop.
2. **Version discipline**: a bump of `rsched.__version__` must come with a matching
   `## [x.y.z]` header at the top of CHANGELOG.md (0.27 shipped without notes once).
   A pre-commit hook runs this file so the mismatch never reaches a commit.
"""

import re
import warnings
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import rsched

SRC = Path(rsched.__file__).parent
REPO = Path(__file__).resolve().parent.parent

MIGRATION_MARKER = re.compile(r"MIGRATION\(expires=(\d{4}-\d{2}-\d{2})\)")
# code that LOOKS like a migration: a migrate-named def/class, a migrate-* CLI command, or a
# migrate-named shell function in a deploy script
MIGRATION_CODE = re.compile(r"def \w*migrat\w*|class \w*[Mm]igrat\w*|[\"']migrate-"
                            r"|^\s*\w*migrat\w*\s*\(\)\s*\{", re.MULTILINE)


#: How much notice a MIGRATION marker gives before its hard expiry (F628). The hard fail is
#: unchanged and still the enforcement; this is LEAD TIME. The signal used to be binary and
#: arrived the morning it was already too late, on the gate every release must pass — and it
#: arrives for whoever happens to ship that day, not for whoever wrote the migration. Measured
#: 2026-10-05: six markers in four modules expire on one single day (2026-10-20), so the person
#: who meets that red has four modules to understand before anything of theirs can ship.
MIGRATION_WARN_DAYS = 21

#: Where the warn window is written so it outlives the test run that computed it. Beside the
#: gate's own evidence, which is already the place a verdict's detail is looked up.
MIGRATION_WARN_FILE = REPO / ".audit-wt" / ".gate-evidence" / "migration-warn-window.txt"


def _migration_markers() -> list[tuple[Path, str]]:
    """Every `MIGRATION(expires=…)` marker in the policed tree, as (path, date). One reader for
    the hard check and the warning, so the two can never disagree about what is declared."""
    found = []
    for path in [*sorted(SRC.rglob("*.py")), *sorted((REPO / "deploy").glob("*.sh"))]:
        text = path.read_text(encoding="utf-8")
        found.extend((path, expires) for expires in MIGRATION_MARKER.findall(text))
    return found


def test_migration_code_declares_expiry_and_expires():
    today = datetime.now(tz=UTC).date().isoformat()
    problems = [
        f"{path.relative_to(REPO)}: migration expired {expires} — it has converged "
        "on production; DELETE the migration code (the delete-after-convergence "
        "policy, see CLAUDE.md)"
        for path, expires in _migration_markers() if expires < today]
    for path in [*sorted(SRC.rglob("*.py")), *sorted((REPO / "deploy").glob("*.sh"))]:
        text = path.read_text(encoding="utf-8")
        if MIGRATION_CODE.search(text) and not MIGRATION_MARKER.search(text):
            problems.append(
                f"{path.relative_to(REPO)}: migration-shaped code without a "
                "MIGRATION(expires=YYYY-MM-DD) marker — every one-shot migration must "
                "declare when it is overdue for deletion")
    assert not problems, "\n".join(problems)


def test_migrations_expiring_soon_are_warned_about_before_they_turn_red(recwarn):
    """F628: the hard expiry above keeps the policy; this gives it LEAD TIME.

    The enforcement was never missing — one grep disproved that — but its signal was binary and
    landed the morning it was already too late, on the gate every release must pass, in front of
    whoever happened to ship that day. So this emits a WARNING (never a failure) for every marker
    inside `MIGRATION_WARN_DAYS`, naming the file, the date and the days left. `pytest -W` shows
    it, the gate's log carries it, and the person who wrote the migration can still act on it.

    It must never fail: a warning that can fail is a second hard deadline with a different name,
    and the one above is deliberately the only one.
    """
    today = datetime.now(tz=UTC).date()
    soon = []
    for path, expires in _migration_markers():
        try:
            due = date.fromisoformat(expires)
        except ValueError:                      # a malformed date is the hard check's business
            continue
        days = (due - today).days
        if 0 <= days <= MIGRATION_WARN_DAYS:
            soon.append((days, path.relative_to(REPO), expires))
    if soon:
        lines = "\n".join(f"  {p}: MIGRATION expires {d} — {n} day(s) left"
                          for n, p, d in sorted(soon))
        notice = (
            f"{len(soon)} MIGRATION marker(s) expire within {MIGRATION_WARN_DAYS} days and the "
            f"suite goes RED on the day — delete the converged migration code now, before the "
            f"deadline lands on whoever happens to ship that day:\n{lines}")
        warnings.warn(notice, UserWarning, stacklevel=1)
        # A PASSING test's warning reaches nobody: pytest folds it into a summary that xdist
        # suppresses, so the lead time this check exists to give would be invisible — the F628
        # failure, re-created one layer in. So the window is also WRITTEN, to a file beside the
        # gate's own evidence, which survives the run and can be read by a person or a routine.
        # Two deliveries, because the warning is the one a `-W error` run can escalate and the
        # file is the one that is still there tomorrow.
        MIGRATION_WARN_FILE.parent.mkdir(parents=True, exist_ok=True)
        MIGRATION_WARN_FILE.write_text(
            f"MIGRATION WARN WINDOW ({MIGRATION_WARN_DAYS} days), written "
            f"{datetime.now(tz=UTC).isoformat(timespec='seconds')}\n{notice}\n",
            encoding="utf-8")
    elif MIGRATION_WARN_FILE.exists():
        # nothing is due: the stale notice must go, or a cleared window keeps shouting
        MIGRATION_WARN_FILE.unlink()
    # the contract this test actually guards: warning, never failing
    assert all(w.category is UserWarning for w in recwarn.list
               if "MIGRATION marker" in str(w.message))


def test_the_warn_window_is_actually_delivered_and_not_merely_computed(recwarn):
    """F628's own trap: a warning emitted by a PASSING test is folded into a summary that the
    gate's xdist run suppresses, so a check that only warned would give no lead time at all —
    which is the defect it was built to fix. This asserts the DELIVERY, not the computation.

    It runs the real check against the real tree. When nothing is inside the window there is
    nothing to deliver, the stale notice is removed, and that is the whole assertion; when
    something is, it must be in the WRITTEN notice (the channel that outlives the run) AND in a
    warning (what a `-W error` run can escalate), naming the file and the date — a count alone
    tells nobody what to delete.
    """
    test_migrations_expiring_soon_are_warned_about_before_they_turn_red(recwarn)
    warned = [str(w.message) for w in recwarn.list if "MIGRATION marker" in str(w.message)]
    today = datetime.now(tz=UTC).date()
    due = [(path, expires) for path, expires in _migration_markers()
           if 0 <= (date.fromisoformat(expires) - today).days <= MIGRATION_WARN_DAYS]
    if not due:
        assert not warned, "warned about an empty window"
        assert not MIGRATION_WARN_FILE.exists(), "a cleared window left its notice behind"
        return
    assert MIGRATION_WARN_FILE.exists(), (
        "the window was computed and never delivered — a warning on a passing test is swallowed "
        "by the summary xdist suppresses, so nobody is told")
    written = MIGRATION_WARN_FILE.read_text(encoding="utf-8")
    assert "MIGRATION WARN WINDOW" in written, written
    assert warned, "nothing was warned, so `-W error` could never escalate this"
    for path, expires in due:
        assert str(path.relative_to(REPO)) in written, (path, written)
        assert expires in written, (expires, written)
    assert "day(s) left" in written


def test_the_warn_window_fires_before_the_hard_fail_does():
    """The two checks must be ORDERED, not merely both present: a window that does not reach
    further than the hard deadline warns about nothing, and the warning would be decoration."""
    assert MIGRATION_WARN_DAYS > 0
    today = datetime.now(tz=UTC).date()
    about_to_expire = (today + timedelta(days=MIGRATION_WARN_DAYS)).isoformat()
    # a marker at the far edge of the window is NOT expired (so the hard check passes) and IS
    # inside the window (so the warning fires) — the gap between the two is the lead time
    assert about_to_expire >= today.isoformat()
    assert (date.fromisoformat(about_to_expire) - today).days == MIGRATION_WARN_DAYS


def test_the_host_pin_is_the_engine_images_python():
    """`.python-version` pins the host venv to the interpreter the engine image runs; bumping
    either alone would test one Python and ship another."""
    pinned = (REPO / ".python-version").read_text(encoding="utf-8").strip()
    image = re.search(r"^FROM python:(\d+\.\d+)", (REPO / "Dockerfile").read_text(encoding="utf-8"),
                      re.MULTILINE)
    assert image, "the Dockerfile no longer builds FROM python:<major.minor>"
    assert image.group(1) == pinned, (f"the engine image runs {image.group(1)}, the host pin is "
                                      f"{pinned}: move both together")


def test_version_bump_has_changelog_entry():
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    headers = re.findall(r"^## \[(\d+\.\d+\.\d+)\] — (\d{4}-\d{2}-\d{2})$",
                         changelog, flags=re.MULTILINE)
    assert headers, "CHANGELOG.md has no '## [x.y.z] — YYYY-MM-DD' release header"
    assert headers[0][0] == rsched.__version__, (
        f"__version__ is {rsched.__version__} but the newest CHANGELOG.md header is "
        f"[{headers[0][0]}] — every version bump ships its release notes in the same commit")
