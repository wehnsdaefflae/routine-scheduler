"""The reminder READ is served from the hook stream, with the tallies merged from the store
(D152 step 4 / goal b4) — the reminder twin of `hooks/reportread`.

WHY THIS IS A MERGE AND NOT A REPOINT, measured on the live instance 2026-11-15:

- `state/reminders.json` carries a per-reminder `stats` block (`fires`, `could_not`,
  `would_have`, `did`, `didnt`) and a top-level `global_stats` tally about GLOBAL reminders.
  **150 of the 196 live reminders carry a non-zero tally**, 12 global ids carry one, 3 rows sit
  in `dead`.
- `hooks/store.record_reminder` deliberately does not mirror a tally — its own docstring says
  mirroring a counter would give one number two writers — so the STREAM CANNOT REPRODUCE THEM.
  A read that simply folded the stream would silently zero every tally, and a tally is what the
  routine's own evidence about a pattern IS (`remind_feedback` writes it).

So the rule is: **definitions from the stream, tallies from the store, and a reminder the store
names that the stream's fold does not is SERVED FROM THE STORE with the shortfall filed** — the
`reportread.folded` rule, with the added requirement that a stream-served definition keeps the
stored tally. Losing a definition would change what HOLDS a run; losing a tally would destroy the
evidence that decides whether a pattern earns its interruptions.

The precondition for all of this was goal b9: `reminderproj.drift` reads EMPTY over all 31 live
routines as of 0.549.0 (0 drifting ids, down from 122), so the two sides agree on definitions
before either serves.
"""

from __future__ import annotations

import json

from rsched import reminders
from rsched.hooks import reminderread


def _store(routine_dir, rows, *, global_stats=None):
    routine_dir.mkdir(parents=True, exist_ok=True)
    (routine_dir / "state").mkdir(exist_ok=True)
    (routine_dir / "state" / "reminders.json").write_text(json.dumps(
        {"reminders": rows, "refused": [], "global_stats": global_stats or {}}))


def _rec(rid, regex="^util:", desc="careful", **kw):
    base = {"id": rid, "regex": regex, "description": desc, "created_run": "r:1",
            "kind": "action", "owner": "", "disposition": "", "muted_run": "",
            "stats": {"fires": 0, "could_not": 0, "would_have": 0, "did": 0, "didnt": 0}}
    base.update(kw)
    return base


def test_materializing_lets_the_raw_mutation_paths_reach_a_stream_only_reminder(
        tmp_path, monkeypatch):
    """D152/b4 cutover hazard 3: a stream-only definition must gain a real row, because the
    three RAW mutation paths work off the store and silently no-op without one.

    What each of them does to an id that is not on disk, before materialization:
    `reminders.record` returns the caller's stats unchanged, so every fire goes UNCOUNTED —
    and the tally is what tunes a pattern; `reminders.dispose` returns "" because `find` sees
    nothing, so a run cannot even mute a bad pattern it holds. Both are checked here against
    the real functions, not a stand-in: the point is that the store's own writers now find
    something to write.
    """
    rd = tmp_path / "r"
    _store(rd, [])
    stream = {"rem-only-1": _rec("rem-only-1", regex="^shell: rm -rf", desc="it removes a tree",
                                 scope="local")}
    monkeypatch.setattr(reminderread, "stream_definitions", lambda home, routine: stream)

    live, _gstats = reminderread.read_local(tmp_path, rd)
    served = next(r for r in live if r.id == "rem-only-1")
    assert reminders.load_local(rd)[0] == [], "the store already held it; nothing was proven"
    # the gap itself: the raw writers cannot reach it
    assert reminders.record(rd, served, "fires")["fires"] == 0
    assert reminders.dispose(rd, "rem-only-1", "mute", "r:2") == ""

    assert reminderread.materialize_stream_only(tmp_path, rd) == ["rem-only-1"]

    row = next(r for r in reminders.load_local(rd)[0] if r.id == "rem-only-1")
    assert row.regex == "^shell: rm -rf" and row.description == "it removes a tree"
    assert row.stats["fires"] == 0, "a tally was invented for a reminder that never fired"
    # …and now both writers reach it
    assert reminders.record(rd, row, "fires")["fires"] == 1
    assert reminders.dispose(rd, "rem-only-1", "mute", "r:2") == "mute"
    assert reminderread.materialize_stream_only(tmp_path, rd) == [], "it ran twice"


def test_materializing_touches_neither_a_stored_row_nor_a_global_definition(
        tmp_path, monkeypatch):
    """It fills a GAP: a row already on disk keeps its tally and its own fields, and a global
    definition is never copied into a routine's private file — that store is the library's, and
    a copy there is where no curation reaches it.
    """
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-held", regex="^util:held",
                     stats={"fires": 9, "could_not": 1, "would_have": 2, "did": 0, "didnt": 0})])
    monkeypatch.setattr(reminderread, "stream_definitions", lambda home, routine: {
        "rem-held": _rec("rem-held", regex="^util:held", scope="local"),
        "rem-lib": _rec("rem-lib", regex="^shell: curl", scope="global", reach="universal"),
    })

    assert reminderread.materialize_stream_only(tmp_path, rd) == []

    rows = reminders.load_local(rd)[0]
    assert [r.id for r in rows] == ["rem-held"]
    assert rows[0].stats["fires"] == 9 and rows[0].stats["would_have"] == 2


def test_a_definition_comes_from_the_stream(tmp_path, monkeypatch):
    """The point of the cutover: what a run holds is derived, not read from the store."""
    rd = tmp_path / "r"
    _store(rd, [])
    monkeypatch.setattr(reminderread, "stream_definitions",
                        lambda home, routine: {"rem-1": _rec("rem-1", regex="^shell:")})
    live, _gstats = reminderread.read_local(tmp_path, rd)
    assert [r.id for r in live] == ["rem-1"]
    assert live[0].regex == "^shell:"


def test_a_stream_served_definition_keeps_the_stores_tally(tmp_path, monkeypatch):
    """The tally is the routine's own evidence about a pattern and the stream cannot hold it.

    150 of 196 live reminders carry a non-zero one, so zeroing them would be the loudest
    possible regression: `remind_feedback` exists to accumulate exactly this.
    """
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-1", stats={"fires": 7, "could_not": 1, "would_have": 2,
                                     "did": 3, "didnt": 1})])
    monkeypatch.setattr(reminderread, "stream_definitions",
                        lambda home, routine: {"rem-1": _rec("rem-1", regex="^shell:")})
    live, _gstats = reminderread.read_local(tmp_path, rd)
    assert live[0].regex == "^shell:"              # definition: the stream's
    assert live[0].stats["fires"] == 7             # tally: the store's
    assert live[0].stats["would_have"] == 2 and live[0].stats["did"] == 3


def test_a_reminder_the_stream_lacks_is_served_from_the_store(tmp_path, monkeypatch):
    """The rule that cannot lose a reminder — `reportread.folded`'s, for a store whose loss
    changes what HOLDS a run rather than what a page lists."""
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-1"), _rec("rem-2")])
    monkeypatch.setattr(reminderread, "stream_definitions",
                        lambda home, routine: {"rem-1": _rec("rem-1")})
    live, _gstats = reminderread.read_local(tmp_path, rd)
    assert sorted(r.id for r in live) == ["rem-1", "rem-2"]


def test_the_shortfall_is_filed_rather_than_passing_silently(tmp_path, monkeypatch):
    """A store serving the read while the stream that replaced it is behind must say so."""
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-1"), _rec("rem-2")])
    monkeypatch.setattr(reminderread, "stream_definitions",
                        lambda home, routine: {"rem-1": _rec("rem-1")})
    filed = []
    monkeypatch.setattr(reminderread, "log_health_event",
                        lambda *a, **kw: filed.append((a, kw)))
    reminderread.read_local(tmp_path, rd)
    assert filed, "a read served from the store filed nothing"
    event = filed[0][0][1] if len(filed[0][0]) > 1 else filed[0][1].get("event")
    assert "reminder_stream" in str(event)


def test_an_empty_stream_and_a_behind_stream_are_reported_apart(tmp_path, monkeypatch):
    """'The store is gone' and 'the store is behind' want different repairs — the reports half
    learned this and named them apart."""
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-1")])
    seen = []
    monkeypatch.setattr(reminderread, "log_health_event",
                        lambda *a, **kw: seen.append(str(a[1] if len(a) > 1 else kw.get("event"))))
    monkeypatch.setattr(reminderread, "stream_definitions", lambda home, routine: {})
    reminderread.read_local(tmp_path, rd)
    monkeypatch.setattr(reminderread, "stream_definitions",
                        lambda home, routine: {"rem-9": _rec("rem-9")})
    reminderread.read_local(tmp_path, rd)
    assert len(set(seen)) == 2, f"both shortfalls filed the same event: {seen}"


def test_the_global_tally_is_the_stores_alone(tmp_path, monkeypatch):
    """`global_stats` is this routine's evidence about a reminder it does not own; the stream
    carries no counter at all, so the store is its only source."""
    rd = tmp_path / "r"
    _store(rd, [], global_stats={"rem-g": {"fires": 4, "did": 1, "could_not": 0,
                                           "would_have": 2, "didnt": 1}})
    monkeypatch.setattr(reminderread, "stream_definitions", lambda home, routine: {})
    _live, gstats = reminderread.read_local(tmp_path, rd)
    assert gstats["rem-g"]["fires"] == 4 and gstats["rem-g"]["would_have"] == 2


def test_a_broken_store_reads_as_empty_rather_than_failing_the_run(tmp_path, monkeypatch):
    """`load_local`'s leniency is load-bearing: this read happens while a run is being
    constructed, where a raise means the run never starts."""
    rd = tmp_path / "r"
    (rd / "state").mkdir(parents=True)
    (rd / "state" / "reminders.json").write_text("{not json")
    monkeypatch.setattr(reminderread, "stream_definitions",
                        lambda home, routine: {"rem-1": _rec("rem-1")})
    live, gstats = reminderread.read_local(tmp_path, rd)
    assert [r.id for r in live] == ["rem-1"] and gstats == {}


def test_an_unreadable_stream_falls_back_to_the_store(tmp_path, monkeypatch):
    """A stream read that raises must not take the run's reminders with it."""
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-1")])

    def boom(home, routine):
        raise OSError("stream gone")

    monkeypatch.setattr(reminderread, "stream_definitions", boom)
    monkeypatch.setattr(reminderread, "log_health_event", lambda *a, **kw: None)
    live, _g = reminderread.read_local(tmp_path, rd)
    assert [r.id for r in live] == ["rem-1"]


def test_a_global_definition_is_not_served_as_a_local_one(tmp_path, monkeypatch):
    """`load_local`'s contract is LOCAL reminders plus the global TALLY — not global definitions.

    Found by measuring the live fleet rather than by a test: the read served 207 definitions
    where the stores hold 196, and the 11 extras were GLOBAL hooks (`rules-review` 10,
    `vodafone-outage-claims` 1). The stream's `definitions(routine=…)` answers the wider
    question — every hook the routine HOLDS — so without this filter a library reminder would
    land in the list whose writers persist to `state/reminders.json`, duplicating a shared
    definition into a private file no curation reaches.
    """
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-1")])
    monkeypatch.setattr(reminderread, "stream_definitions", lambda home, routine: {
        "rem-1": _rec("rem-1"),
        "rem-g": dict(_rec("rem-g"), scope="global", reach="universal")})
    monkeypatch.setattr(reminderread, "log_health_event", lambda *a, **kw: None)
    live, _g = reminderread.read_local(tmp_path, rd)
    assert [r.id for r in live] == ["rem-1"]


def test_load_local_stays_the_stores_reader_or_the_comparator_lies(tmp_path, monkeypatch):
    """`load_local` must NOT become the merged read, and this is the subtlest trap in the
    cutover.

    `reminderproj.stored_definitions` builds the STORE side of the drift comparison by calling
    `load_local`. Repoint `load_local` at the stream and the comparator compares the stream to
    itself: `drift` reads EMPTY no matter how far apart the two stores actually are — destroying
    the very instrument goal b9 was measured with, and silently. So the merged read is its own
    function (`reminderread.read_local`) and `load_local` keeps answering from the file.
    """
    rd = tmp_path / "r"
    _store(rd, [_rec("rem-1", regex="^stored:")])
    monkeypatch.setattr(reminderread, "stream_definitions",
                        lambda home, routine: {"rem-9": _rec("rem-9", regex="^streamed:")})
    live, _g = reminders.load_local(rd)
    assert [r.id for r in live] == ["rem-1"] and live[0].regex == "^stored:"

    from rsched.hooks import reminderproj
    assert sorted(reminderproj.stored_definitions(rd)) == ["rem-1"]
