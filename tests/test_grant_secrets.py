"""A util grant carries the util's OWN declared secrets (F562) — one operator decision for
one capability.

The defect these pin: granting `util:<name>` left every secret that util declares `undecided`,
so the routine's first call hit the exposure gate and filed a SECOND blocking access request
for the same capability. `bkm-foerder-steward:20260925-062318` did exactly that; R2106
(`suedlink-wlf`) is the same shape from the permissions side.

The fix widens the REQUEST, not the interpretation (`engine/grantsecrets.py`): the access
request also carries `secret:<NAME>` per declared secret, so every exposure is still an
explicit grant row and `secretgate.secret_state` keeps answering about ROWS — the last test
here pins that, because the cheaper variant (infer `granted` from an adjacent util grant) is
the silent credential carry the design rejected.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rsched import secrets, utils_lib
from rsched.engine import grantsecrets, secretgate
from rsched.engine.interact import handle_ask
from rsched.paths import read_json

TS = "20261022-010000"


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    """Both scopes into tmp: scoped_path derives from secrets_path, so one patch moves both."""
    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "store" / "secrets.env")


def _util(name: str = "probe", *, declares: str = "(none)",
          usage: str = "usage: gu probe [--selftest]", calls: str = "(none)") -> str:
    return (f'"""{name} — a test util.\n\n{usage}\ntags: a, b, c\n'
            f'secrets: {declares}\ncalls: {calls}\nnet: none\nfs: none\n"""\nprint("hi")\n')


def _lib(tmp_path: Path, **utils: str) -> Path:
    home = tmp_path / "lib"
    utils_lib.ensure_library(home)
    for name, src in utils.items():
        utils_lib.write_util_file(home, name, src)
    return home


def _ctx(home: Path, *, slug: str = "granted", grants: dict | None = None,
         granted_now: object = (), denied_now: object = ()) -> SimpleNamespace:
    return SimpleNamespace(
        server=SimpleNamespace(libraries_home=home),
        routine=SimpleNamespace(slug=slug, grants=dict(grants or {})),
        depth=0, granted_now=set(granted_now), denied_now=set(denied_now))


# ---- what a grant carries ----------------------------------------------------------------


def test_a_util_grant_carries_the_secrets_that_util_declares(tmp_path):
    secrets.set_secret("PROBE_TOKEN", "t")
    ctx = _ctx(_lib(tmp_path, probe=_util(declares="PROBE_TOKEN")))
    assert grantsecrets.carried(ctx, "probe") == ["PROBE_TOKEN"]
    ids, question = grantsecrets.widen(ctx, ["util:probe"], "May I call probe?")
    assert ids == ["util:probe", "secret:PROBE_TOKEN"]
    # the Decisions card renders the question's PROSE, not the entity ids (answerform.js reads
    # `q.request` only to pick the buttons) — so a click that exposes a credential says so
    assert "PROBE_TOKEN" in question and "May I call probe?" in question


def test_a_verb_scoped_grant_carries_only_that_verbs_secrets(tmp_path):
    """R2622 stays intact: a `NAME@verb` secret rides only with a grant that REACHES the verb.
    A bare `util:probe` grants every verb, so it carries both."""
    secrets.set_secret("PROBE_READ_KEY", "r")
    secrets.set_secret("PROBE_SEND_KEY", "s")
    ctx = _ctx(_lib(tmp_path, probe=_util(
        usage="usage: gu probe read [--json]\n       gu probe send TEXT",
        declares="PROBE_READ_KEY@read, PROBE_SEND_KEY@send")))
    assert grantsecrets.carried(ctx, "probe:read") == ["PROBE_READ_KEY"]
    assert grantsecrets.carried(ctx, "probe:send") == ["PROBE_SEND_KEY"]
    assert grantsecrets.carried(ctx, "probe") == ["PROBE_READ_KEY", "PROBE_SEND_KEY"]
    ids, _q = grantsecrets.widen(ctx, ["util:probe:read"], "May I read?")
    assert ids == ["util:probe:read", "secret:PROBE_READ_KEY"]


def test_an_unbound_secret_rides_with_any_grant_of_its_util(tmp_path):
    """An unsuffixed declaration is what every call of the util receives, so a verb-scoped
    grant carries it too — the scope narrows nothing it was never bound to."""
    secrets.set_secret("PROBE_TOKEN", "t")
    ctx = _ctx(_lib(tmp_path, probe=_util(usage="usage: gu probe read [--json]",
                                          declares="PROBE_TOKEN")))
    assert grantsecrets.carried(ctx, "probe:read") == ["PROBE_TOKEN"]


def test_only_what_an_exposure_decision_is_about_is_carried(tmp_path):
    """`secretgate._exposable` is the single judge, so this cannot drift from the gate it
    feeds: an OPTIONAL `?` name (D51/F290) is withheld silently and never worth a decision, a
    name the central store lacks needs none (the call fails visibly inside instead), and a name
    the routine's own store shadows (D103) is already owned."""
    secrets.set_secret("PROBE_TOKEN", "t")
    secrets.set_secret("PROBE_OPTIONAL", "o")
    secrets.set_secret("PROBE_SHADOWED", "central")
    secrets.set_routine_secret("granted", "PROBE_SHADOWED", "mine")
    ctx = _ctx(_lib(tmp_path, probe=_util(
        declares="PROBE_TOKEN, PROBE_OPTIONAL?, PROBE_SHADOWED, PROBE_UNPROVISIONED")))
    assert grantsecrets.carried(ctx, "probe") == ["PROBE_TOKEN"]


def test_a_decided_secret_is_never_put_to_the_operator_again(tmp_path):
    """An allow on a util may not re-open a tombstone, and a name already granted is not asked
    twice: only UNDECIDED names ride."""
    secrets.set_secret("PROBE_TOKEN", "t")
    secrets.set_secret("PROBE_DENIED", "d")
    home = _lib(tmp_path, probe=_util(declares="PROBE_TOKEN, PROBE_DENIED"))
    ctx = _ctx(home, grants={"secret:PROBE_TOKEN": True, "secret:PROBE_DENIED": False})
    assert grantsecrets.carried(ctx, "probe") == []
    assert grantsecrets.widen(ctx, ["util:probe"], "May I?") == (["util:probe"], "May I?")
    # a denial the RUN made (denied_now) holds the same way
    now = _ctx(home, denied_now={"secret:PROBE_DENIED"})
    assert grantsecrets.carried(now, "probe") == ["PROBE_TOKEN"]


def test_the_calls_tree_counts_because_one_tree_is_one_env(tmp_path):
    """A `calls:` edge hands a callee's credentials to its caller (utils_run.util_needs), so a
    secret reached that way is part of what the grant really exposes."""
    secrets.set_secret("CALLEE_KEY", "c")
    home = _lib(tmp_path, probe=_util(calls="helper"),
                helper=_util("helper", usage="usage: gu helper [--selftest]",
                             declares="CALLEE_KEY"))
    assert grantsecrets.carried(_ctx(home), "probe") == ["CALLEE_KEY"]


def test_other_classes_a_missing_util_and_an_explicit_id_pass_through(tmp_path):
    secrets.set_secret("PROBE_TOKEN", "t")
    home = _lib(tmp_path, probe=_util(declares="PROBE_TOKEN"))
    ctx = _ctx(home)
    assert grantsecrets.widen(ctx, ["secret:PROBE_TOKEN"], "q") == (["secret:PROBE_TOKEN"], "q")
    assert grantsecrets.widen(ctx, ["fs-write:/tmp/x"], "q") == (["fs-write:/tmp/x"], "q")
    assert grantsecrets.widen(ctx, ["util:ghost"], "q") == (["util:ghost"], "q")
    assert grantsecrets.widen(ctx, [], "q") == ([], "q")
    # already asked for explicitly: no duplicate id, and the note is not repeated
    both = ["util:probe", "secret:PROBE_TOKEN"]
    assert grantsecrets.widen(ctx, both, "q") == (both, "q")


# ---- the seam: the decision record the operator answers -----------------------------------


class _Transcript:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def event(self, kind, payload):
        self.events.append((kind, payload))


def _ask_loop(tmp_path: Path, home: Path, *, slug: str = "granted") -> SimpleNamespace:
    d = tmp_path / "routines" / slug
    (d / "questions" / "pending").mkdir(parents=True)
    (d / "inbox").mkdir(parents=True)
    ctx = SimpleNamespace(
        server=SimpleNamespace(libraries_home=home, routines_home=tmp_path / "routines",
                               conversations_home=tmp_path / "conversations",
                               background_home=tmp_path / "background"),
        routine=SimpleNamespace(slug=slug, grants={}, dir=d, capabilities=None),
        root_routine_dir=d, depth=0, run_ts=TS, turn=3, asks_deferred=0,
        granted_now=set(), denied_now=set(), transcript=_Transcript())
    return SimpleNamespace(ctx=ctx, dialog_qids={})


def test_the_filed_record_carries_both_ids_so_one_decision_covers_the_capability(tmp_path):
    """THE DEFECT, at the seam the operator answers: the durable record's `request` ids are
    what `web/grants_apply.apply_forever` writes and what `engine/requests._seed` seeds, so a
    record naming only `util:probe` is a grant that leaves the util half-usable."""
    secrets.set_secret("PROBE_TOKEN", "t")
    home = _lib(tmp_path, probe=_util(declares="PROBE_TOKEN"))
    loop = _ask_loop(tmp_path, home)
    out = handle_ask(loop, {"kind": "ask_user", "mode": "deferred",
                            "question": "May I call probe?", "request": "util:probe"}, 0.01)
    assert out["request"] == ["util:probe", "secret:PROBE_TOKEN"]
    rec = read_json(loop.ctx.root_routine_dir / "questions" / "pending" / f"{out['qid']}.json")
    assert rec["request"] == ["util:probe", "secret:PROBE_TOKEN"]
    assert rec["type"] == "request"
    assert "PROBE_TOKEN" in rec["question"]        # the card shows prose, so the prose says it
    asked = next(p for kind, p in loop.ctx.transcript.events if kind == "question")
    assert asked["request"] == ["util:probe", "secret:PROBE_TOKEN"]
    assert "PROBE_TOKEN" in asked["subject"]       # the re-submission key covers both ids


def test_a_plain_ask_is_left_exactly_as_it_was(tmp_path):
    loop = _ask_loop(tmp_path, _lib(tmp_path))
    out = handle_ask(loop, {"kind": "ask_user", "mode": "deferred",
                            "question": "Which folder?", "options": ["a", "b"]}, 0.01)
    rec = read_json(loop.ctx.root_routine_dir / "questions" / "pending" / f"{out['qid']}.json")
    assert rec["question"] == "Which folder?" and rec["type"] == "question"
    assert "request" not in rec and "request" not in out


# ---- the design decision itself ----------------------------------------------------------


def test_the_exposure_gate_still_answers_about_rows_never_about_a_util_grant(tmp_path):
    """Why the request is widened rather than the interpretation. Teaching `secret_state` to
    answer `granted` whenever some held `util:` grant declares the name would be fewer lines —
    and would make a util grant SILENTLY carry credentials while answering about rows that do
    not exist (R2702: enforce at the boundary, never infer from an adjacent grant).

    So: a bare `util:probe` grant leaves the secret UNDECIDED (the half-usable state F562
    reported), and it is the widened request's own `secret:` id that settles it — after which
    the gate has nothing left to ask, which is the second blocking request not being filed.
    """
    secrets.set_secret("PROBE_TOKEN", "t")
    home = _lib(tmp_path, probe=_util(declares="PROBE_TOKEN"))
    util_only = _ctx(home, granted_now={"util:probe"})
    assert secretgate.secret_state(util_only, "PROBE_TOKEN") == "undecided"
    ids, _q = grantsecrets.widen(util_only, ["util:probe"], "May I call probe?")
    decided = _ctx(home, granted_now=ids)          # what requests._seed does with the decision
    assert secretgate.secret_state(decided, "PROBE_TOKEN") == "granted"
    loop = SimpleNamespace(ctx=decided)
    assert secretgate.gate_util_secrets(loop, {"kind": "util", "name": "probe"}, 0.01) is None
