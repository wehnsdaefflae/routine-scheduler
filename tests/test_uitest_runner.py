"""The browser-stage container's spool (deploy/uitest/): a job written by the client is run by
the runner and comes back as if it had run in the caller's own process — output, exit code,
cancellation — and the spool carries no credential and the container mounts no live state.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "uitest"))
import protocol  # noqa: E402
import runner  # noqa: E402
import submit  # noqa: E402


@pytest.fixture
def spool(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    root = checkout / ".uitest-jobs"
    root.mkdir(parents=True)
    monkeypatch.setenv(protocol.SPOOL_ENV, str(root))
    monkeypatch.chdir(checkout)
    (root / protocol.HEARTBEAT).touch()
    return root


def _drive(root: Path, job: Path, running=None, timeout=30.0) -> dict:
    """Step the runner until `job` has an exit code."""
    running = {} if running is None else running
    ends = time.monotonic() + timeout
    while not (job / "exit").exists():
        assert time.monotonic() < ends, (job / "log").read_text()
        runner.step(root, running)
        time.sleep(0.05)
    return running


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_a_job_comes_back_with_its_output_and_its_exit_code(spool, capfdbinary):
    job, _ = submit.submit(spool, _py("print('from the container'); raise SystemExit(3)"), [])
    _drive(spool, job)
    assert submit.follow(job, spool) == 3
    assert b"from the container" in capfdbinary.readouterr().out


def test_a_carried_file_is_importable_by_the_job(spool, tmp_path):
    plugin = tmp_path / "carried_plugin.py"
    plugin.write_text("VALUE = 41\n")
    job, note = submit.submit(spool, _py("import carried_plugin, sys; "
                                         "sys.exit(carried_plugin.VALUE + 1)"), [str(plugin)])
    _drive(spool, job)
    assert (job / "exit").read_text().strip() == "42"
    assert "carried carried_plugin.py" in note


def test_the_job_file_carries_no_credential(spool, monkeypatch):
    monkeypatch.setenv("BROWSER_CDP_TOKEN", "do-not-write-me")
    monkeypatch.setenv("RSCHED_API_TOKEN", "nor-me")
    monkeypatch.setenv("RSCHED_TEST_BIND", "127.0.0.1")
    monkeypatch.setenv("AUDIT_GATE_PROGRESS", "/x/progress.jsonl")
    job, _ = submit.submit(spool, _py("pass"), [])
    text = (job / "job.json").read_text()
    assert "do-not-write-me" not in text and "nor-me" not in text
    assert "RSCHED_TEST_BIND" in text and "AUDIT_GATE_PROGRESS" in text


def test_a_pythonpath_entry_outside_the_checkout_is_dropped_and_named(spool, monkeypatch):
    inside = spool.parent / "src"
    monkeypatch.setenv("PYTHONPATH", f"{inside}:/home/mark/routines/self-audit/scripts")
    job, note = submit.submit(spool, _py("pass"), [])
    assert "/home/mark/routines/self-audit/scripts" in note
    assert "routines" not in (job / "job.json").read_text()


def test_a_cancel_ends_the_job_group(spool):
    job, _ = submit.submit(spool, _py("import time\nwhile True: time.sleep(0.1)"), [])
    running = {}
    while not (job / "claimed").exists():
        runner.step(spool, running)
    (job / "cancel").touch()
    _drive(spool, job, running)
    assert (job / "exit").read_text().strip() == "143"         # 128 + SIGTERM


def test_a_job_its_runner_died_under_is_reported_lost(spool):
    job = spool / "1-1"
    job.mkdir()
    (job / "job.json").write_text("{}")
    (job / "claimed").write_text("999\n")
    runner.recover(spool)
    assert (job / "exit").read_text().strip() == str(protocol.LOST)
    assert "restarted" in (job / "log").read_text()


def test_a_cwd_outside_the_checkout_fails_the_job_rather_than_running_elsewhere(spool, tmp_path):
    job, _ = submit.submit(spool, _py("pass"), [])
    (job / "job.json").write_text(f'{{"argv": ["true"], "cwd": "{tmp_path}"}}')
    _drive(spool, job)
    assert (job / "exit").read_text().strip() == "2"
    assert "inside" in (job / "log").read_text()


def test_no_runner_is_named_and_leaves_no_job_behind(spool, capsys):
    (spool / protocol.HEARTBEAT).unlink()
    assert submit.main(["--", sys.executable, "-c", "pass"]) == protocol.NO_RUNNER
    assert "docker compose up -d uitest" in capsys.readouterr().out
    assert [p for p in spool.iterdir() if not p.name.startswith(".")] == []


def test_the_no_runner_code_is_the_one_the_gate_reads_as_unavailable():
    # self-audit's candidate_gate maps 77 (MISSING_CREDENTIAL) to `unavailable`: a stage that
    # could not run says nothing about the candidate. An absent container is the same case.
    assert protocol.NO_RUNNER == 77


def test_the_container_mounts_the_checkout_and_nothing_live():
    spec = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["uitest"]
    home = "${RSCHED_HOME:-/home/mark}"
    targets = {v.replace(home, "@").split(":")[1]: v.replace(home, "@").split(":")[2:]
               for v in spec["volumes"]}
    assert set(targets) == {"/home/mark/git-repos/routine-scheduler",
                            "/home/mark/.cache/ms-playwright",
                            "/etc/localtime", "/etc/timezone"}
    assert targets["/home/mark/.cache/ms-playwright"] == ["ro"]
    assert spec["image"] == "rsched:latest"          # production's interpreter and packages
    assert "deploy/uitest/runner.py" in spec["entrypoint"]
    assert "--frozen" in spec["entrypoint"]           # never rewrites the shared lock
