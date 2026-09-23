"""Dedicated archival must not change foreground routing or silently lose its assignment."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched.config import ModelRef, load_server_config
from rsched.engine import window
from rsched.web.app import create_app


def test_compaction_setting_validates_persists_clears_and_guards_deletion(tmp_path):
    server = make_test_server(tmp_path, routine_token="routine-tok")
    with TestClient(create_app(server, with_scheduler=False)) as c:
        c.headers["Authorization"] = "Bearer test-token"
        assert c.get("/api/settings/endpoints").json()["compaction_model"] is None
        url = "/api/settings/compaction-model"
        assert c.put(url, json={"name": "missing"}).status_code == 400
        assert c.post("/api/settings/endpoints", json={"name": "archive-ep", "kind": "openai"}).status_code == 200
        assert c.post("/api/settings/models", json={"name": "archive", "endpoint": "archive-ep", "model": "archive-id"}).status_code == 200
        assert c.put(url, json={"name": "archive"}, headers={"Authorization": "Bearer routine-tok"}).status_code == 403
        assert c.put(url, json={"name": "archive"}).json()["compaction_model"] == "archive"
        assert load_server_config(server.source)[0].compaction_model == "archive"
        assert c.delete("/api/settings/models/archive").status_code == 400
        assert c.put(url, json={"name": ""}).json()["compaction_model"] is None
        assert load_server_config(server.source)[0].compaction_model == ""
        assert c.delete("/api/settings/models/archive").status_code == 200


def test_unknown_config_compaction_model_is_diagnosed(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("compaction_model: missing\n")
    _, problems = load_server_config(path)
    assert "compaction_model: 'missing' is not a catalog model" in problems


@pytest.mark.parametrize(("dedicated", "small", "unavailable", "expected"), [
    ("archive", False, False, "archive"),
    ("archive", True, False, "tool"),
    ("archive", False, True, "tool"),
    ("", False, False, "tool"),
])
def test_archival_selection_keeps_foreground_window(tmp_path, monkeypatch, dedicated, small,
                                                   unavailable, expected):
    main = ModelRef(endpoint="ep", model="main", name="main", context_tokens=20000, max_tokens=1000)
    tool = ModelRef(endpoint="ep", model="tool", name="tool", context_tokens=100000, max_tokens=1000)
    archive = ModelRef(endpoint="ep", model="archive", name="archive",
                       context_tokens=1000 if small else 100000, max_tokens=1000)
    events, selected, windows = [], [], []

    def for_name(name):
        assert name == "archive"
        if unavailable:
            raise RuntimeError("unavailable")
        return "archive-endpoint", archive

    ctx = SimpleNamespace(server=SimpleNamespace(compaction_model=dedicated),
        routine=SimpleNamespace(models={}), usage={}, phase="", tokens_remaining=lambda: None,
        registry=SimpleNamespace(for_model=lambda *a: ("tool-endpoint", tool), for_name=for_name),
        transcript=SimpleNamespace(event=lambda kind, data: events.append(data)))
    loop = SimpleNamespace(ctx=ctx, messages=[{"role": "user", "content": "x" * 2000}] * 50,
                           turn_records=[], _last_compact_after=0)
    monkeypatch.setattr(window, "_warn_before_eviction", lambda *a: False)

    def compact(messages, records, cap):
        windows.append(cap)
        return messages[:6] + messages[-24:], {"mode": "digest"}

    monkeypatch.setattr(window, "maybe_compact", compact)
    monkeypatch.setattr(window.archival, "start", lambda loop, middle, ep, ref, turn: selected.append(ref.name))
    window._archive_if_needed(loop, "main-endpoint", main)
    assert selected == [expected]
    # the FOREGROUND model's cap decides the elision — min(0.6 x 20 000, 20 000 - 1 000 output)
    assert windows == [12000]
    if dedicated:
        assert events[-1]["archival_model"] == expected
    else:
        assert "archival_model" not in events[-1]
    assert ("archival_selection_fallback" in events[-1]) == bool(dedicated and (small or unavailable))


def test_archival_falls_back_to_the_main_model_when_the_tool_window_is_too_small(monkeypatch):
    """Machine work belongs on the cheaper tier, but only if it FITS there: a tool-call model
    whose window cannot hold the middle hands the archive back to the main model."""
    main = ModelRef(endpoint="ep", model="main", name="main",
                    context_tokens=20000, max_tokens=1000)
    tool = ModelRef(endpoint="ep", model="tool", name="tool",
                    context_tokens=1000, max_tokens=500)
    events, selected = [], []
    ctx = SimpleNamespace(server=SimpleNamespace(compaction_model=""),
        routine=SimpleNamespace(models={}), usage={}, phase="", tokens_remaining=lambda: None,
        registry=SimpleNamespace(for_model=lambda *a: ("tool-endpoint", tool)),
        transcript=SimpleNamespace(event=lambda kind, data: events.append(data)))
    loop = SimpleNamespace(ctx=ctx, messages=[{"role": "user", "content": "x" * 2000}] * 50,
                           turn_records=[], _last_compact_after=0)
    monkeypatch.setattr(window, "_warn_before_eviction", lambda *a: False)
    monkeypatch.setattr(window, "maybe_compact",
                        lambda msgs, *_a: (msgs[:6] + msgs[-24:], {"mode": "digest"}))
    monkeypatch.setattr(window.archival, "start", lambda *a: selected.append(a[3].name))

    window._archive_if_needed(loop, "main-endpoint", main)

    assert selected == ["main"]
    assert events[-1]["archival"] == "background"
    assert "tool: the archival request does not fit" in events[-1]["archival_selection_fallback"]


def test_archival_is_skipped_when_no_model_can_hold_the_middle(tmp_path, monkeypatch):
    """A failover chain ending on a 32k model used to post a 90k-token archival to it and
    lose the middle to a 400 (llmsectest-weekday:20260922-060001). Every candidate now goes
    through the same `archival_fits` reserve, and when none fits the run keeps the
    deterministic digest and the transcript says so.
    """
    tiny = ModelRef(endpoint="ep", model="tiny", name="tiny", context_tokens=4000, max_tokens=500)
    events, selected = [], []
    ctx = SimpleNamespace(server=SimpleNamespace(compaction_model=""),
        routine=SimpleNamespace(models={}), usage={}, phase="", tokens_remaining=lambda: None,
        registry=SimpleNamespace(for_model=lambda *a: ("tiny-endpoint", tiny)),
        transcript=SimpleNamespace(event=lambda kind, data: events.append(data)))
    loop = SimpleNamespace(ctx=ctx, messages=[{"role": "user", "content": "x" * 2000}] * 50,
                           turn_records=[], _last_compact_after=0)
    monkeypatch.setattr(window, "_warn_before_eviction", lambda *a: False)
    monkeypatch.setattr(window, "maybe_compact",
                        lambda msgs, *_a: (msgs[:6] + msgs[-24:], {"mode": "digest"}))
    monkeypatch.setattr(window.archival, "start",
                        lambda *a: selected.append(a[3].name))

    window._archive_if_needed(loop, "tiny-endpoint", tiny)

    assert selected == []
    assert events[-1]["archival"] == "skipped"
    assert "4,000-token window" in events[-1]["archival_skipped"]
