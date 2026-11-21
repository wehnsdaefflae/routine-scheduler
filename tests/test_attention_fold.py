"""D289's one-pass fold: `attention_for_runs` reads the health stream ONCE for N runs and
returns exactly what `classify_run` returns per run — the gap `attention.py`'s own docstring
promised was closed, measured here rather than asserted.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsched.readmodels import attention


def _run(home: Path, slug: str, ts: str, status: dict) -> Path:
    d = home / slug / "runs" / ts
    d.mkdir(parents=True)
    status.setdefault("run_id", f"{slug}:{ts}")
    (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return d


def _stream(home: Path, rows: list[dict]) -> Path:
    p = home / ".control" / "health-events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def _goal(text: str, evidence: str) -> dict:
    return {"id": "b1", "text": text, "status": "met", "evidence": evidence}


def test_the_stream_is_read_once_for_many_runs(tmp_path, monkeypatch):
    """A routine-card surface folds one run per routine; per-call reads would open the stream
    once per card on every refresh."""
    dirs = [_run(tmp_path, f"r{i}", f"2026110{i}-100000",
                 {"goals": [_goal("done", "the artefact is published")]}) for i in range(1, 5)]
    _stream(tmp_path, [{"run_id": "r1:20261101-100000", "event": "stall", "detail": "x"}])

    opens: list[str] = []
    real_open = Path.open

    def counting_open(self, *a, **k):
        if self.name == "health-events.jsonl":
            opens.append(str(self))
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", counting_open)
    attention.attention_for_runs(tmp_path, dirs)

    assert len(opens) == 1, f"the stream was opened {len(opens)} times for {len(dirs)} runs"


def test_the_folded_rows_are_the_same_rows_classify_run_returns(tmp_path):
    """The fold is an optimisation, not a second classifier: a surface must not show one thing
    on the run page and another on the card."""
    d = _run(tmp_path, "r1", "20261101-100000",
             {"goals": [_goal("the digest is published", "artifacts/digest.md")],
              "outcome": "partial"})
    _stream(tmp_path, [{"run_id": "r1:20261101-100000", "event": "stall", "detail": "waited"},
                       {"run_id": "r1:20261101-100000", "event": "stall", "detail": "again"},
                       {"run_id": "other:20261101-100000", "event": "noise"}])

    folded = attention.attention_for_runs(tmp_path, [d])[str(d)]
    direct = attention.classify_run(d, home=tmp_path)

    assert folded == direct
    assert [r["class"] for r in folded] == ["b", "d", "e"], "his order, (a)…(g)"


def test_a_repeated_event_is_one_row_with_its_count(tmp_path):
    """Measured 2026-11-14: 266 of 846 rows were the same event again inside one run, one run
    producing 30. Thirty lines, nine of them the same word, would be longer and less
    actionable than the narrative the block leads."""
    d = _run(tmp_path, "r1", "20261101-100000", {})
    _stream(tmp_path, [{"run_id": "r1:20261101-100000", "event": "stall", "detail": "d"}] * 3)

    rows = attention.attention_for_runs(tmp_path, [d])[str(d)]

    assert len(rows) == 1
    assert rows[0]["occurrences"] == 3


def test_a_run_with_nothing_for_him_folds_to_no_rows(tmp_path):
    """An empty list renders no block at all, so such a run reads exactly as it did before."""
    d = _run(tmp_path, "r1", "20261101-100000", {"outcome": "ok"})
    assert attention.attention_for_runs(tmp_path, [d])[str(d)] == []


def test_a_missing_stream_costs_the_b_class_and_nothing_else(tmp_path):
    """Never raises: this feeds operator-facing surfaces, and a fold that can break one is
    worse than a fold that occasionally says less."""
    d = _run(tmp_path, "r1", "20261101-100000",
             {"goals": [_goal("done", "evidence")]})
    rows = attention.attention_for_runs(tmp_path, [d])[str(d)]
    assert [r["class"] for r in rows] == ["d"]


def test_every_run_asked_for_gets_a_key_even_with_no_status(tmp_path):
    """A caller folding a list must be able to index the answer by what it passed in."""
    good = _run(tmp_path, "r1", "20261101-100000", {"goals": [_goal("x", "y")]})
    missing = tmp_path / "r2" / "runs" / "20261101-100000"
    rows = attention.attention_for_runs(tmp_path, [good, missing])
    assert set(rows) == {str(good), str(missing)}
    assert rows[str(missing)] == []
