"""D226: the browser stage's job comes back with the PEAK MEMORY of the `uitest` container's own
memory cgroup for that job's duration — measured inside the container that does the work, since
the caller (the gate, in the engine container) cannot read that cgroup.

Offline throughout: the cgroup is a directory tree in `tmp_path`, never the host's. What these
tests pin is the seam's three promises — the number is read where it can be read, it travels
under a key both halves agree on, and no failure of the measurement changes a job's outcome.
They cannot pin the ONE thing the number never claims: that no OOM occurred (a restart starts a
fresh cgroup; only the host's `journalctl -k` settles it), so the reading says so itself and
`test_a_reading_never_claims_no_oom` holds it to that.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "uitest"))
import cgroupmem  # noqa: E402
import protocol  # noqa: E402
import submit  # noqa: E402

# The spool and the stepper the other uitest tests already use — re-exported rather than
# rebuilt, so both files exercise one spool. A fixture is re-registered under its own name
# (the house pattern, cf. tests/test_artifact_freshness.py) because an imported fixture a test
# then takes as a parameter reads to ruff as a redefinition.
from test_uitest_runner import _drive, _py  # noqa: E402
from test_uitest_runner import spool as _spool_fixture  # noqa: E402

spool = pytest.fixture(name="spool")(_spool_fixture.__wrapped__)


def _tree(base: Path, files: dict[str, str], relative: str = "") -> tuple[Path, Path]:
    """A fake cgroup v2 hierarchy: the mount root and a /proc/self/cgroup naming `relative`."""
    where = base / "cgroup" / relative if relative else base / "cgroup"
    where.mkdir(parents=True)
    for name, text in files.items():
        (where / name).write_text(text)
    proc = base / "proc-self-cgroup"
    proc.write_text(f"0::/{relative}\n")
    return base / "cgroup", proc


# ---- the reader, against a fake /sys/fs/cgroup -------------------------------------------------

def test_memory_peak_is_preferred_and_read_as_an_exact_bound(tmp_path):
    root, proc = _tree(tmp_path, {"memory.peak": "3221225472\n", "memory.current": "12\n",
                                  "memory.max": "4294967296\n"})
    watch = cgroupmem.watch(root=root, proc=proc)
    assert (root / "memory.peak").read_text() == "0"      # reset, so the mark covers this job
    (root / "memory.peak").write_text("3221225472\n")     # what the job then peaked at
    reading = watch.finish()
    assert reading["peak_bytes"] == 3221225472
    assert reading["limit_bytes"] == 4294967296
    assert reading["peak_pct_of_limit"] == 75.0
    assert reading["bound"] == "exact"                    # the kernel's own high-water mark
    assert reading["jobs_overlapped"] is False


def test_a_peak_that_could_not_be_reset_is_an_upper_bound_not_an_exact_one(tmp_path, monkeypatch):
    root, proc = _tree(tmp_path, {"memory.peak": "500\n", "memory.max": "1000\n"})
    monkeypatch.setattr(cgroupmem, "_write_zero", lambda _path: False)   # pre-6.1 kernel
    reading = cgroupmem.watch(root=root, proc=proc).finish()
    assert reading["peak_bytes"] == 500 and reading["bound"] == "upper"


def test_a_concurrent_job_makes_the_reading_an_upper_bound(tmp_path):
    root, proc = _tree(tmp_path, {"memory.peak": "500\n", "memory.max": "1000\n"})
    reading = cgroupmem.watch(exclusive=False, root=root, proc=proc).finish()
    # The cgroup is the CONTAINER's: two jobs in it cannot be told apart, and the reading says so
    # rather than attributing the other job's memory to this one.
    assert reading["bound"] == "upper" and reading["jobs_overlapped"] is True


def test_without_memory_peak_the_current_is_sampled_as_a_lower_bound(tmp_path, monkeypatch):
    root, proc = _tree(tmp_path, {"memory.current": "100\n", "memory.max": "1000\n"})
    monkeypatch.setattr(cgroupmem, "SAMPLE_EVERY_S", 0.01)
    watch = cgroupmem.watch(root=root, proc=proc)
    for value in (900, 400):                      # a rise the sampler must keep, then a fall
        (root / "memory.current").write_text(f"{value}\n")
        time.sleep(0.08)
    reading = watch.finish()
    assert reading["peak_bytes"] == 900           # the high-water mark of what it saw
    assert reading["peak_pct_of_limit"] == 90.0
    assert reading["bound"] == "lower"            # a spike between two samples is missed
    assert reading["samples"] >= 2


def test_a_cgroup_under_a_relative_path_is_found_the_way_the_kernel_names_it(tmp_path):
    root, proc = _tree(tmp_path, {"memory.peak": "7\n", "memory.max": "max\n"},
                       relative="docker/abcdef")
    assert cgroupmem.cgroup_dir(root, proc) == root / "docker/abcdef"
    watch = cgroupmem.watch(root=root, proc=proc)
    (root / "docker/abcdef" / "memory.peak").write_text("7\n")   # what a kernel would refill
    reading = watch.finish()
    assert reading["peak_bytes"] == 7
    assert reading["cgroup"] == str(root / "docker/abcdef")       # the one it actually read


def test_no_ceiling_set_reports_no_percentage_rather_than_a_guess(tmp_path):
    root, proc = _tree(tmp_path, {"memory.peak": "7\n", "memory.max": "max\n"})
    reading = cgroupmem.watch(root=root, proc=proc).finish()
    assert reading["limit_bytes"] is None and reading["peak_pct_of_limit"] is None


def test_both_files_absent_measures_nothing_rather_than_zero(tmp_path):
    root, proc = _tree(tmp_path, {})
    assert cgroupmem.cgroup_dir(root, proc) is None
    assert cgroupmem.watch(root=root, proc=proc).finish() is None


def test_an_unreadable_cgroup_measures_nothing_and_raises_nothing(tmp_path):
    root, proc = _tree(tmp_path, {"memory.max": "1000\n"})
    (root / "memory.peak").mkdir()                # exists, and every read of it fails
    assert cgroupmem.watch(root=root, proc=proc).finish() is None


def test_a_broken_reader_measures_nothing_rather_than_raising(tmp_path, monkeypatch):
    root, proc = _tree(tmp_path, {"memory.peak": "7\n"})
    monkeypatch.setattr(cgroupmem, "_reading", lambda *a, **k: 1 / 0)
    assert cgroupmem.watch(root=root, proc=proc).finish() is None


def test_a_reading_never_claims_no_oom(tmp_path):
    root, proc = _tree(tmp_path, {"memory.peak": "7\n", "memory.max": "1000\n"})
    reading = cgroupmem.watch(root=root, proc=proc).finish()
    # A restart starts a FRESH cgroup, so these files can never settle whether the kernel's OOM
    # killer fired: `journalctl -k` on the host is the only thing that can.
    assert reading["proves_no_oom"] is False


# ---- the protocol: the key both halves agree on ------------------------------------------------

def test_a_result_round_trips_under_the_key_both_halves_read(tmp_path):
    protocol.write_result(tmp_path, {protocol.MEMORY: {"peak_bytes": 7, "bound": "exact"}})
    assert protocol.job_result(tmp_path)[protocol.MEMORY] == {"peak_bytes": 7, "bound": "exact"}


def test_a_result_without_the_field_reads_as_not_measured_never_as_zero(tmp_path):
    protocol.write_result(tmp_path, {protocol.MEMORY: None})      # nothing was measured
    assert not (tmp_path / protocol.RESULT).exists()              # and nothing is claimed
    assert protocol.job_result(tmp_path) == {}
    assert protocol.job_result(tmp_path).get(protocol.MEMORY) is None


def test_a_runner_that_reports_nothing_and_one_that_reports_more_both_read(tmp_path):
    # The two halves deploy separately (the runner is baked into the image, the client runs from
    # the checkout), so neither may break on the other's vocabulary.
    (tmp_path / protocol.RESULT).write_text('{"memory": {"peak_bytes": 7}, "future": 1}')
    assert protocol.job_result(tmp_path) == {"memory": {"peak_bytes": 7}, "future": 1}
    (tmp_path / protocol.RESULT).write_text("not json at all")
    assert protocol.job_result(tmp_path) == {}


# ---- end to end, through the spool ------------------------------------------------------------

@pytest.fixture
def fake_cgroup(tmp_path, monkeypatch):
    """Point the reader at a tree we control, so the host's own kernel cannot sway these.

    `memory.current` only, with no `memory.peak`: a fake tree has no kernel to refill a mark
    this code resets, so the SAMPLING path is the one an offline end-to-end test can exercise
    honestly — the `memory.peak` path is pinned above, against a tree whose peak is set by hand.
    """
    root, proc = _tree(tmp_path / "fake", {"memory.current": "2048\n", "memory.max": "8192\n"})
    monkeypatch.setattr(cgroupmem, "CGROUP_ROOT", root)
    monkeypatch.setattr(cgroupmem, "PROC_CGROUP", proc)
    return root


@pytest.mark.usefixtures("fake_cgroup")
def test_a_finished_job_carries_its_peak_back_to_the_caller(spool, capfdbinary):
    job, _ = submit.submit(spool, _py("print('ran')"), [])
    _drive(spool, job)
    assert protocol.job_result(job)[protocol.MEMORY]["limit_bytes"] == 8192
    assert submit.follow(job, spool) == 0
    out = capfdbinary.readouterr().out.decode()
    assert protocol.MEMORY_LINE in out and '"peak_pct_of_limit": 25.0' in out


@pytest.mark.usefixtures("fake_cgroup")
def test_the_result_is_complete_before_the_exit_file_appears(spool):
    # `exit` means "finished", so whatever a caller reads afterwards must already be whole.
    job, _ = submit.submit(spool, _py("pass"), [])
    _drive(spool, job)
    assert (job / "exit").stat().st_mtime_ns >= (job / protocol.RESULT).stat().st_mtime_ns
    assert protocol.MEMORY in protocol.job_result(job)


@pytest.mark.usefixtures("fake_cgroup")
def test_a_job_that_cannot_start_leaves_no_sampling_thread_behind(spool, tmp_path):
    # The runner is a long-lived process: a watch opened for a job that then fails to start must
    # be closed, or every failed start leaks a thread polling memory.current for the container's
    # lifetime.
    # A binary that does not exist fails in Popen — AFTER the watch is opened, which is the
    # only ordering that can leak one. (A bad `cwd` is refused before it, so it cannot.)
    job, _ = submit.submit(spool, [str(tmp_path / "no-such-binary")], [])
    _drive(spool, job)
    assert (job / "exit").read_text().strip() == "2"
    assert [t.name for t in threading.enumerate() if t.name == "uitest-memory"] == []


def test_an_unmeasurable_cgroup_leaves_the_job_untouched(spool, tmp_path, monkeypatch,
                                                         capfdbinary):
    monkeypatch.setattr(cgroupmem, "CGROUP_ROOT", tmp_path / "nothing-here")
    job, _ = submit.submit(spool, _py("print('still ran'); raise SystemExit(3)"), [])
    _drive(spool, job)
    assert submit.follow(job, spool) == 3                 # the job's own code, to the bit
    out = capfdbinary.readouterr().out.decode()
    assert "still ran" in out
    assert protocol.MEMORY_LINE not in out                # not measured prints nothing at all


def test_a_measurement_that_explodes_cannot_fail_the_job(spool, monkeypatch, capfdbinary):
    # This routine's own instrument once killed a pytest session it was only observing.
    monkeypatch.setattr(cgroupmem, "cgroup_dir", lambda *a, **k: 1 / 0)
    job, _ = submit.submit(spool, _py("raise SystemExit(5)"), [])
    _drive(spool, job)
    assert submit.follow(job, spool) == 5
    assert protocol.MEMORY_LINE not in capfdbinary.readouterr().out.decode()
