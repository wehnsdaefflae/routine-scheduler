"""An engine process ranks itself first for the OOM killer, so a memory cgroup that runs out
takes a run and never the daemon holding every run (rsched/oomrank.py)."""
from __future__ import annotations

from types import SimpleNamespace

from rsched import cli, oomrank


def test_it_writes_the_kernel_maximum(tmp_path):
    adj = tmp_path / "oom_score_adj"
    adj.write_text("0\n")
    assert oomrank.mark_run_expendable(adj) == ""
    assert adj.read_text() == "1000\n"


def test_a_refused_write_is_reported_not_raised(tmp_path):
    problem = oomrank.mark_run_expendable(tmp_path / "missing" / "oom_score_adj")
    assert "OOM score" in problem and "daemon" in problem


def test_engine_run_ranks_itself_before_anything_else(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(oomrank, "mark_run_expendable", lambda: calls.append(1) or "")
    args = SimpleNamespace(config=str(tmp_path / "absent.yaml"), homes="x")
    assert cli.cmd_engine_run(args) == 2          # refused at the config check …
    assert calls == [1]                           # … which the rank already preceded
