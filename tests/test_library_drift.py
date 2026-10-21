"""F604: a library gap the operator ANSWERED stays answered, and the record names what opened it.

Two halves of one complaint (operator message 2026-10-01, library-export handover item 7; R2125).
A drift card is a NOTICE — nothing to materialize — so his only answer to one is `dismiss`, and
all that did was unlink the record. The answer therefore existed nowhere: `library_watch._report`
skipped a gap that still had an OPEN record, an answered one is not open, and the next library
commit — about anything at all — queued the identical question naming ITSELF as the cause. He was
asked twice and told the wrong commit did it.

So a decision is keyed on the GAP (`library_drift.fingerprint`: the routine, the entity, its
state, its stated reason) rather than on how recently the library moved, and the provenance line
is narrowed to what the history actually shows (`library_drift.trigger`).

The watcher's own invariants live in `test_library_watch.py`, whose fixture and git helpers these
reuse — one world, two concerns.
"""

from __future__ import annotations

import pytest
import yaml

from conftest import authed_client, seeded_server
from helpers import util_src
from rsched import libgit, library_drift, pending
from rsched.daemon.library_watch import LibraryWatch
from test_library_watch import _break_sig, _git, build_world


@pytest.fixture
def world(tmp_path, monkeypatch):
    return build_world(tmp_path, monkeypatch)


def _show(repo, rev: str) -> str:
    """One revision's full hash — what a record's `trigger` has to name to be checkable."""
    return libgit.git(repo, "rev-parse", rev).stdout.strip()


def _unrelated(lib, name: str = "note.md") -> None:
    """A library commit about something else entirely — nobody holds this rule, so it breaks
    nothing. This is the commit that used to resurrect a settled question and be named as its
    cause.
    """
    (lib / "rules" / name).write_text("---\ntags: [x, y, z]\n---\n# rule: x — y\n",
                                      encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", f"rules: add {name}")


def _answer(routines, rec) -> None:
    """What the operator's only answer to a drift card does: settle the gap, drop the record —
    exactly the pair `web/api_pending.discard` performs on a dismissal (pinned below).
    """
    library_drift.settle(routines, rec, "drift acknowledged")
    pending.drop(routines, str(rec["id"]))


# ---- the keying: identity, not recency -------------------------------------------------------

def test_a_settled_gap_is_not_re_asked_on_the_next_unrelated_change(world):
    """The defect itself: an answered question came back on the very next library commit."""
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()                                   # baseline
    _break_sig(lib)
    watch._check()
    _answer(routines, pending.load_all(routines)[0])

    _unrelated(lib)
    watch._check()
    assert pending.load_all(routines) == []          # the gap is unchanged: already answered


def test_a_gap_whose_reason_changes_is_a_new_question(world):
    """Settled must not mean silenced. The same entity needed for a WIDER reason is a claim the
    operator has not read, let alone answered.
    """
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)
    watch._check()
    _answer(routines, pending.load_all(routines)[0])

    # a second util declares the same secret, so the gap's stated reason widens
    (lib / "utils" / "sig2").mkdir()
    (lib / "utils" / "sig2" / "main.py").write_text(util_src("sig2", secrets="NEW_PIN"),
                                                    encoding="utf-8")
    (routines / "holder" / "routine.yaml").write_text(yaml.safe_dump(
        {"description": "t", "permissions": [], "rules": [],
         "capabilities": {"utils": ["sig", "sig2"]}}), encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "sig2: a second caller of the PIN")
    watch._check()

    recs = pending.load_all(routines)
    assert len(recs) == 1
    assert recs[0]["fields"]["node"]["why"] == "needed by sig, sig2"


def test_a_settled_gap_that_closes_is_forgotten_so_it_can_be_asked_again(world, monkeypatch):
    """A settlement lives exactly as long as its gap — the same invariant the records keep. A gap
    that closes and later opens again is a new question, not a silence nobody chose.
    """
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)
    watch._check()
    _answer(routines, pending.load_all(routines)[0])
    assert len(library_drift.load(routines)) == 1

    monkeypatch.setattr("rsched.secrets.load_secrets", lambda *a, **k: {"NEW_PIN": "1234"})
    LibraryWatch(server)._check()                    # a new process sweeps; the gap is closed
    assert library_drift.load(routines) == {}

    monkeypatch.setattr("rsched.secrets.load_secrets", dict)   # the secret goes away again
    _unrelated(lib)
    watch._check()
    assert len(pending.load_all(routines)) == 1      # so it is asked again


# ---- the provenance: what OPENED the gap, not what arrived with it ---------------------------

def test_the_record_names_the_commit_that_actually_opened_the_gap(world):
    """`After library change <head8>` named whatever commit happened to be HEAD when the gap was
    noticed, and the operator read that as the cause.
    """
    server, lib, routines = world
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)                                  # this is the cause…
    _unrelated(lib)                                  # …and this is merely the newest commit
    watch._check()

    rec = pending.load_all(routines)[0]
    breaking = _show(lib, "HEAD~1")
    assert rec["fields"]["trigger"] == (
        f"Opened by {breaking[:8]} (sig: require a PIN), which changed the sig util")
    assert rec["fields"]["trigger"] in rec["summary"]
    assert "rules: add note.md" not in rec["summary"]     # the innocent commit is not blamed


def test_a_gap_no_library_commit_touched_says_what_is_known_instead(world):
    """A gap can open on the ROUTINE's side (a settings change, a util newly held) and be noticed
    at the next library commit. Naming that commit would be the same false causation, so the
    record states the library STATE it was found at and says the cause is unknown.
    """
    server, lib, routines = world
    watch = LibraryWatch(server)
    _break_sig(lib)                                  # sig needs the PIN before this daemon runs
    watch._check()                                   # first boot: records HEAD, stays quiet
    _unrelated(lib)
    watch._check()

    rec = pending.load_all(routines)[0]
    head = _show(lib, "HEAD")
    assert rec["fields"]["trigger"] == (
        f"Found at library {head[:8]}; nothing in that change touched the sig util, so the "
        "commit that opened this gap is not known")


# ---- the wiring: dismissing a card is what settles its gap -----------------------------------

@pytest.fixture
def web_server(tmp_path):
    return seeded_server(tmp_path)


@pytest.fixture
def client(web_server):
    with authed_client(web_server) as c:
        yield c


def _queue_drift(home, *, node: dict, gap: str | None = None, routine: str = "holder") -> None:
    fields = {"entity": f"{routine}:{node['id']}", "node": node, "head": "abc12345deadbeef",
              "trigger": "Opened by abc12345 (zulip: require a key), which changed the zulip util"}
    if gap is not None:
        fields["gap"] = gap
    pending.queue(home, kind="library-drift", routine=routine, run_id="", fields=fields,
                  summary=f"{routine}: {node['id']} — {node['why']}.")


def test_dismissing_a_drift_card_settles_the_gap_it_is_about(web_server, client, make_routine):
    """The card has nothing to materialize, so `dismiss` IS the answer — and it used only to
    unlink the record, which is why the answer existed nowhere.
    """
    make_routine(slug="holder")
    node = {"id": "secret:ZULIP_API_KEY", "state": "absent", "severity": "blocks",
            "why": "needed by zulip", "source": {"utils": ["zulip"]}}
    _queue_drift(web_server.routines_home, node=node,
                 gap=library_drift.fingerprint("holder", node))
    pid = client.get("/api/pending-creations").json()[0]["id"]

    r = client.post(f"/api/pending-creations/{pid}/discard", json={"reason": "acknowledged"})
    assert r.status_code == 200
    key = "holder|secret:ZULIP_API_KEY|absent|needed by zulip"
    assert r.json()["settled"] == key
    assert client.get("/api/pending-creations").json() == []
    settled = library_drift.load(web_server.routines_home)
    assert list(settled) == [key] and settled[key]["reason"] == "acknowledged"
    # `holder` is the routine the drift BROKE, not a proposer — nothing is messaged
    assert r.json()["notified"] is False


def test_dismissing_a_record_from_before_the_settled_store_still_settles_its_gap(
        web_server, client, make_routine):
    """Records already on disk must still read: one queued before F604 carries no `gap` field, so
    the fingerprint is derived from the node it does carry — otherwise the first dismissal after
    the upgrade settles nothing and the defect survives its own fix.
    """
    make_routine(slug="holder")
    _queue_drift(web_server.routines_home, gap=None,
                 node={"id": "util:zulip", "state": "absent", "severity": "blocks",
                       "why": "held as a reserved util"})
    pid = client.get("/api/pending-creations").json()[0]["id"]

    r = client.post(f"/api/pending-creations/{pid}/discard", json={"reason": ""})
    assert r.status_code == 200
    assert r.json()["settled"] == "holder|util:zulip|absent|held as a reserved util"
    assert list(library_drift.load(web_server.routines_home)) == [
        "holder|util:zulip|absent|held as a reserved util"]


def test_discarding_an_ordinary_proposal_settles_no_gap(web_server, client):
    """The settled store is only ever about drift. A discarded creation must leave it untouched,
    or an unrelated drift report could find itself already "answered".
    """
    pending.queue(web_server.routines_home, kind="create_routine", routine="routine-improver",
                  run_id="routine-improver:20260827-030000",
                  fields={"slug": "nope2", "name": "N", "instruction": "p",
                          "workflow": "general-task"},
                  summary="create routine 'nope2'")
    pid = client.get("/api/pending-creations").json()[0]["id"]

    r = client.post(f"/api/pending-creations/{pid}/discard", json={"reason": "no"})
    assert r.status_code == 200 and r.json()["settled"] == ""
    assert library_drift.load(web_server.routines_home) == {}
    assert not (web_server.routines_home / library_drift.STORE).exists()
