"""Allowlist vetting (separators, substitution, patterns) and execution behavior."""

import os

from rsched.engine.shell_guard import run_shell, scrubbed_env, split_segments, vet

ALLOW = ["gu *", "git *", "uv run --script *"]


def test_allows_simple_and_piped_allowlisted():
    assert vet("gu list", ALLOW) == []
    assert vet("gu gmail list --days 3 --json", ALLOW) == []
    assert vet("git status", ALLOW) == []
    assert vet("gu freelance-de --json | gu pangram --json", ALLOW) == []
    assert vet("gu list && git add -A", ALLOW) == []
    assert vet("gu", ALLOW) == []  # bare program name matches its own pattern


def test_blocks_non_allowlisted_and_smuggled_segments():
    assert vet("rm -rf /", ALLOW)
    assert vet("gu list; rm -rf ~", ALLOW)
    assert vet("git status && curl http://evil", ALLOW)
    assert vet("", ALLOW)


def test_blocks_substitution():
    assert any("$(" in p for p in vet("gu claude --prompt \"$(cat /etc/passwd)\"", ALLOW))
    assert vet("git commit -m `whoami`", ALLOW)
    assert vet("gu list <(echo x)", ALLOW)


def test_split_segments_quoting():
    segs = split_segments("gu claude --prompt 'a; b && c' | git log")
    assert len(segs) == 2 and segs[0].startswith("gu claude") and segs[1] == "git log"


def test_run_shell_captures_and_times_out(tmp_path):
    r = run_shell("echo hello && echo err >&2", cwd=tmp_path, timeout_s=10)
    assert r.exit == 0 and "hello" in r.stdout and "err" in r.stderr and not r.timed_out
    r = run_shell("sleep 5", cwd=tmp_path, timeout_s=1)
    assert r.timed_out and r.exit == -1
    r = run_shell("pwd", cwd=tmp_path, timeout_s=10)
    assert str(tmp_path) in r.stdout


def test_scrubbed_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")
    monkeypatch.setenv("KEEP_ME", "yes")
    env = scrubbed_env()
    assert "ANTHROPIC_API_KEY" not in env and env["KEEP_ME"] == "yes"
    assert env["PATH"] == os.environ["PATH"]
