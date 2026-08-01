"""D62 admin conversation mechanism: RSCHED_ADMIN_TOKEN validation, the one-shot per-leg
marker lifecycle, and the admin audit trail."""

from __future__ import annotations

import json

from rsched.engine.admin import (
    ADMIN_TOKEN_ENV,
    admin_marker,
    admin_token_valid,
    clear_admin_marker,
    log_admin_action,
    write_admin_marker,
)


def test_admin_token_fail_closed_when_unset(monkeypatch):
    """No configured token = admin DISABLED for the instance: nothing can obtain it."""
    monkeypatch.delenv(ADMIN_TOKEN_ENV, raising=False)
    assert admin_token_valid("anything") is False
    assert admin_token_valid("") is False
    assert admin_token_valid(None) is False
    # an empty configured token is treated as unset (fail-closed), never an open door
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "")
    assert admin_token_valid("") is False


def test_admin_token_matches_only_the_configured_value(monkeypatch):
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "s3cret-admin-token")
    assert admin_token_valid("s3cret-admin-token") is True
    assert admin_token_valid("wrong") is False
    assert admin_token_valid("") is False
    assert admin_token_valid(None) is False


def test_admin_marker_is_one_shot(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert admin_marker(run_dir) is False          # absent by default
    write_admin_marker(run_dir)
    assert admin_marker(run_dir) is True
    clear_admin_marker(run_dir)
    assert admin_marker(run_dir) is False           # cleared → the leg's unlock is spent
    clear_admin_marker(run_dir)                      # idempotent, never raises


def test_log_admin_action_appends_jsonl(tmp_path):
    home = tmp_path / "routines"
    log_admin_action(home, run_id="c-x:20260101-000000", kind="util", brief="shell ls")
    log_admin_action(home, run_id="c-x:20260101-000000", kind="write_file", brief="config")
    path = home / ".control" / "admin-audit.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["run_id"] == "c-x:20260101-000000"
    assert first["kind"] == "util"
    assert first["brief"] == "shell ls"
    assert "ts" in first
