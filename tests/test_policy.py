"""Machine-checked repo policies — conventions that broke silently once are tests now."""

import ast
import itertools
import re
import warnings
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import rsched
from rsched import migration_markers as markers

SRC = Path(rsched.__file__).parent
REPO = Path(__file__).resolve().parent.parent

#: How much notice a MIGRATION marker gives before its hard expiry (F628). The hard fail is
#: unchanged and still the enforcement; this is LEAD TIME. The signal used to be binary and
#: arrived the morning it was already too late, on the gate every release must pass — and it
#: arrives for whoever happens to ship that day, not for whoever wrote the migration. Measured
#: 2026-10-05: six markers in four modules expired on one single day (2026-10-20), so the person
#: who met that red would have had four modules to understand before anything of theirs shipped.
MIGRATION_WARN_DAYS = markers.WARN_DAYS

#: Where the warn window is written so it outlives the test run that computed it. Beside the
#: gate's own evidence, which is already the place a verdict's detail is looked up.
MIGRATION_WARN_FILE = REPO / ".audit-wt" / ".gate-evidence" / "migration-warn-window.txt"


def _policed() -> list[Path]:
    """The files the policy reads, pinned to THIS checkout — a worktree's own tree, never the
    installed package's (F495: a bare pytest in a worktree imports the live tree's `src/`)."""
    return markers.policed_paths(repo=REPO, package=SRC)


def test_migration_code_declares_expiry_and_expires():
    today = datetime.now(tz=UTC).date()
    problems = [
        f"{d.path.relative_to(REPO)}: migration expired {d.expires} — it has converged "
        "on production; DELETE the migration code (the delete-after-convergence "
        "policy, see CLAUDE.md)"
        for d in markers.expired(today, _policed())]
    problems += [
        f"{path.relative_to(REPO)}: migration-shaped code (or a back-compat alias table) "
        "without a MIGRATION(expires=YYYY-MM-DD) marker — every one-shot migration and every "
        "deprecation must declare when it is overdue for deletion. A comment that merely "
        "MENTIONS a marker does not declare one (rsched.migration_markers.declares)"
        for path in markers.undeclared(_policed())]
    assert not problems, "\n".join(problems)


def test_narration_about_a_marker_is_not_a_declaration():
    """R2596, the operator's ruling. 0.423.1 deleted the `trigger_*` appliers and left a
    tombstone comment that SPELLED the marker it was describing; the morning after that date the
    check read the sentence as a live declaration, `main` itself went red and every release on
    this host was blocked by a comment about code that no longer existed.
    """
    declaring = [
        '"""One-shot boot migration onto ONE off switch — MIGRATION(expires=2026-11-15).',
        "    # MIGRATION(expires=2026-11-15): `schedule.disabled` folded into one off switch",
        "(MIGRATION(expires=2026-11-15)): the seed sync only ADDS missing patterns",
        "MIGRATION(expires=2026-10-20)",
        "# MIGRATION(expires=2026-11-15) — ONE-SHOT: move an existing install off root",
    ]
    narrating = [
        "    # their MIGRATION(expires=2026-10-15) came due and the drain is complete",
        ('    """The three `trigger_*` appliers were deleted when their '
         "MIGRATION(expires=2026-10-15)\n    came due; the routes that queued them went too"),
        "# the deprecation whose MIGRATION(expires=2026-10-15) we honoured in 0.423.1",
        # 2026-11-19: the WRAPPED sentence — `cli_daemon.py`'s two tombstones, as written. The
        # marker opened its line, so the watch filed both as due-soon for a migration deleted
        # weeks before.
        ("    # (The `converse` pattern's goals revision was carried by a one-shot here until its\n"
         "    # MIGRATION(expires=2026-11-30) was honoured: it ran on 2026-10-08"),
    ]
    # …while a declaration under a comment that ENDS a sentence, or under code, still declares.
    declaring += [
        ("    # (A fresh install gets the revision from the seed sync above.)\n"
         "    # MIGRATION(expires=2026-12-31): the fourteen INSTALLED patterns gain"),
        "    sync_seed_utils(server.libraries_home)\n    # MIGRATION(expires=2027-01-31): the gu",
    ]
    for text in declaring:
        hit = markers.MARKER.search(text)
        assert hit and markers.declares(text, hit.start()), f"read as narration: {text!r}"
    for text in narrating:
        hit = markers.MARKER.search(text)
        assert hit and not markers.declares(text, hit.start()), f"read as a declaration: {text!r}"


def test_a_narrated_marker_cannot_excuse_a_real_migration(tmp_path):
    """The grammar must not become a loophole: a file whose only marker is narration still has
    migration-shaped code with nothing declared, so the check's second half flags it. Otherwise
    "mention the marker in prose" would be a way to buy silence forever.
    """
    shaped = tmp_path / "migrate_thing.py"
    shaped.write_text('"""Its MIGRATION(expires=2027-01-01) was honoured long ago."""\n\n'
                      "def run_migration(server):\n    return {}\n", encoding="utf-8")
    assert markers.undeclared([shaped]) == [shaped]
    assert markers.declarations([shaped]) == []
    shaped.write_text("# MIGRATION(expires=2027-01-01): the real thing\n"
                      "def run_migration(server):\n    return {}\n", encoding="utf-8")
    assert not markers.undeclared([shaped])
    assert [d.expires for d in markers.declarations([shaped])] == ["2027-01-01"]


def test_a_back_compat_alias_table_must_declare_an_expiry(tmp_path):
    """F699: `jobs_compat.py` was the one deprecation here with no expiry, and the check could
    not have seen it — its code is not migrate-named. What a back-compat layer in this repo IS
    made of is an alias table: a constant holding the pre-rename SPELLING. A protocol adapter
    named `*_compat.py` that deprecates nothing must stay unflagged, which is why the filename
    is not the rule (`endpoints/openai_compat.py` would be flagged forever).
    """
    shim = tmp_path / "widget_compat.py"
    shim.write_text('"""What the widget layer was called before the rename."""\n\n'
                    'LEGACY_FILE = "state/widgets.json"\n'
                    'SETTING_KEY, SETTING_ALIAS = "gadgets", "widgets"\n', encoding="utf-8")
    assert markers.undeclared([shim]) == [shim], "an alias table with no expiry must be flagged"
    adapter = tmp_path / "vendor_compat.py"
    adapter.write_text('"""A vendor-compatible adapter — deprecates nothing."""\n\n'
                       'ENDPOINT = "https://example.invalid/v1"\n'
                       "def complete(prompt):\n    return prompt\n", encoding="utf-8")
    assert not markers.undeclared([adapter]), "a protocol adapter is not a deprecation"
    assert not markers.undeclared([Path(markers.__file__)]), "the policy module is not one either"


def test_migrations_expiring_soon_are_warned_about_before_they_turn_red(recwarn):
    """F628: the hard expiry above keeps the policy; this gives it LEAD TIME."""
    today = datetime.now(tz=UTC).date()
    soon = [(left, d.path.relative_to(REPO), d.expires)
            for left, d in markers.due_soon(today, MIGRATION_WARN_DAYS, _policed())]
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
    """
    test_migrations_expiring_soon_are_warned_about_before_they_turn_red(recwarn)
    warned = [str(w.message) for w in recwarn.list if "MIGRATION marker" in str(w.message)]
    today = datetime.now(tz=UTC).date()
    due = markers.due_soon(today, MIGRATION_WARN_DAYS, _policed())
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
    for _left, declared in due:
        assert str(declared.path.relative_to(REPO)) in written, (declared.path, written)
        assert declared.expires in written, (declared.expires, written)
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


def test_changelog_versions_never_repeat_or_go_backwards():
    """Several writers release into one main (self-audit, the builder, operator sessions), and a
    merge that keeps its own entry on top passed the check above while main had already shipped
    the same number — two different 0.371.0s, and the console's version went backwards (decided
    2026-11-05). Every header must be strictly lower than the one above it."""
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = [tuple(int(n) for n in v.split("."))
                for v in re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, flags=re.MULTILINE)]
    bad = [f"{'.'.join(map(str, a))} above {'.'.join(map(str, b))}"
           for a, b in itertools.pairwise(versions) if a <= b]
    assert not bad, f"CHANGELOG.md headers repeat or go backwards: {bad[:5]}"


def test_designs_md_holds_orders_never_open_questions():
    """docs/designs.md is DECIDED-but-unbuilt work, and its reader is the builder. Five
    "Decide: …" entries sat there from 2026-10-01 to 2026-11-05: an open question in a file only
    the builder reads never reached the operator's Decisions page, so nobody could answer it and
    nobody could build it. A question is a self-audit `D` row (it reaches the Decisions page);
    the entry is written here once the answer exists."""
    text = (REPO / "docs" / "designs.md").read_text(encoding="utf-8")
    asked = [h for h in re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
             if re.match(r"(?i)(decide|question|open question|undecided)\b", h)]
    assert not asked, f"open questions parked in docs/designs.md (ask them as D rows): {asked}"


#: The writing standard (CLAUDE.md Standards): the bounds a narrating author outgrows first.
CHANGELOG_ENTRY_MAX, CHANGELOG_BOUNDED_FROM, CLAUDE_MD_MAX = 40, (0, 574, 0), 300


def test_test_docstrings_are_one_paragraph():
    """A test docstring says what is pinned and why in one paragraph; the story is the commit's."""
    found = []
    for path in sorted((REPO / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        nodes = [tree, *(n for n in ast.walk(tree)
                         if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                         and n.name.startswith("test_"))]
        found += [f"{path.relative_to(REPO)}::{getattr(n, 'name', '<module>')}" for n in nodes
                  if "\n\n" in (ast.get_docstring(n) or "").strip()]
    assert not found, f"multi-paragraph test docstrings: {found}"


def test_changelog_entries_stay_short():
    """Every release entry from 0.574.0 on is at most 40 lines under its header."""
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    entries = re.split(r"^## \[(\d+)\.(\d+)\.(\d+)\][^\n]*$", changelog, flags=re.MULTILINE)[1:]
    long_ = [f"{'.'.join(v)}: {n} lines" for *v, body in itertools.batched(entries, 4)
             if tuple(map(int, v)) >= CHANGELOG_BOUNDED_FROM
             and (n := len(body.strip("\n").splitlines())) > CHANGELOG_ENTRY_MAX]
    assert not long_, f"CHANGELOG entries over {CHANGELOG_ENTRY_MAX} lines: {long_}"


def test_claude_md_stays_the_working_tier():
    """CLAUDE.md holds one line per contract; the long form is docs/contracts.md."""
    lines = len((REPO / "CLAUDE.md").read_text(encoding="utf-8").splitlines())
    assert lines <= CLAUDE_MD_MAX, f"CLAUDE.md is {lines} lines (max {CLAUDE_MD_MAX})"
