"""A model that ANSWERED WITH NOTHING must be distinguishable from one that answered (F921).
Reported from outside: a voice review retried on `role:uncensored` returned rc=0 with empty
output, twice, so the pipeline carried on, produced no rewrite, and had no error to act on. The
engine's turn loop had a guard of its own; nothing else did, and each reader of a reply phrased
the failure as whatever its own parse hit — "non-JSON", "malformed", "failed lint". Under test
is the one predicate they now share and the callers that name the case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsched.config import ModelRef, ServerConfig
from rsched.endpoints.base import Completion

REPO = Path(__file__).resolve().parents[1]


class _Silent:
    """An endpoint whose model answers with nothing — whitespace, no parsed object."""

    name = "scripted"

    def __init__(self, text: str = "  \n "):
        self.text = text
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        return Completion(text=self.text, usage={"in": 7, "out": 0}, stop_reason="stop")


def _ref() -> ModelRef:
    return ModelRef(endpoint="scripted", model="m", name="scripted/m")


def test_the_predicate_is_about_nothing_coming_back():
    """Empty = no text, no parsed object, no chained object. Whitespace is not an answer."""
    assert Completion(text="").is_empty()
    assert Completion(text="   \n\t ").is_empty()
    assert not Completion(text="an answer").is_empty()
    assert not Completion(text="", parsed={"a": 1}).is_empty()


def test_a_reply_whose_later_object_arrived_is_not_empty():
    """`parsed_rest` is part of it, and the inline test it replaced could not see that: a reply
    chaining calls can carry nothing in `parsed` while a later object did arrive, and reading
    that as silence would discard a real answer."""
    assert not Completion(text="", parsed=None, parsed_rest=[{"kind": "finish"}]).is_empty()


def test_the_turn_loop_reads_the_same_predicate():
    """One definition of silence: the loop's branch inlined `parsed is None and not
    text.strip()`, and that is the copy which drifted from `parsed_rest`."""
    src = (REPO / "src" / "rsched" / "engine" / "completion.py").read_text(encoding="utf-8")
    assert "completion.is_empty()" in src
    assert "parsed is None and not completion.text.strip()" not in src


def test_compaction_says_the_model_answered_with_nothing(tmp_path):
    """Not "non-JSON": that sends the next reader after a parse bug that is not there. Both
    paths let deterministic compaction take the pass, so the message is the whole difference."""
    from rsched.engine import historyarchive

    middle = [{"role": "user", "content": "a turn worth archiving"}]
    with pytest.raises(RuntimeError) as exc:
        historyarchive.archive_middle(middle, _Silent(), _ref(), tmp_path, 12)
    assert "EMPTY completion" in str(exc.value)
    assert "non-JSON" not in str(exc.value)


def test_compaction_still_calls_a_non_json_reply_non_json(tmp_path):
    """The case that was already handled keeps its own wording — the new branch must not have
    swallowed it, or one diagnosis would have replaced the other rather than joined it."""
    from rsched.engine import historyarchive

    middle = [{"role": "user", "content": "a turn worth archiving"}]
    with pytest.raises(RuntimeError) as exc:
        historyarchive.archive_middle(middle, _Silent("not json at all"), _ref(), tmp_path, 12)
    assert "non-JSON" in str(exc.value)


def test_suggest_reports_empty_rather_than_malformed(monkeypatch, tmp_path):
    """A reworded prompt cannot fix a silent model, so a caller retrying on "malformed" would
    retry the one thing that cannot help — and `_ask_json` would spend its repair round asking
    a silent model to try again."""
    from rsched.workflows import suggest as sug_mod

    ep = _Silent()

    class _Reg:
        def __init__(self, server):
            pass

        def for_system(self):
            return ep, _ref()

    monkeypatch.setattr(sug_mod, "EndpointRegistry", _Reg)
    s = ServerConfig()
    s.routines_home = tmp_path
    obj, why = sug_mod._ask_json(s, "pick one", {"type": "object"}, purpose="test")
    assert obj is None
    assert why == "empty", why
    assert ep.calls == 1, "a silent model must not be asked to repair a reply it never gave"


def test_generate_does_not_blame_the_lint_for_a_silent_model(monkeypatch, tmp_path):
    """"failed lint twice" of an EMPTY reply blames a draft the model never wrote, and the
    repair round would ask it to fix a file that does not exist."""
    import shutil

    from rsched.workflows import generate as gen_mod

    lib = tmp_path / "library"
    for kind in ("workflows", "rules", "permissions"):
        shutil.copytree(REPO / "library-seed" / kind, lib / kind,
                        ignore=shutil.ignore_patterns("__pycache__"))
    ep = _Silent()

    class _Reg:
        def __init__(self, server):
            pass

        def for_system(self):
            return ep, _ref()

    monkeypatch.setattr(gen_mod, "EndpointRegistry", _Reg)
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.libraries_home = lib
    with pytest.raises(RuntimeError) as exc:
        gen_mod.generate(s, "do a thing")
    assert "EMPTY completion" in str(exc.value)
    assert "failed lint" not in str(exc.value)
    assert ep.calls == 1, "a silent model must not be asked to repair a draft it never wrote"


def test_the_claim_judge_logs_silence_as_silence():
    """The verifier fails OPEN either way — the run's word stands — so the log line is the only
    place a silent judge can be told from one that answered badly, and a judge that has stopped
    answering is a fleet fact worth seeing."""
    src = (REPO / "src" / "rsched" / "engine" / "verifier.py").read_text(encoding="utf-8")
    assert "completion.is_empty()" in src and "EMPTY completion" in src


def test_every_engine_reader_of_a_reply_can_name_the_case():
    """The structural claim, and the reason this is one predicate rather than five branches:
    a caller that reads a completion's text must be ABLE to tell silence apart, so each one
    either consults the predicate or hands the reply to something that does. A new caller
    added without it is what put `llm-complete` at rc=0 on an empty reply for months."""
    # `engine/compaction.py` is NOT here: the split that paid its size breach moved every
    # model-reply reader into `engine/historyarchive.py`, leaving compaction the deterministic
    # window arithmetic, which never sees a completion.
    readers = ["engine/completion.py", "engine/historyarchive.py", "engine/verifier.py",
               "workflows/suggest.py", "workflows/generate.py"]
    missing = [r for r in readers
               if "is_empty()" not in (REPO / "src" / "rsched" / r).read_text(encoding="utf-8")]
    assert not missing, f"these read a model reply without being able to name silence: {missing}"
    assert json is not None
