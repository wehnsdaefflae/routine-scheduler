"""The playground layer: inert for production, and what it surfaces when a conversation opts in.
The isolation tests are the ones that matter — an experimental layer lives in the live process,
so the property under test is not "the experiment works" but "a run that did not ask for it is
unchanged"; if that reds, nothing else about the layer is worth measuring.
"""

from __future__ import annotations

import json

import yaml

from rsched.engine import recall
from rsched.playground import features, scoring, stores
from rsched.playground.scoring import LexicalBackend


def test_the_lexical_baseline_is_the_engines_own_tokenizer():
    """`scoring.STOPWORDS`/`tokens` are a deliberate copy of `engine/recall`'s, so the lexical
    backend measures the engine's behaviour rather than a variant of it — and that claim is only
    true while the two stay identical. Nothing enforced it, so editing recall's list would have
    silently turned the baseline into something production does not do, which is the one reading
    the whole comparison rests on; this pins them together the way the repo's other deliberate
    duplicate names its own pinning test."""
    assert scoring.STOPWORDS == recall._STOPWORDS
    assert scoring.MIN_TOKEN == recall._MIN_TOKEN
    for text in ("read the state file", "A utIl-call: write_file json.py", "", "a an the"):
        assert scoring.tokens(text) == recall._tokens(text)


class _Ctx:
    def __init__(self, routine, run_dir):
        self.routine = routine
        self.run_dir = run_dir
        self.turn = 7


class _Routine:
    def __init__(self, directory):
        self.dir = directory


class _Loop:
    def __init__(self, routine, run_dir, server=None):
        self.ctx = _Ctx(routine, run_dir)
        self.ctx.server = server


def _conversation(tmp_path, block=None):
    """A conversation dir whose routine.yaml carries `block` as its playground: key."""
    conv = tmp_path / "conv"
    (conv / "runs" / "20260101-000000").mkdir(parents=True)
    raw = {"slug": "conv", "kind": "conversation"}
    if block is not None:
        raw["playground"] = block
    (conv / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return conv


def test_a_conversation_without_the_block_enables_nothing(tmp_path):
    """The production case: no `playground:` key at all means the layer is never asked."""
    conv = _conversation(tmp_path)
    assert features.enabled_features(_Routine(conv)) == []


def test_the_block_must_say_on(tmp_path):
    """Features listed but `on` absent is still off — the gate is the flag, not the list."""
    conv = _conversation(tmp_path, {"features": ["util-hint"]})
    assert features.enabled_features(_Routine(conv)) == []


def test_an_enabled_conversation_gets_its_features(tmp_path):
    conv = _conversation(tmp_path, {"on": True, "features": ["util-hint", "memory-hint"]})
    got = [f.slug for f in features.enabled_features(_Routine(conv))]
    assert got == ["util-hint", "memory-hint"]


def test_an_unknown_feature_slug_is_ignored_not_fatal(tmp_path):
    """A conversation written by a newer build must stay usable on an older one."""
    conv = _conversation(tmp_path, {"on": True, "features": ["util-hint", "from-the-future"]})
    assert [f.slug for f in features.enabled_features(_Routine(conv))] == ["util-hint"]


def test_a_malformed_block_leaves_the_run_as_production(tmp_path):
    """Unreadable experiment config must never change a turn."""
    conv = _conversation(tmp_path)
    (conv / "routine.yaml").write_text("playground: [this is not a mapping]\n", encoding="utf-8")
    assert features.enabled_features(_Routine(conv)) == []


def test_the_engine_seam_adds_nothing_for_a_production_run(tmp_path):
    """THE isolation test: the tail the loop appends for a non-playground run is empty."""
    from rsched.playground import runtime

    conv = _conversation(tmp_path)
    loop = _Loop(_Routine(conv), conv / "runs" / "20260101-000000")
    out = runtime.at_observation(loop, {"kind": "shell", "say": "listing the repo",
                                        "command": "ls"}, {"kind": "shell", "exit": 0})
    assert out == ""


def test_the_seam_never_raises_into_a_turn(tmp_path):
    """A broken experiment degrades to production behaviour instead of ending a conversation."""
    from rsched.playground import runtime

    class _Exploding:
        @property
        def dir(self):
            raise RuntimeError("boom")

    loop = _Loop(_Exploding(), tmp_path)
    assert runtime.at_observation(loop, {"kind": "shell", "say": "x"}, {"kind": "shell"}) == ""


def test_the_query_recipe_reads_the_components_it_names():
    """`A+O` must carry the action AND the observation, and `R` the run's own prose."""
    action = {"kind": "shell", "command": "ffmpeg -i clip.mp4", "say": "extracting a frame"}
    obs = {"kind": "shell", "exit": 1, "stderr": "ffmpeg: command not found"}
    evidential = features.query_text("A+O", action, obs)
    assert "ffmpeg" in evidential
    assert "command not found" in evidential
    assert "extracting a frame" not in evidential
    assert features.query_text("R", action, obs) == "extracting a frame"


def test_r_and_i_are_the_same_text_until_the_intent_field_exists():
    """Said out loud as a test: today's composition has no separate intention field, so a
    comparison of the two recipes is measuring one signal twice. The `intent-field`
    experiment is what makes them differ."""
    action = {"kind": "util", "name": "mail", "say": "sending the digest"}
    assert features.query_text("R", action, {}) == features.query_text("I", action, {})


def test_stores_read_conditions_not_payloads(tmp_path):
    """A util's condition is its summary and usage, never its source."""
    lib = tmp_path / "lib" / "utils" / "sender"
    lib.mkdir(parents=True)
    lib.joinpath("main.py").write_text(
        '"""sender — deliver a message somewhere.\n\n'
        'usage:\n  gu sender send ADDRESS\n\ntags: email, chat\n"""\n'
        'SECRET_CONSTANT = "do-not-index-me"\n', encoding="utf-8")
    got = stores.utils(tmp_path / "lib")
    assert len(got) == 1
    assert "deliver a message somewhere" in got[0].condition
    assert "gu sender send" in got[0].condition
    assert "do-not-index-me" not in got[0].condition


def test_memory_conditions_come_from_the_engine_owned_index(tmp_path):
    """The `about` line is the condition — it already states when to consult the note."""
    mem = tmp_path / "routines" / "alpha" / ".memory"
    mem.mkdir(parents=True)
    mem.joinpath("INDEX.md").write_text(
        "- quirks.md: The two things that break on this host. Consult BEFORE deploying."
        " · run-verified 2026-01-01\n", encoding="utf-8")
    got = stores.memory(tmp_path / "routines")
    assert len(got) == 1
    assert got[0].owner == "alpha"
    assert got[0].ident == "quirks"
    # the provenance tail is kept OUT of the indexed text but stays in what the page shows
    assert "run-verified" not in got[0].condition
    assert "run-verified" in got[0].detail


def test_reminder_conditions_are_the_consequence_not_the_regex(tmp_path):
    """Indexing the regex would be circular: it is the mechanism being compared against."""
    gdir = tmp_path / "lib" / "reminders"
    gdir.mkdir(parents=True)
    gdir.joinpath("rem-1.json").write_text(json.dumps({
        "id": "rem-1", "regex": "^util:fs-ops mv ", "description": "a move can orphan a file",
    }), encoding="utf-8")
    got = stores.reminders(tmp_path / "lib", tmp_path / "none")
    assert len(got) == 1
    assert got[0].condition == "a move can orphan a file"
    assert "fs-ops" not in got[0].condition
    assert got[0].owner == ""      # global: the blast radius a firing records


def test_lexical_ranking_normalizes_by_query_length():
    """A long query must not outscore a short one on every item — the comparison depends on
    scores being commensurable across recipes of very different sizes."""
    from rsched.playground.stores import Condition

    conditions = [Condition(store="utils", ident="mail", condition="send an email message")]
    short = LexicalBackend().rank("send email", conditions, 3)
    padded = LexicalBackend().rank(
        "send email " + " ".join(f"word{i}" for i in range(40)), conditions, 3)
    assert short and padded
    assert short[0].score > padded[0].score


def test_the_floor_is_per_conversation(tmp_path):
    """The measurement left no single threshold that separates true hits from coincidence, so
    the floor is a per-conversation dial rather than a constant."""
    conv = _conversation(tmp_path, {"on": True, "features": ["util-hint"], "min_score": 1.05})
    assert features.min_score(_Routine(conv)) == 1.05
    plain = _conversation(tmp_path / "other", {"on": True, "features": []})
    assert features.min_score(_Routine(plain)) == features.MIN_SCORE


def test_a_true_positive_lands_below_the_default_floor():
    """THE finding the lab exists to expose. A hand-rolled SMTP call does rank `mail` first —
    the retrieval is right — and still scores under the default floor, so the layer would
    withhold it. Measured against the live catalog the same case scored 1.26 against a floor of
    1.4; the property, not that number, is what is pinned, because the score moves with how
    much usage text a util's condition carries."""
    from rsched.playground.stores import Condition

    conditions = [Condition(store="utils", ident="mail", condition=(
        "mail everything about a MAILBOX account (Gmail or FAU): list, read, attachments, "
        "draft, mark, delete/trash, filters, send. email, inbox, communication, send, smtp"))]
    hits = LexicalBackend().rank(
        "shell: python3 -c import smtplib sendmail email body shell sent email", conditions, 3)
    assert hits and hits[0].ident == "mail"       # the right util IS found…
    assert hits[0].score < features.MIN_SCORE     # …and the default floor withholds it


def test_lexical_matching_is_brittle_on_morphology():
    """Pinned because it is the lexical baseline's known ceiling — the finding the embeddings
    comparison was built to weigh (that bench went with the console subpage, F959): `email` does
    not match `mail` and `smtplib` does not match `smtp`, so a condition written with one
    spelling misses a query written with the other, however relevant it is."""
    from rsched.playground.stores import Condition

    conditions = [Condition(store="utils", ident="mail",
                            condition="mail everything about a mailbox account: send smtp")]
    assert LexicalBackend().rank("sending email with smtplib", conditions, 3) == []
