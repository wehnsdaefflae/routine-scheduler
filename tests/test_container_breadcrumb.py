"""The orphan breadcrumb: an orphaned run says WHETHER its process tree was replaced (R2928).

Three kills in two days (2026-10-25 18:03:35, 2026-10-25 20:29:48, 2026-10-26 16:24:05) closed
out as *"orphaned: process gone at daemon boot — cause unrecorded (no deliberate-shutdown
mark)"*, and three runs across two routines concluded the killer was outside what any run could
establish. It was not: pid 1's start time against the run's last status write answers it, and on
2026-10-26 pid 1 was measured 54 SECONDS YOUNGER than the orphan rows — the whole container had
been recreated under the run.

The operator ruled on it (2026-10-26, answering R2966): *"Both: find the restarter AND add the
breadcrumb."* Finding the restarter is a host-side look no run can make and is his. This is the
breadcrumb, and these pin the three things it must get right:

- the inference itself, both ways round (a recreated container, and a daemon that died inside a
  living one — a different investigation that must not wear the container's verdict);
- that a SHUTDOWN MARK still wins, because a mark is a statement of intent by the process that
  performed the stop and nothing inferred may override it;
- that an unanswerable measurement leaves `unknown` exactly as it was. An inference resting on
  nothing is worse than an honest silence, which is the same discipline `classify_cause` already
  applies to a missing memory sample (F569).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from rsched.daemon import containerbirth

HZ = 100


def _proc(tmp_path, *, boot: datetime, pid1_after_boot_s: float):
    """A /proc stand-in carrying just the two files the measurement reads.

    Written as files rather than monkeypatched functions because the parsing is half the
    correctness here: pid 1's `comm` is parenthesized and may contain spaces, so a reader that
    splits the whole line on whitespace mis-indexes every field after it.
    """
    root = tmp_path / "proc"
    (root / "1").mkdir(parents=True)
    (root / "stat").write_text(f"cpu  1 2 3\nbtime {int(boot.timestamp())}\n", encoding="utf-8")
    ticks = int(pid1_after_boot_s * HZ)
    # proc(5) numbers `starttime` field 22, and the first token AFTER the comm is field 3
    # (state) — so exactly 18 fillers stand between them. Written out rather than sliced
    # because getting this count wrong is the one way to mis-read the file, and the first
    # version of this fixture did: it wrote 19 and the parser faithfully read the filler.
    # The comm itself contains a space and a dash (the real pid 1 here is
    # `docker-init -- docker-entrypoint.sh`), which is what breaks a naive whitespace split.
    fields = [str(n) for n in range(4, 22)]      # fields 4..21
    (root / "1" / "stat").write_text(
        f"1 (docker-init -- sh) S {' '.join(fields)} {ticks} 0 0\n", encoding="utf-8")
    return root


def test_pid1_start_time_is_read_through_a_comm_containing_spaces(tmp_path):
    boot = datetime(2026, 10, 26, 10, 0, 0).astimezone()
    proc = _proc(tmp_path, boot=boot, pid1_after_boot_s=600)
    assert containerbirth.pid1_started(proc) == boot + timedelta(seconds=600)


def test_a_container_recreated_under_the_run_is_named_with_both_timestamps(tmp_path):
    """The measured case: pid 1 is YOUNGER than the run's last sign of life.

    2026-10-26's real numbers — pid 1 at 16:23:11, the orphan row at 16:24:05 — are 54 s the
    other way round from what a surviving container looks like, and that difference IS the
    verdict. The sentence carries both stamps because a verdict without its measurement is a
    claim that gets repeated without being checkable.
    """
    boot = datetime(2026, 10, 26, 16, 0, 0).astimezone()
    proc = _proc(tmp_path, boot=boot, pid1_after_boot_s=23 * 60 + 11)   # 16:23:11
    last_turn = boot + timedelta(minutes=22, seconds=17)                # 16:22:17
    recreated, sentence = containerbirth.recreated_under(last_turn, proc)
    assert recreated is True
    assert "CONTAINER was recreated" in sentence
    assert "16:23:11" in sentence and "16:22:17" in sentence
    assert "54s AFTER" in sentence
    assert "only the host can say which" in sentence      # what is genuinely not ours


def test_a_daemon_that_died_inside_a_living_container_is_a_different_cause(tmp_path):
    """pid 1 OLDER than the run: the container lived and the daemon alone went. Named, because
    it is a genuinely different investigation and must not wear the container's verdict.
    """
    boot = datetime(2026, 10, 26, 16, 0, 0).astimezone()
    proc = _proc(tmp_path, boot=boot, pid1_after_boot_s=5)              # 16:00:05
    recreated, sentence = containerbirth.recreated_under(
        boot + timedelta(minutes=30), proc)
    assert recreated is False
    assert "NOT recreated" in sentence


def test_an_unanswerable_measurement_says_nothing_at_all(tmp_path):
    """No last sign of life, or a /proc that will not answer: `(False, "")`, so the caller keeps
    the verdict it had. The honest silence is the point — see the module docstring.
    """
    boot = datetime(2026, 10, 26, 16, 0, 0).astimezone()
    proc = _proc(tmp_path, boot=boot, pid1_after_boot_s=60)
    assert containerbirth.recreated_under(None, proc) == (False, "")
    assert containerbirth.recreated_under(boot, tmp_path / "nothing-here") == (False, "")
    assert containerbirth.pid1_started(tmp_path / "nothing-here") is None


def test_the_run_record_supplies_its_last_sign_of_life_leniently():
    """`RunInfo.updated` is the last status write — the closest thing to a time of death the box
    keeps. A missing or half-written stamp yields None rather than failing the boot reap that is
    trying to close the orphan out.
    """
    stamp = "2026-10-26T16:22:17+02:00"
    assert containerbirth.last_sign_of_life(
        SimpleNamespace(updated=stamp)) == datetime.fromisoformat(stamp)
    assert containerbirth.last_sign_of_life(SimpleNamespace(updated="")) is None
    assert containerbirth.last_sign_of_life(SimpleNamespace(updated="not a stamp")) is None
    assert containerbirth.last_sign_of_life(SimpleNamespace()) is None


def test_a_shutdown_mark_outranks_the_inference():
    """A mark is a statement of intent by the process that performed the stop. The breadcrumb
    only ever refines `unknown` — the word that read as "nobody can know" and sent three runs
    looking outside the container for something readable from inside it.
    """
    run = SimpleNamespace(updated="2026-10-26T16:22:17+02:00")
    assert containerbirth.read_breadcrumb(run, "daemon_restart", "daemon restart (requested)") \
        == ("daemon_restart", "daemon restart (requested)")
    assert containerbirth.read_breadcrumb(run, "oom_kill", "the kernel") == ("oom_kill",
                                                                            "the kernel")


def test_an_unknown_cause_with_nothing_measurable_stays_unknown():
    """The boot reap's own fallback survives: `unknown` plus its sentence, unchanged."""
    run = SimpleNamespace(updated="")      # no last sign of life -> nothing to infer from
    assert containerbirth.read_breadcrumb(run, "unknown", "cause unrecorded") == (
        "unknown", "cause unrecorded")
