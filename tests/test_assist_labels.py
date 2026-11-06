"""The outcome label for a rule assist's HOLD (D239) — the four coupled parts, end to end.

A `hold`-payload assist costs the run a turn and had no way to say whether that turn bought
anything: `state/assists.json` was an int per key, `remind_feedback` answered an assist key
with "no reminder … is live for this run", the health read model showed a bare count, and the
hold's own wording asked for prose in the next `say` instead of a structured label. One
assist in the library carries that payload and stood at 72 turns across three routines with
no way to tell a useful hold from a false positive — the one number that decides whether a
trigger keeps a turn-costing rung.

One test per part, then the acceptance the increment exists for: a held run labels its hold
and the label lands in the tally under the assist's own key and is visible in the read model.
"""

import json
from types import SimpleNamespace

import yaml

from conftest import finish, write_file
from helpers import server_for, set_capabilities
from rsched import assist_stats
from rsched import assists as lib
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.readmodels.run_health import cautions
from rsched.reminders import LABELS, STAT_FIELDS

TS = "20260905-190000"
#: The live key, as `Assist.key` renders it and as the tally file carries it.
KEY = "git-checkpoint/before-the-first-repo-edit"


def _rule(server, slug: str, assist_id: str, moment: str, predicate: str, line: str) -> None:
    """A library rule declaring one assist, its payload following the moment."""
    home = server.rules_home
    home.mkdir(parents=True, exist_ok=True)
    (home / f"{slug}.md").write_text(
        f"---\ntags: [a, b, c]\nassists:\n  - id: {assist_id}\n    moment: {moment}\n"
        f"    predicate: {predicate}\n    payload: {lib.MOMENT_PAYLOADS[moment][0]}\n"
        f"    line: {line}\n---\n# rule: {slug} — s\nbody\n", encoding="utf-8")


def _patch_yaml(routine_dir, **keys) -> None:
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    path.write_text(yaml.safe_dump({**raw, **keys}), encoding="utf-8")


def _loop(routine_dir, owed: dict | None = None):
    """The two fields the label path reads — a run's own ledger and where its tally lives."""
    return SimpleNamespace(assist_owed=dict(owed or {}),
                           ctx=SimpleNamespace(routine=SimpleNamespace(dir=routine_dir)))


# --- part 1: `remind_feedback` routed by the SHAPE of its id --------------------------------

def test_an_assist_key_is_routed_to_the_assist_tally_and_a_reminder_id_is_not(tmp_path):
    """An assist key carries `/`; a reminder id cannot (`reminders.ID_RE`). That shape is the
    whole routing rule — and the reminder path must be reached UNCHANGED, because every label
    a reminder ever recorded goes through it."""
    from rsched.engine import assist_label

    (tmp_path / "state").mkdir()
    took = []
    loop = _loop(tmp_path, {KEY: 1})
    note = assist_label.apply(loop, {"id": KEY, "label": "would_have"},
                              lambda lp, fb: took.append(fb) or "reminder path")
    assert took == []                                  # the reminder path was NOT taken
    assert KEY in note and "would_have" in note
    assert assist_stats.read(tmp_path)[KEY]["would_have"] == 1

    assert assist_label.apply(loop, {"id": "rem-mv", "label": "did"},
                              lambda lp, fb: "reminder path") == "reminder path"


def test_a_label_answers_one_hold_once_and_an_unheld_key_is_refused(tmp_path):
    """The reminder layer's own rule, mirrored: a label with no hold behind it is evidence
    about nothing (one run once labelled a single reminder 75 times against 5 fires)."""
    from rsched.engine import assist_label

    (tmp_path / "state").mkdir()
    loop = _loop(tmp_path, {KEY: 1})
    assert "labelled would_have" in assist_label.apply(loop, {"id": KEY, "label": "would_have"},
                                                       lambda lp, fb: "")
    second = assist_label.apply(loop, {"id": KEY, "label": "could_not"}, lambda lp, fb: "")
    assert "no hold of" in second and "once" in second
    assert assist_stats.read(tmp_path)[KEY]["could_not"] == 0


# --- part 2: the tally grows to the stat fields a reminder keeps, with a boot migration -----

def test_the_tally_keeps_the_same_stat_fields_a_reminder_does(tmp_path):
    """Mirrored field names, not a parallel vocabulary: the read model, the table and the
    prune check all read `fires` + the four labels."""
    (tmp_path / "state").mkdir()
    assist_stats.record(tmp_path, KEY, "fires")
    got = assist_stats.read(tmp_path)[KEY]
    assert sorted(got) == sorted(STAT_FIELDS)
    assert got["fires"] == 1 and all(got[lbl] == 0 for lbl in LABELS)


def test_the_migration_converts_the_live_int_per_key_file_in_place(tmp_path):
    """The real shape on disk — seven keys, an int each (313 bytes in a live routine dir).
    The int is the fire count and must survive as `fires`."""
    from rsched import migrate_assist_stats as mig

    routines = tmp_path / "routines"
    d = routines / "scheduler-builder"
    (d / "state").mkdir(parents=True)
    live = {"ask-policy/when-asks-pile-up": 5, "fix-the-cause/on-a-correction": 21,
            KEY: 11, "interface-craft/look-at-the-render": 25}
    (d / "state" / "assists.json").write_text(json.dumps(live), encoding="utf-8")

    record = mig.run_migration(SimpleNamespace(routines_home=routines))

    assert record["converted"] == 1 and record["keys"] == 4
    after = json.loads((d / "state" / "assists.json").read_text(encoding="utf-8"))
    assert after[KEY] == {"fires": 11, **dict.fromkeys(LABELS, 0)}
    assert mig.run_migration(SimpleNamespace(routines_home=routines)) == {}   # one-shot


def test_a_stand_in_server_without_the_homes_cannot_kill_the_boot():
    """R3132's shape: `cmd_daemon` is handed a four-attribute stand-in by the fresh-container
    boot test, so every attribute read here is a `getattr` — a hard read would fail the whole
    daemon boot for a question this migration was not asked."""
    from rsched import migrate_assist_stats as mig

    assert mig.run_migration(SimpleNamespace()) == {}
    assert mig.run_migration(SimpleNamespace(routines_home="")) == {}


def test_the_daemons_boot_sequence_actually_runs_it():
    """A migration nothing CALLS is a silent no-op that passes its own suite."""
    import inspect

    from rsched import cli_daemon

    assert "migrate_assist_stats" in inspect.getsource(cli_daemon.cmd_daemon)


# --- part 3: the health read model and its table gain the four columns ---------------------

def test_the_read_model_carries_the_four_labels_per_assist(tmp_path):
    """The console's cautions table is where a trigger's precision is reviewed; with only
    `fires` there was nothing to review it against."""
    from rsched.config import ServerConfig

    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.libraries_home = tmp_path / "library"
    d = server.routines_home / "r"
    (d / "state").mkdir(parents=True)
    assist_stats.write(d, {KEY: {"fires": 31, "could_not": 2, "would_have": 7,
                                 "did": 1, "didnt": 0}})

    row = next(r for r in cautions(server, d)["assists"] if r["key"] == KEY)
    assert row["fires"] == 31
    assert row["stats"]["could_not"] == 2 and row["stats"]["would_have"] == 7


def test_a_legacy_int_tally_still_reads_as_a_row(tmp_path):
    """The read model promises a routine whose store is old or hand-broken shows what it has
    rather than a broken page — the migration runs at boot, the page may be read before it."""
    from rsched.config import ServerConfig

    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.libraries_home = tmp_path / "library"
    d = server.routines_home / "r"
    (d / "state").mkdir(parents=True)
    (d / "state" / "assists.json").write_text(
        json.dumps({"x/pre-action": 4, "y/observation": "lots"}), encoding="utf-8")

    rows = [(r["key"], r["fires"], r["stats"]["did"]) for r in cautions(server, d)["assists"]]
    assert rows == [("x/pre-action", 4, 0), ("y/observation", 0, 0)]


# --- part 4: the `assist_hold` observation asks for the label ------------------------------

def test_the_assist_hold_asks_for_the_label_the_way_a_reminder_hold_does():
    """The hold's wording is the only place the label can be asked for: it is the turn the run
    is re-deciding the action on, and `remind_feedback` rides any action at no turn cost."""
    from rsched.engine.obs_hold import assist_hold, reminder_hold

    text = assist_hold({"action": "write_file path=/repo/x", "assists": [KEY],
                        "lines": ["[RULE git-checkpoint — a tree with uncommitted work] commit"]})
    assert "ACTION HELD — it did NOT run." in text
    assert "emit the SAME action again" in text            # the escape is still offered
    assert "remind_feedback" in text and KEY in text
    for label in LABELS:
        assert label in text
    # the same four labels and the same gloss as the reminder hold — one vocabulary
    assert "The labels: could_not" in text
    assert "The labels: could_not" in reminder_hold({"action": "a", "reminders": []})


# --- the acceptance: a held run labels its hold, end to end --------------------------------

def test_a_held_run_labels_the_hold_and_the_label_lands_in_the_tally(make_routine, scripted):
    """D239's acceptance, end to end: the run held by
    `git-checkpoint/before-the-first-repo-edit` carries `remind_feedback` with that key on its
    next action, and the label lands in `state/assists.json` under it and shows in the read
    model. Until all four parts landed the label had nowhere to go."""
    d = make_routine(slug="assistl")
    server = server_for(d)
    _rule(server, "git-checkpoint", "before-the-first-repo-edit", "pre-action",
          "uncheckpointed-repo-write", "commit a checkpoint before the first edit")
    _patch_yaml(d, rules=["git-checkpoint"])
    set_capabilities(d, reminders="local")   # the label rides `remind_feedback`'s own channel
    repo = d.parent.parent / "project"
    (repo / ".git").mkdir(parents=True)
    _patch_yaml(d, fs_write_roots=[str(repo)])
    target = repo / "auth.py"

    scripted([write_file(str(target), content="changed"),          # HELD by the assist
              {**write_file(str(target), content="changed"),       # re-emitted, with the label
               "remind_feedback": {"id": KEY, "label": "would_have"}},
              finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)

    events, _ = read_events(run_dir / "transcript.jsonl")
    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "assist_hold"]
    assert len(holds) == 1 and holds[0]["payload"]["assists"] == [KEY]
    tally = assist_stats.read(d)[KEY]
    assert tally == {"fires": 1, "could_not": 0, "would_have": 1, "did": 0, "didnt": 0}
    row = next(r for r in cautions(server, d)["assists"] if r["key"] == KEY)
    assert row["stats"]["would_have"] == 1 and row["fires"] == 1
    assert status == "ok"
