"""write_file's observation carries the file's TOTAL on-disk size after the write, so an
`append` that silently overwrote (size == bytes written, not prior+written) is provable from
the observation alone — the diagnostic that was missing when a routine reported an append
had clobbered a file's existing content.
"""

from __future__ import annotations

from helpers import action_ctx
from rsched.engine.fileops import do_write_file
from rsched.engine.observations import format_observation


def test_write_file_append_reports_grown_total_size(make_routine, tmp_path):
    ctx = action_ctx(make_routine(), tmp_path)
    target = ctx.routine.dir / "state" / "note.md"
    first = do_write_file({"path": str(target), "content": "header\n"}, ctx)
    assert first["size"] == first["bytes"] == len(b"header\n")
    second = do_write_file(
        {"path": str(target), "content": "added\n", "append": True}, ctx)
    assert second["append"] is True
    assert second["bytes"] == len(b"added\n")
    # `size` is the TOTAL after appending — grown, NOT overwritten
    assert second["size"] == len(b"header\nadded\n")
    assert f"file now {second['size']} bytes" in format_observation(second)


def test_write_file_overwrite_size_equals_payload(make_routine, tmp_path):
    ctx = action_ctx(make_routine(), tmp_path)
    target = ctx.routine.dir / "state" / "note.md"
    do_write_file({"path": str(target), "content": "aaaaaa\n"}, ctx)
    over = do_write_file({"path": str(target), "content": "bb\n"}, ctx)
    assert not over.get("append")
    assert over["size"] == over["bytes"] == len(b"bb\n")


def test_append_outside_own_dir_preserves_existing_content(make_routine, tmp_path):
    """R1 regression: append:true on an existing file OUTSIDE the routine dir (an
    fs_write_root — the reported case was a conversation LEDGER) passes the grounding
    gate UNREAD (append adds without destroying) and keeps every original byte; the
    observation proves it (size == prior + bytes, never == bytes)."""
    ctx = action_ctx(make_routine(), tmp_path)
    ext = tmp_path / "conversations" / "c-20260719-162554"
    ext.mkdir(parents=True)
    ctx.routine.fs_write_roots = [tmp_path / "conversations"]
    target = ext / "LEDGER.md"
    original = "# LEDGER — conversation\n\n### seed — conversation created\n"
    target.write_text(original, encoding="utf-8")

    entry = "### 20260719-190013 — appended entry\n"
    obs = do_write_file({"path": str(target), "content": entry, "append": True}, ctx)
    assert "error" not in obs                       # never read this run, still allowed
    text = target.read_text(encoding="utf-8")
    assert text == original + entry                 # appended, NOT overwritten
    assert obs["append"] is True
    assert obs["size"] == len((original + entry).encode("utf-8"))
    assert obs["size"] == len(original.encode("utf-8")) + obs["bytes"]


def test_a_string_record_appended_to_a_jsonl_file_stays_one_line(make_routine, tmp_path):
    """R2446/R2452, measured: three release rows appended as STRINGS with no trailing newline
    concatenated — rows 2 and 3 landed on one 5,832-character line carrying three `{` against
    two `}`, and `json.loads` died at char 2,771. The reader is `self-audit`'s orient stage,
    which folds this file into its changelog and flips handed rows to `addressed`; a file whose
    integrity depends on whether each append happened to end in a newline is one it cannot tell
    "nothing shipped" from "the row was eaten" in.

    A record-structured file (`.jsonl`/`.ndjson`) gets the separator it needs. An already
    newline-terminated string is not given a second one — that would leave blank lines a
    line-per-record reader counts.
    """
    import json

    ctx = action_ctx(make_routine(), tmp_path)
    rows = [{"version": "0.408.0", "commit": "87336a76"},
            {"version": "0.408.1", "commit": "fc9a4ded"},
            {"version": "0.409.0", "commit": "afbbe20d"}]
    for row in rows:
        obs = do_write_file({"path": "state/shipped.jsonl",
                             "content": json.dumps(row),      # a STRING, no trailing newline
                             "append": True}, ctx)
        assert "error" not in obs
    text = (ctx.routine.dir / "state" / "shipped.jsonl").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 3, f"rows ran together: {len(lines)} line(s) for 3 records"
    assert [json.loads(x) for x in lines] == rows
    assert text.endswith("\n"), "the last record is unterminated, so the NEXT append merges"


def test_a_string_append_already_ending_in_a_newline_gains_no_second_one(make_routine,
                                                                        tmp_path):
    ctx = action_ctx(make_routine(), tmp_path)
    for i in range(2):
        do_write_file({"path": "state/rows.ndjson", "content": f'{{"i": {i}}}\n',
                       "append": True}, ctx)
    text = (ctx.routine.dir / "state" / "rows.ndjson").read_text(encoding="utf-8")
    assert text == '{"i": 0}\n{"i": 1}\n', repr(text)


def test_a_prose_append_is_left_exactly_as_written(make_routine, tmp_path):
    """Only a record-per-line file gets the separator. A note, a ledger, a markdown log is
    appended byte-faithfully — a run that writes half a sentence and continues it next turn
    must not have a newline inserted into its text."""
    ctx = action_ctx(make_routine(), tmp_path)
    do_write_file({"path": "state/notes.md", "content": "a half"}, ctx)
    do_write_file({"path": "state/notes.md", "content": " sentence", "append": True}, ctx)
    assert (ctx.routine.dir / "state" / "notes.md").read_text(
        encoding="utf-8") == "a half sentence"


def test_structured_append_is_one_compact_jsonl_line(make_routine, tmp_path):
    """An APPENDED structured value is a record: one compact line, so a JSONL ledger stays
    one-object-per-line. The pretty form landed a 13-line row in a changelog.jsonl once, and
    every reader then counted it as 13 malformed rows. Overwrite keeps the readable form."""
    import json

    ctx = action_ctx(make_routine(), tmp_path)
    row = {"ts": "2026-09-12T00:00:00+00:00", "items": ["F1", "D2"], "summary": "one"}
    do_write_file({"kind": "write_file", "path": "audit/changelog.jsonl", "content": row,
                   "append": True}, ctx)
    do_write_file({"kind": "write_file", "path": "audit/changelog.jsonl", "content": row,
                   "append": True}, ctx)
    lines = (ctx.routine.dir / "audit" / "changelog.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(x) == row for x in lines)
    do_write_file({"kind": "write_file", "path": "audit/report.json", "content": row}, ctx)
    assert (ctx.routine.dir / "audit" / "report.json").read_text(
        encoding="utf-8").count("\n") > 1          # a whole document stays pretty-printed



def test_a_truncated_json_body_does_not_read_as_a_clean_write(make_routine, tmp_path):
    """F588: a body the MODEL generated incomplete is written whole and reads as a success.

    Python's write() is all-or-nothing, so bytes-written EQUALS the payload's length whenever
    the write succeeded — asserted below, because it is why "report the bytes written" is by
    itself no fix at all. The difference a run can act on has to come from the BODY: a `.json`
    whose whole purpose is to parse, and does not. F460's parse gate refuses only an overwrite
    of a file that parsed BEFORE, so a NEW file — the usual shape of a generated artefact —
    arrives here ungated and the observation says nothing.

    The specimen, from this instance's own history: `sprind`'s memory note
    (`.memory/analysis-pipeline.md`, quoted again in R461) — "a single write_file of a full
    25-key analysis OBJECT gets TRUNCATED (my reasoning + the JSON share the turn's output
    budget; fails ~>5KB or after long thinking)". The target was `work/<app>/analysis.json`,
    a NEW file each run: nothing in the observation said so, so the routine had to learn it
    from downstream breakage and work around it by authoring ≤5KB fragments and merging them.
    """
    import json

    ctx = action_ctx(make_routine(), tmp_path)
    whole = json.dumps({"phase": "steady", "open": ["F588", "F460"], "sweep": "2026-10-24"},
                       indent=2)
    cut = whole[:len(whole) // 2]                 # the generation stopped mid-document
    obs = do_write_file({"kind": "write_file", "path": "state/plan.json",
                         "content": cut}, ctx)
    assert "error" not in obs, obs               # not refused: the file did not parse BEFORE
    # the measurement that makes the item's literal wording empty: both numbers are the same
    assert obs["bytes"] == obs["size"] == len(cut.encode("utf-8"))
    # The engine's own wording is pinned either side of the parser's complaint. The complaint
    # itself is matched loosely on purpose: pinning CPython's "(char 39)" offset would make
    # this test flaky across interpreter versions for no gain.
    shown = format_observation(obs)
    head, _, rest = shown.partition("\n")
    assert head == "OBSERVATION (write_file): wrote 44 bytes to state/plan.json", head
    assert rest.startswith("WARNING — the body just written does not parse as JSON: "), rest
    assert "Unterminated string" in rest, rest        # the parser names where it stopped
    assert rest.endswith("A reply cut off mid-generation lands exactly like this: treat the "
                         "file as possibly incomplete and write it again whole rather than "
                         "reading this as a finished file."), rest


def test_a_complete_body_or_a_prose_body_is_reported_without_a_warning(make_routine, tmp_path):
    """No false positive: a body that parses, and a format with nothing to parse, are clean."""
    import json

    ctx = action_ctx(make_routine(), tmp_path)
    good = do_write_file({"kind": "write_file", "path": "state/ok.json",
                          "content": json.dumps({"a": 1})}, ctx)
    assert "unparsed" not in good and "does not parse" not in format_observation(good)
    prose = do_write_file({"kind": "write_file", "path": "state/half.md",
                           "content": "# A heading and a sentence that stops mid-"}, ctx)
    assert "unparsed" not in prose and "does not parse" not in format_observation(prose)


def test_a_truncated_record_appended_to_a_jsonl_file_is_named(make_routine, tmp_path):
    """The record-per-line family is exempt from F460's gate on purpose (a partial line is
    legal mid-append), which leaves an appended record the only thing that can say it: a row
    cut short merges nothing and corrupts nothing, it just is not JSON — and the reader of
    that ledger finds out instead of the run that wrote it."""
    ctx = action_ctx(make_routine(), tmp_path)
    obs = do_write_file({"kind": "write_file", "path": "state/shipped.jsonl",
                         "content": '{"version": "0.411.0", "commit": "c03b',
                         "append": True}, ctx)
    assert "error" not in obs, obs
    shown = format_observation(obs)
    assert "does not parse" in shown, shown
