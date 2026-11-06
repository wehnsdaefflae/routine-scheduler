"""P1 — the engine writes the process record (`rsched/runledger.py`, `engine/ledgerentry.py`).

Three things are pinned here: WHAT the engine writes (one entry per finished routine run leg,
built from the summary, the accounting and the recipe-revision lines, and nothing for a child or
a conversation), HOW it rotates (oldest whole entries out to a monthly archive, the preamble
never touched), and that NO RUN can write it (the file-action gate on every write path, rendered
through the render floor; the kernel half is in test_own_dir_seal.py).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from conftest import finish, seeded_server, write_file
from helpers import run_context, server_for, transcript_events
from rsched import runledger
from rsched.engine.executor import dispatch
from rsched.engine.ledgerentry import entry
from rsched.engine.observations import format_observation
from rsched.engine.runtime import run_routine

TS = "20261106-080000"


def _entry(ts: str, body: str = "did the thing") -> str:
    return f"### {ts} · ok · 3 turns\n{body}\n"


# --- what the engine writes ---------------------------------------------------------------

def test_a_finished_run_leaves_one_engine_entry(make_routine, scripted):
    d = make_routine(slug="ledgered")
    summary = ("**Process:** read the feed, one source was down.\n**Work:** 3 items filed.\n"
               "RECIPE REVISION: stages/scan.md — skip the mirror that is always down.")
    scripted([write_file("artifacts/report.md"),
              {**finish(summary=summary), "accounting": ["d1 met: report.md written"]}])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    text = (d / runledger.FILE).read_text(encoding="utf-8")
    _pre, entries = runledger.split(text)
    assert len(entries) == 1
    head, *_ = entries[0].splitlines()
    assert head == f"### {TS} · ok · 2 turns"
    assert summary in entries[0]                                   # verbatim
    assert "Accounting:\n- d1 met: report.md written" in entries[0]
    assert "Recipe revisions:\n- stages/scan.md — skip the mirror" in entries[0]


def test_the_entry_shape_names_a_resumed_leg_and_drops_empty_blocks(make_routine, tmp_path):
    ctx = run_context(make_routine(slug="legs"), TS, turn=1)
    text = entry(ctx, status="partial", summary="half done", leg=3)
    assert text.splitlines()[0] == f"### {TS} · leg 3 · partial · 1 turn"
    assert "Accounting" not in text and "Recipe revisions" not in text
    # a null revision files nothing, so it lists nothing
    assert "Recipe revisions" not in entry(ctx, status="ok", summary="RECIPE REVISION: none.")


def test_a_summary_line_shaped_like_a_heading_cannot_split_the_entry(make_routine):
    ctx = run_context(make_routine(slug="guard"), TS, turn=2)
    text = entry(ctx, status="ok", summary=f"quoting:\n### {TS} · ok · 9 turns\nend")
    assert len(runledger.split(text)[1]) == 1


def test_a_child_and_a_conversation_keep_no_ledger(make_routine, tmp_path):
    from rsched import conversations as conv_mod
    from rsched.engine import ledgerentry

    d = make_routine(slug="kid")
    (d / runledger.FILE).unlink()
    child = run_context(d, TS, depth=1)
    ledgerentry.record(SimpleNamespace(ctx=child, resume=False), "ok", "x")
    assert not (d / runledger.FILE).exists()
    conv = conv_mod.create_conversation(seeded_server(tmp_path), slug="c-noledger",
                                        first_message="hello")
    assert not (conv / runledger.FILE).exists()                    # no longer seeded


# --- rotation ------------------------------------------------------------------------------

def test_rotation_moves_only_whole_old_entries_and_never_the_preamble(tmp_path, monkeypatch):
    """The preamble — a title, a frozen brief, hand-written history in any shape — stays in
    LEDGER.md as written; only entries in the engine's own heading shape are ever moved."""
    monkeypatch.setattr(runledger, "MAX_BYTES", 400)
    monkeypatch.setattr(runledger, "KEEP_BYTES", 300)
    preamble = ("# LEDGER — birthday\n\n## Frozen brief\nthe party is on the 12th\n\n"
                "### Fixes landed\n- a hand sub-heading\n\n## Run 76 (2026-10-22)\nold notes\n\n")
    (tmp_path / runledger.FILE).write_text(preamble + "x" * 3000 + "\n", encoding="utf-8")
    stamps = ["20261030-080000", "20261031-080000", "20261101-080000", "20261102-080000"]
    written: list[str] = []
    for ts in stamps:
        written += runledger.append(tmp_path, _entry(ts, "y" * 90), title="birthday")
    text = (tmp_path / runledger.FILE).read_text(encoding="utf-8")
    pre, entries = runledger.split(text)
    assert pre.startswith(preamble)                     # untouched, however large
    assert [runledger.run_ts_of(e) for e in entries] == stamps[2:]
    assert sum(len(e.encode()) for e in entries) <= 300
    oct_ = (tmp_path / runledger.ARCHIVE_DIR / "2026-10.md").read_text(encoding="utf-8")
    assert [runledger.run_ts_of(e) for e in runledger.split(oct_)[1]] == stamps[:2]
    assert set(written) == {".ledger/2026-10.md"}
    assert "Frozen brief" not in oct_


def test_the_newest_entry_always_stays_and_a_small_ledger_never_rotates(tmp_path, monkeypatch):
    runledger.append(tmp_path, _entry("20261101-080000"), title="t")
    assert not (tmp_path / runledger.ARCHIVE_DIR).exists()
    monkeypatch.setattr(runledger, "MAX_BYTES", 10)
    monkeypatch.setattr(runledger, "KEEP_BYTES", 5)
    runledger.append(tmp_path, _entry("20261102-080000", "z" * 500), title="t")
    _pre, entries = runledger.split((tmp_path / runledger.FILE).read_text(encoding="utf-8"))
    assert [runledger.run_ts_of(e) for e in entries] == ["20261102-080000"]


def test_the_digest_shows_earlier_entries_but_not_the_last_runs_own(tmp_path):
    assert runledger.digest_tail(tmp_path) == ""
    (tmp_path / runledger.FILE).write_text(
        "# LEDGER\n\n## hand history\nnot shown\n\n" + _entry("20261101-080000", "older")
        + "\n" + _entry("20261102-080000", "the-last-run"), encoding="utf-8")
    tail = runledger.digest_tail(tmp_path, skip_run="20261102-080000")
    assert "older" in tail and "the-last-run" not in tail and "hand history" not in tail
    assert tail.startswith("LEDGER — the engine's record of earlier runs")


# --- the seal ------------------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    {"kind": "write_file", "path": "LEDGER.md", "content": "### mine\n"},
    {"kind": "write_file", "path": "state/../LEDGER.md", "content": "x"},
    {"kind": "edit_file", "path": "LEDGER.md", "anchor": "# LEDGER", "replacement": "# X"},
    {"kind": "delete", "path": "LEDGER.md"},
    {"kind": "move", "src": "LEDGER.md", "dst": "state/old-ledger.md"},
    {"kind": "move", "src": "state/mine.md", "dst": "LEDGER.md"},
    {"kind": "write_file", "path": ".ledger/2026-11.md", "content": "x"},
    {"kind": "delete", "path": ".ledger", "recursive": True},
], ids=["write", "traversal", "edit", "delete", "move-src", "move-dst", "archive", "rm-archive"])
def test_no_file_action_can_write_the_record(make_routine, action):
    d = make_routine(slug="sealed-ledger")
    (d / "state" / "mine.md").write_text("mine\n", encoding="utf-8")
    (d / runledger.ARCHIVE_DIR).mkdir()
    (d / runledger.ARCHIVE_DIR / "2026-11.md").write_text("# archive\n", encoding="utf-8")
    before = (d / runledger.FILE).read_text(encoding="utf-8")
    obs = dispatch({"say": "s", **action}, run_context(d, TS))
    text = format_observation(obs)
    assert "the engine's" in text and "Nothing was written" in text, text
    assert (d / runledger.FILE).read_text(encoding="utf-8") == before
    assert (d / runledger.ARCHIVE_DIR / "2026-11.md").is_file()


def test_the_refusal_names_where_the_record_goes_instead(make_routine, scripted):
    d = make_routine(slug="told")
    scripted([write_file("LEDGER.md", content="### run — what I did"), finish()])
    run_routine(d, server_for(d), run_ts=TS)
    obs = next(e["payload"] for e in transcript_events(d / "runs" / TS)
               if e["type"] == "observation" and e["payload"].get("kind") == "write_file")
    said = json.dumps(obs)
    assert "finish summary" in said and "`note`" in said
    _pre, entries = runledger.split((d / runledger.FILE).read_text(encoding="utf-8"))
    assert len(entries) == 1 and "what I did" not in entries[0]   # only the engine's entry
