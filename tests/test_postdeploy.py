"""`rsched postdeploy` — the checklist a deploy session never had (R2428, rsched/postdeploy.py).

Read 2026-10-01's releases as a dataset: only 0.374.0 came from a scheduled detector; everything
else came from a human mid-deploy. Nine of them say "found while deploying". The four this command
checks for each really happened and each really went unnoticed:

* `rsched-backup.service` failed 19 nights unnoticed — the indicator was ABSENT (0.374.3, F602);
* a deploy script drove the WRONG HOST's Docker daemon (0.374.0);
* the nightly backup failed on a root-owned proxy file (0.373.1);
* and a deploy that does not relaunch the process is the commonest silent failure of all
  (`docker compose up -d` never reloads bind-mounted code).

More deploy TESTS would have caught none of them: each was a property of the live instance, not of
a fixture. So the deliverable is a command the operator runs, and these tests prove the command —
hermetically, over the pure checks in `rsched/postdeploy.py`, with the one live read
(`/api/status`) stubbed at the seam `cli_postdeploy` keeps it behind.
"""

from datetime import UTC, datetime, timedelta

import pytest

from rsched.postdeploy import (
    FAIL,
    NOTE,
    PASS,
    Check,
    PathStat,
    backup_freshness,
    build_skew,
    exit_code,
    record_row,
    served_identity,
    state_path_ownership,
    summary_line,
)

NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)


def _status(**over) -> dict:
    """An `/api/status` payload as `web/app.py` builds it, overridable per test."""
    return {"version": "0.444.0", "build": "ca142b06 2026-10-23", "disk_build": "ca142b06 2026-10-23",
            "build_skew": False, "server_tz": "Europe/Berlin", "active_runs": 0, **over}


# ---- the deploy that did not relaunch: what is SERVED vs what we built --------------------


def test_a_daemon_serving_an_older_version_than_the_checkout_is_the_deploy_that_never_landed():
    """The commonest silent failure. `docker compose up -d` compares the CONFIG, finds no drift
    because the source is bind-mounted, and no-ops — so a green `compose config` and a `Container
    rsched Running` both look like success and say nothing about what is live.
    """
    check = build_skew(_status(version="0.443.0"), "0.444.0", "/home/mark/git-repos/rsched")

    assert check.verdict == FAIL
    assert "0.443.0" in check.detail and "0.444.0" in check.detail   # both numbers, not a verdict
    assert "never reloads" in check.detail                           # and what to do about it


def test_the_daemons_own_skew_verdict_is_read_and_not_recomputed():
    """`/api/status` already carries `build_skew` (web/assetskew, F696): the server is the only
    party holding both the stamp it booted with and the checkout's stamp NOW. A second
    implementation here would be a second thing to keep true.
    """
    check = build_skew(_status(build_skew=True, disk_build="deadbeef 2026-10-23"), "0.444.0")

    assert check.verdict == FAIL
    assert "ca142b06 2026-10-23" in check.detail and "deadbeef 2026-10-23" in check.detail


def test_a_version_match_with_both_stamps_known_is_the_only_pass():
    check = build_skew(_status(), "0.444.0", "/repo")

    assert check.verdict == PASS
    assert "the deploy landed" in check.detail


def test_a_deployment_with_no_git_stamp_is_a_note_and_says_what_it_cannot_see():
    """`assetskew.stamps_skewed` is deliberately false whenever either stamp is unknown — "no
    stamp must never read as a skew". That hole is the version axis's reason for existing, and
    what it cannot cover must be SAID rather than passed over.
    """
    check = build_skew(_status(build="", disk_build=""), "0.444.0")

    assert check.verdict == NOTE
    assert "moved SINCE the boot cannot be seen" in check.detail


def test_an_unreadable_checkout_version_still_reports_the_served_one():
    check = build_skew(_status(), "", "/repo")

    assert check.verdict == NOTE
    assert "0.444.0" in check.detail and "__version__" in check.detail


def test_an_instance_that_did_not_answer_is_not_a_skew_finding_too():
    """One defect, one finding: `served-identity` already holds "nothing answered", and saying it
    twice in one run teaches nobody anything (`conformance.chain_problems`' own rule).
    """
    check = build_skew(None, "0.444.0")

    assert check.verdict == NOTE
    assert "served-identity" in check.detail


# ---- F602: nineteen nights, and the ABSENCE of the indicator was the defect ----------------


def test_a_backup_record_that_cannot_be_read_at_all_fails_and_never_passes():
    """THE specimen. The cause was not a broken indicator; it was the absence of one — a grep for
    `rsched-backup` over src/ and static/ returned zero hits, so nothing could tell a healthy
    instance from an unbacked one, and nineteen nights passed.

    This is the one place the command diverges from `daemon/backup_watch`, which calls an
    unreadable stamp `unknown` and claims nothing: that is right for a health event filed off a
    5-second tick, and wrong for a person standing at the box he just deployed.
    """
    check = backup_freshness("unknown", "no readable .rsched-backup-completed under any root")

    assert check.verdict == FAIL
    assert "ABSENCE" in check.detail
    assert "journalctl --user -u rsched-backup" in check.detail      # the next command to type


def test_a_stale_backup_says_the_instance_is_unprotected_now():
    check = backup_freshness("stale", "the last COMPLETE state snapshot finished 2026-10-04")

    assert check.verdict == FAIL
    assert "UNPROTECTED right now" in check.detail
    assert "F602" in check.detail and "19 nights" in check.detail


def test_a_fresh_backup_passes_with_the_watchs_own_words():
    check = backup_freshness("ok", "last complete snapshot 2026-10-23T01:12:00+00:00")

    assert check.verdict == PASS
    assert check.detail == "last complete snapshot 2026-10-23T01:12:00+00:00"


def test_an_unrecognised_state_from_the_watch_is_a_note_and_not_a_false_pass():
    check = backup_freshness("something-new", "?")

    assert check.verdict == NOTE


@pytest.mark.parametrize(("age_days", "verdict"), [(1, PASS), (19, FAIL)])
def test_the_real_backup_watch_verdict_drives_the_check(tmp_path, age_days, verdict):
    """Wired to the real module, not to a copy of its logic: `backup_watch.staleness` is the ONE
    reader of backup state. 19 days is the real outage, 2026-09-12 to 2026-10-01.
    """
    from rsched.daemon import backup_watch

    root = tmp_path / "share"
    root.mkdir()
    when = NOW - timedelta(days=age_days)
    (root / backup_watch.COMPLETED_STAMP).write_text(f"{when.isoformat()}\n", encoding="utf-8")

    state, detail = backup_watch.staleness(now=NOW, roots=[root])

    assert backup_freshness(state, detail).verdict == verdict


def test_a_host_with_no_backup_root_mounted_reaches_the_absence_finding(tmp_path):
    """The end of the F602 path, through the real reader: no stamp anywhere → `unknown` → FAIL."""
    from rsched.daemon import backup_watch

    state, detail = backup_watch.staleness(now=NOW, roots=[tmp_path / "not-mounted"])

    assert state == "unknown"
    assert backup_freshness(state, detail).verdict == FAIL


# ---- 0.373.1: one root-owned file fails its whole home ------------------------------------


def test_a_root_owned_file_inside_a_correctly_owned_home_is_named_with_its_uid():
    """The 0.373.1 shape exactly: the root-run cliproxy sidecar wrote one 0600 file as root in a
    home whose own directory was perfectly owned. `backup.sh` reads every home as the host user,
    so that one file failed its home — and a failed home keeps NO snapshot at all, every night
    from the sidecar's 2026-09-14 login on.
    """
    stats = [PathStat("/home/mark/routines", exists=True, uid=1000),
             PathStat("/home/mark/.config/routine-scheduler/cliproxy/auth.json",
                      exists=True, uid=0)]

    check = state_path_ownership(stats, 1000)

    assert check.verdict == FAIL
    assert "cliproxy/auth.json is owned by uid 0" in check.detail   # the path AND the uid
    assert "chown" in check.detail


def test_a_home_that_does_not_exist_yet_is_a_note_and_not_a_failure():
    """`STATE_PATHS_OPTIONAL`'s whole reason for being a separate list: state that exists only
    once a feature has been used is a fresh install, not a broken one. A post-deploy check that
    went red on one is a check that gets run once.
    """
    check = state_path_ownership([PathStat("/home/mark/routines", exists=True, uid=1000),
                                 PathStat("/home/mark/background", exists=False)], 1000)

    assert check.verdict == NOTE
    assert "/home/mark/background" in check.detail


def test_a_clean_but_bounded_walk_reports_that_it_was_bounded():
    """A pass the walk did not earn is the failure mode this whole item is about: a check that
    stopped at its file cap and said PASS would be a clean bill over an unknown remainder.
    """
    clean = [PathStat("/home/mark/routines", exists=True, uid=1000)]

    assert state_path_ownership(clean, 1000).verdict == PASS
    bounded = state_path_ownership(clean, 1000, truncated=True)
    assert bounded.verdict == NOTE
    assert "file cap" in bounded.detail


def test_ownership_is_not_checked_when_the_reference_uid_is_unknown():
    check = state_path_ownership([], -1)

    assert check.verdict == NOTE
    assert "could not be determined" in check.detail


# ---- 0.374.0: "I was looking at the wrong machine" ----------------------------------------


def test_the_instance_that_answered_is_printed_with_the_provenance_of_its_address():
    """A deploy script drove the wrong host's Docker daemon and built ~5.2 GB locally. The cure
    is not cleverness: it is printing WHICH box answered, and where the address came from,
    before any verdict.
    """
    check = served_identity("http://127.0.0.1:8321", _status(active_runs=2), "config bind/port")

    assert check.verdict == PASS
    assert "http://127.0.0.1:8321" in check.detail and "config bind/port" in check.detail
    assert "0.444.0" in check.detail and "ca142b06" in check.detail
    assert "2 run(s) active" in check.detail


def test_nothing_answering_fails_and_disclaims_every_verdict_below_it():
    check = served_identity("http://192.168.0.128:8321", None, "--url")

    assert check.verdict == FAIL
    assert "nothing below is a verdict" in check.detail


def test_a_payload_that_is_not_an_rsched_status_says_so_rather_than_passing():
    check = served_identity("http://127.0.0.1:8321", {"ok": True}, "--url")

    assert check.verdict == NOTE
    assert "different service" in check.detail


# ---- the UI: the exit code's meaning, the summary, the record -----------------------------


def test_only_a_fail_moves_the_exit_code():
    """The exit code means exactly one thing: the operator must act before he walks away. A
    command that goes red over a home a fresh install has not created is one nobody runs twice.
    """
    notes = [Check("a", PASS, ""), Check("b", NOTE, ""), Check("c", PASS, "")]

    assert exit_code(notes) == 0
    assert exit_code([*notes, Check("d", FAIL, "")]) == 1
    assert exit_code([]) == 0


def test_the_summary_names_what_to_act_on_and_not_only_a_count():
    checks = [Check("served-identity", PASS, ""), Check("build-skew", FAIL, ""),
              Check("backup-freshness", FAIL, ""), Check("state-path-ownership", NOTE, "")]

    line = summary_line(checks)

    assert "ACT: build-skew, backup-freshness" in line
    assert "2 FAIL" in line and "1 PASS" in line


def test_a_clean_session_says_there_is_nothing_to_act_on():
    assert "nothing to act on" in summary_line([Check("served-identity", PASS, "")])


def test_a_verdict_line_leads_with_the_verdict_so_a_column_of_them_scans():
    line = Check("build-skew", FAIL, "the daemon serves 0.443.0").line()

    assert line.startswith("FAIL  build-skew")
    assert line.endswith("the daemon serves 0.443.0")


def test_the_record_row_keeps_every_verdict_by_name():
    """One row per session — the trace a deploy session used to leave nowhere. Keyed by check
    NAME so a row stays readable when the checklist grows.
    """
    checks = [Check("served-identity", PASS, "answered"), Check("build-skew", FAIL, "skewed")]

    row = record_row("2026-10-23T03:00:00+00:00", "http://127.0.0.1:8321", _status(), checks)

    assert row["ts"] == "2026-10-23T03:00:00+00:00"
    assert row["version"] == "0.444.0" and row["build"] == "ca142b06 2026-10-23"
    assert row["verdicts"] == {"served-identity": PASS, "build-skew": FAIL}
    assert row["details"]["build-skew"] == "skewed"
    assert row["exit"] == 1
    assert "ACT: build-skew" in row["summary"]


def test_a_whole_session_renders_exactly_this_block():
    """The operator-visible output, pinned BYTE-FOR-BYTE — because this output is the product.

    Everything in this item is about a session that left no trace and had no fixed shape, so the
    shape is a test rather than a description: a column of verdicts that scans, the address and
    its provenance first, and a last line naming what to act on. A change to the layout has to
    come here and be seen.
    """
    checks = [served_identity("http://127.0.0.1:8321", _status(), "config bind/port"),
              build_skew(_status(version="0.443.0"), "0.444.0", "/home/mark/git-repos/rsched"),
              backup_freshness("ok", "last complete snapshot 2026-10-23T01:12:00+00:00"),
              state_path_ownership([PathStat("/home/mark/routines", exists=True, uid=1000)], 1000)]

    block = "\n".join([*(c.line() for c in checks), summary_line(checks)])

    assert block == (
        "PASS  served-identity       http://127.0.0.1:8321 (config bind/port) — rsched 0.444.0, "
        "build ca142b06 2026-10-23, tz Europe/Berlin, 0 run(s) active\n"
        "FAIL  build-skew            the daemon serves 0.443.0 and the checkout at "
        "/home/mark/git-repos/rsched is 0.444.0 — the deploy did NOT relaunch the process (or it "
        "relaunched from another tree). `docker compose up -d` never reloads bind-mounted code\n"
        "PASS  backup-freshness      last complete snapshot 2026-10-23T01:12:00+00:00\n"
        "PASS  state-path-ownership  all 1 state path(s) are owned by uid 1000, the uid the "
        "daemon writes as\n"
        "4 checks: 3 PASS · 1 FAIL — ACT: build-skew")
    assert exit_code(checks) == 1


def test_a_row_for_an_instance_that_never_answered_still_records_the_session():
    row = record_row("2026-10-23T03:00:00+00:00", "http://nope:8321", None,
                     [Check("served-identity", FAIL, "no answer")])

    assert row["version"] == "" and row["build"] == ""
    assert row["exit"] == 1
