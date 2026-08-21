"""Semantic stopping conditions (F334/D98 v1): user-owned prose bounds the engine makes
impossible to ignore — store normalization, the always-visible digest section, and the
deterministic finish-accounting check. The engine never judges a condition's semantics;
it forces the MODEL's accounting (`[s<n>] met/unmet — why`) into every finish."""

from __future__ import annotations

from rsched.engine import stopping

NOW = "2026-08-21T00:00:00+02:00"


def test_save_normalizes_and_assigns_stable_ids(tmp_path):
    rows = stopping.save(tmp_path, [
        {"text": "stop once the PDF is published and verified"},
        {"id": "s7", "text": "only diagnose — do not start fixing", "status": "met"},
        {"text": "   "},                          # blank → dropped
        {"id": "junk!", "text": "bad id gets a fresh one", "status": "nonsense"},
    ], now=NOW)
    assert [r["id"] for r in rows] == ["s1", "s7", "s2"]   # well-formed id kept, gaps filled
    assert rows[0]["status"] == "open" and rows[0]["ts"] == NOW
    assert rows[1]["status"] == "met"
    assert rows[2]["status"] == "open"                     # unknown status falls back
    assert stopping.load(tmp_path) == rows                 # round-trips
    assert [c["id"] for c in stopping.open_conditions(tmp_path)] == ["s1", "s2"]


def test_load_survives_missing_and_corrupt_store(tmp_path):
    assert stopping.load(tmp_path) == []
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "stopping.json").write_text("not json", encoding="utf-8")
    assert stopping.load(tmp_path) == []


def test_digest_section_lists_open_conditions_and_the_contract(tmp_path):
    assert stopping.digest_section(tmp_path) == ""         # no store → no section
    stopping.save(tmp_path, [{"text": "verify the upload"},
                             {"text": "old", "status": "dropped"}], now=NOW)
    sec = stopping.digest_section(tmp_path)
    assert "[s1] verify the upload" in sec and "old" not in sec
    assert "met LIMIT condition" in sec and "finish NOW" in sec


def test_unaccounted_checks_the_bracket_mention_per_open_condition(tmp_path):
    stopping.save(tmp_path, [{"text": "a"}, {"text": "b"}, {"text": "c", "status": "met"}],
                  now=NOW)
    assert stopping.unaccounted("no accounting at all", tmp_path) == ["s1", "s2"]
    assert stopping.unaccounted("[s1] met — done; [s2] unmet — blocked", tmp_path) == []
    assert stopping.unaccounted("[s1] met only", tmp_path) == ["s2"]
    assert stopping.unaccounted("", tmp_path) == ["s1", "s2"]
