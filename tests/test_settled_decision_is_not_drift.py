"""F609: a decision the operator already RECORDED is settled — not a gap, and never re-asked
without a reason.

The row that proves the defect (`.control/pending-creations/pc-20261002-223218-c0137c.json`,
written 2026-10-03, status null — never triaged). `freelance-radar` holds `outbound-mail`, whose
`requires:` names `fau-mail:send`, so every save raises `fau-mail` back into its utils list;
`fau-mail` declares `FAU_USER` and `FAU_PASSWORD`; the routine sends only Gmail. The operator
denied both secrets and his settings were accepted (web, 2026-10-01 05:11). The surface read that
denial back as state `denied` with severity `blocks` and a fix of
`{"kind": "clear_grant", "entity": "secret:FAU_PASSWORD"}` — and `library_watch._resolve`
collects exactly the `blocks` rows, so his own settled answer was filed as library drift: a
decision reported as blocked work, carrying a proposal that would revert him, blamed on whichever
commit happened to move the library HEAD (`f43bfce8`, which touched neither the routine nor
either secret).

The three criteria are the row's own, and each is read here against the behaviour rather than the
wording:

1. a node denied by an accepted settings decision is SETTLED, not a gap — no severity `blocks`,
   no `clear_grant` proposal (`test_a_declined_secret_is_a_settled_note_with_no_offer`), and so
   never a drift row (`test_a_declined_secret_is_never_filed_as_library_drift`, plus the
   withdrawal that retires the rows already queued);
2. the cited cause must be a change that actually TOUCHED the entity, or the row must cite no
   cause at all. F604 did that (`library_drift.trigger`, b1e59993, 2026-10-21 — three weeks
   AFTER the row complaining about it), so this file verifies it rather than claims it:
   `test_the_row_names_no_commit_that_did_not_touch_the_entity`;
3. a re-ask needs a reason the previous answer stopped applying
   (`test_a_re_asked_gap_says_what_changed_since_the_answer`). Keying on the gap's fingerprint
   decides WHETHER to ask again; it never said WHY, so a widened reason came back looking like
   the question he had already answered.

The watcher's own invariants live in `test_library_watch.py` and the F604 keying in
`test_library_drift.py`; this file reuses their world and covers neither again.
"""

from __future__ import annotations

import yaml

from helpers import util_src, write_util
from rsched import library_drift, pending
from rsched.daemon.library_watch import LibraryWatch
from rsched.readmodels.remedies import REMEDIES, surface_lines
from rsched.readmodels.surface import BOOT_SEVERITIES, NOTE, routine_surface
from test_library_watch import _break_sig, _git, build_world
from test_surface import _by_id, _cfg, _server


def _holder_grants(routines, grants: dict) -> None:
    """The operator's own answer, where an accepted settings save leaves it: `grants:` in the
    holder's routine.yaml. Nothing else in the system writes a `false` row — the web save
    (`web/api_routine_patch`) and a decline-forever on an access request are the only two
    writers, and both are him.
    """
    (routines / "holder" / "routine.yaml").write_text(yaml.safe_dump(
        {"description": "t", "permissions": [], "rules": [],
         "capabilities": {"utils": ["sig"]}, "grants": grants}), encoding="utf-8")


def _unrelated(lib, name: str = "note.md") -> None:
    """A library commit about something else entirely — nobody holds this rule, so it breaks
    nothing. This is the commit shape that used to resurrect a settled question and be named as
    its cause.
    """
    (lib / "rules" / name).write_text("---\ntags: [x, y, z]\n---\n# rule: x — y\n",
                                      encoding="utf-8")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", f"rules: add {name}")


# ---- criterion 1: a recorded decision is a STATEMENT, not a gap ------------------------------

def test_a_declined_secret_is_a_settled_note_with_no_offer(tmp_path, monkeypatch):
    """The source of all three halves, in one row of the read model.

    `blocks` said the routine cannot do the thing; what is true is that the operator decided it
    may not have the credential, and F756 lets the call proceed without the name rather than
    refuse it. So nothing here is UNMET, and the rule the surface already states covers it: a row
    reporting a deliberate act carries no `fix`, because the only "fix" for one is to undo it and
    offering that reads as a defect report on a routine set up exactly as intended
    (`surface_nodes._node`, docs/rules-permissions.md).
    """
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"FAU_PASSWORD": "x"})
    server = _server(tmp_path)
    write_util(server, "fau-mail", secrets="FAU_PASSWORD")
    surface = routine_surface(server, _cfg(tmp_path, capabilities={"utils": ["fau-mail"]},
                                           grants={"secret:FAU_PASSWORD": False}))
    node = _by_id(surface, "secret:FAU_PASSWORD")
    assert node is not None and node["state"] == "denied"
    assert node["severity"] == NOTE, "the operator's own answer is not a blocking gap"
    assert node["fix"] == {}, "nothing may offer to revert a decision he recorded"
    assert surface["verdict"]["blocks"] == 0 and surface["verdict"]["ready"] is True

    # the half worth keeping: the row still STATES what the decision costs
    assert "declined" in node["effect"] and "WITHOUT it" in node["effect"]
    # `clear_grant` was emitted at this one call site and nowhere else, so the kind retires with
    # it — the vocabulary is held equal at both ends (tests/test_surface.py)
    assert "clear_grant" not in REMEDIES
    # and it leaves the boot note, which exists for gaps a RUN would otherwise hit the hard way
    assert surface_lines(surface, BOOT_SEVERITIES) == []


def test_an_absent_secret_still_blocks(tmp_path, monkeypatch):
    """The contrast that gives the demotion its meaning. Nobody decided an ABSENT secret: the
    store has no such name, the call runs without it and there is a real act that settles it.
    """
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    server = _server(tmp_path)
    write_util(server, "fau-mail", secrets="FAU_PASSWORD")
    node = _by_id(routine_surface(server, _cfg(tmp_path,
                                               capabilities={"utils": ["fau-mail"]})),
                  "secret:FAU_PASSWORD")
    assert node["severity"] == "blocks" and node["fix"]["kind"] == "add_secret"


# ---- criterion 1, the consequence: the watcher never files a settled decision -----------------

def _deny_the_new_pin(lib, routines, monkeypatch) -> None:
    """The live case, reproduced: the library declares a secret, the store HAS it, and the
    operator has recorded that this routine may not see it.
    """
    _break_sig(lib)
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda *a, **k: {"NEW_PIN": "1234"})
    _holder_grants(routines, {"secret:NEW_PIN": False})


def test_a_declined_secret_is_never_filed_as_library_drift(tmp_path, monkeypatch):
    """The regeneration itself. Every library commit re-resolved every routine's whole surface,
    found the denial sitting there as `blocks`, and queued the same decision again — which is why
    the row that proves this was still being written five weeks after he answered it.
    """
    server, lib, routines = build_world(tmp_path, monkeypatch)
    watch = LibraryWatch(server)
    watch._check()                                   # baseline
    _deny_the_new_pin(lib, routines, monkeypatch)
    watch._check()
    assert pending.load_all(routines) == []

    # and it stays empty however often the library moves, about anything
    for name in ("one.md", "two.md", "three.md"):
        _unrelated(lib, name)
        watch._check()
    assert pending.load_all(routines) == []
    assert library_drift.load(routines) == {}, "nothing was asked, so there is nothing to settle"


def test_a_drift_row_queued_for_a_declined_secret_is_withdrawn(tmp_path, monkeypatch):
    """The rows already on disk. The live artefact sat in `.control/pending-creations` from
    2026-10-03 with status null, so the fix has to retire what the defect produced rather than
    only stop producing more — and the daemon's first check is where that happens, since no
    library commit is needed to make a settled decision settled.
    """
    server, lib, routines = build_world(tmp_path, monkeypatch)
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)
    watch._check()
    rec = pending.load_all(routines)[0]              # the gap as it was before he answered
    assert rec["fields"]["entity"] == "holder:secret:NEW_PIN"

    monkeypatch.setattr("rsched.secrets.load_secrets", lambda *a, **k: {"NEW_PIN": "1234"})
    _holder_grants(routines, {"secret:NEW_PIN": False})
    LibraryWatch(server)._check()                    # a new process, the same HEAD: the sweep
    assert pending.load_all(routines) == []


# ---- criterion 2: the cited cause touched the entity, or nothing is cited ---------------------

def test_the_row_names_no_commit_that_did_not_touch_the_entity(tmp_path, monkeypatch):
    """F604's half, verified rather than rebuilt (b1e59993, 2026-10-21 — after the row that
    reported it was written, which is why the artefact carries the old wording).

    The quoted row named `f43bfce8`, whose own summary — "removed=1 (routine-improver stale
    memory note), withheld=2 (sprind webhook token files, unmirrored), weightloss
    google-fit.json.php now redacted at source" — touched neither the routine nor either secret.
    Naming an innocent commit is worse than naming none: it sends the reader to a diff that
    cannot explain the row.
    """
    server, lib, routines = build_world(tmp_path, monkeypatch)
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)                                  # the cause…
    _unrelated(lib, "innocent.md")                   # …and merely the newest commit
    watch._check()

    rec = pending.load_all(routines)[0]
    trigger = rec["fields"]["trigger"]
    assert "which changed the sig util" in trigger
    assert "innocent" not in trigger and "innocent" not in rec["summary"]


# ---- criterion 3: a re-ask says what changed -------------------------------------------------

def test_a_re_asked_gap_says_what_changed_since_the_answer(tmp_path, monkeypatch):
    """Settled must not mean silenced — a wider claim on the same entity is a question he has not
    read. But the card said nothing about WHY it was back, so a re-ask he was right to receive
    was indistinguishable from the defect: the same routine, the same secret, asked again.

    The fingerprint decides WHETHER to ask (`library_drift.fingerprint`, F604). This is the other
    half: the record states which of its parts moved since the answer it is not covered by.
    """
    server, lib, routines = build_world(tmp_path, monkeypatch)
    watch = LibraryWatch(server)
    watch._check()
    _break_sig(lib)
    watch._check()
    rec = pending.load_all(routines)[0]
    assert not rec["fields"].get("reask"), "a FIRST ask has no previous answer to explain"
    library_drift.settle(routines, rec, "drift acknowledged")
    pending.drop(routines, str(rec["id"]))

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

    again = pending.load_all(routines)[0]
    reason = again["fields"]["reask"]
    assert "needed by sig" in reason and "needed by sig, sig2" in reason
    assert reason in again["summary"], "the reason has to reach the surface he reads"


def test_a_reason_is_offered_only_against_an_answer_that_does_not_cover_the_gap():
    """The unit beneath it, read on the store directly: the same gap needs no reason (it is never
    asked), a DIFFERENT entity's answer explains nothing, and a changed STATE is its own reason.
    """
    node = {"id": "secret:NEW_PIN", "state": "absent", "why": "needed by sig, sig2"}
    answered = {library_drift.fingerprint("holder", {"id": "secret:NEW_PIN", "state": "absent",
                                                     "why": "needed by sig"}):
                {"routine": "holder", "entity": "secret:NEW_PIN", "at": "2026-10-01T00:00:00Z"}}
    reason = library_drift.reask_reason(answered, "holder", node)
    assert "needed by sig" in reason and "needed by sig, sig2" in reason

    # the answer IS this gap: never asked, so never explained
    same = {library_drift.fingerprint("holder", node): {"routine": "holder", "at": "x"}}
    assert library_drift.reask_reason(same, "holder", node) == ""
    # another routine's answer, and another entity's, say nothing about this one
    assert library_drift.reask_reason(answered, "other", node) == ""
    assert library_drift.reask_reason(
        answered, "holder", {"id": "secret:OTHER", "state": "absent", "why": "w"}) == ""
    # a state change is a different question with a different answer
    moved = library_drift.reask_reason(
        answered, "holder", {"id": "secret:NEW_PIN", "state": "denied", "why": "needed by sig"})
    assert "absent" in moved and "denied" in moved
