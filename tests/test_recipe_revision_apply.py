"""D200, the operator's half: APPLYING a run's proposed recipe revision.

`test_recipe_revision.py` is the engine seam — every finishing run is asked, and what it answers
becomes one standing proposal. This file is the other end: what the operator can actually do with
that card, which is where the decision row's constraint lands. A scheduled run holds no
`write_recipe`, so the proposal cannot be an edit; and nothing out here can turn prose a run wrote
about its own recipe into a diff without re-deriving the reasoning that run already did and left
in its transcript. So approving ARMS the unlock that already exists (`engine/revise.py`, the run
view's "editable recipe" checkbox): the proposing run is resumed with its recipe writable and
makes the edit itself — one unlock path, not a second one that can drift from it.

Every guard here exists because the marker is a ONE-SHOT unlock the next leg of that run dir
reads, whatever starts it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import authed_client, make_test_server, mk_run
from rsched import pending
from rsched.engine import recipeproposal
from rsched.paths import read_json


def _queued(routines: Path, slug: str, ts: str, lines: list[str]) -> dict:
    return pending.queue(routines, kind=recipeproposal.KIND, routine=slug,
                         run_id=f"{slug}:{ts}", fields={"lines": lines, "run_id": f"{slug}:{ts}"},
                         summary="proposes a revision to its own recipe")


@pytest.fixture
def web(tmp_path, make_routine):
    make_routine(slug="rev")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def test_approving_resumes_the_proposing_run_with_its_recipe_writable(web, monkeypatch):
    """The apply is NOT a web-side recipe write: the proposal is prose a run reasoned about its
    own recipe, and the reasoning is in that run's transcript. So approving arms the unlock that
    already exists and lets the run make the edit (one unlock path, not a second one)."""
    from rsched.engine.revise import REVISE_MARKER

    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", "20260709-120000", "finished")
    rec = _queued(tmp / "routines", "rev", "20260709-120000",
                  ["stages/scan.md — read the cursor first"])
    resumed: dict = {}

    async def fake_resume(cfg, ts=None, *, reason=""):
        resumed.update(slug=cfg.slug, ts=ts, reason=reason)
        return f"{cfg.slug}:{ts}"

    monkeypatch.setattr(c.app.state.runner, "resume_terminal", fake_resume)
    r = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert r.status_code == 200, r.text
    assert resumed == {"slug": "rev", "ts": "20260709-120000", "reason": "converse"}
    # the unlock the resumed leg reads at init, carrying the run's OWN words as the go-ahead
    instruction = read_json(run_dir / REVISE_MARKER)["instruction"]
    assert "stages/scan.md — read the cursor first" in instruction
    assert "writable to you on THIS leg only" in instruction
    # …and it rides a LIVE via, or a resumed leg's boot would never drain it
    msgs = [read_json(p) for p in (tmp / "routines" / "rev" / "inbox").glob("msg-*.json")]
    assert [m["via"] for m in msgs] == ["web-converse"]
    # the card is gone once the leg is armed — the work is now that run's
    assert pending.load_all(tmp / "routines") == []


def test_a_resume_that_cannot_happen_leaves_no_unlock_behind(web, monkeypatch):
    """The marker is a ONE-SHOT unlock the next leg of the run dir reads, whatever starts it.
    Left behind by a resume that was refused, it would hand a later plain converse recipe write
    it never presented for — the failure api_run_control already guards against."""
    from rsched.engine.revise import REVISE_MARKER

    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", "20260709-130000", "finished")
    rec = _queued(tmp / "routines", "rev", "20260709-130000", ["main.md — x"])

    async def refuse(cfg, ts=None, *, reason=""):
        return None

    monkeypatch.setattr(c.app.state.runner, "resume_terminal", refuse)
    r = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert r.status_code == 409
    assert not (run_dir / REVISE_MARKER).exists()
    assert len(pending.load_all(tmp / "routines")) == 1       # still the operator's to decide


def test_a_proposal_whose_run_is_gone_says_so_instead_of_resuming_nothing(web):
    """Retention prunes runs and a proposal outlives the run that made it. With no transcript
    there is no reasoning to resume, so the card says to apply it from the routine's own editor
    rather than silently opening a leg that has lost its context."""
    c, tmp = web
    rec = _queued(tmp / "routines", "rev", "20200101-000000", ["main.md — x"])
    r = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert r.status_code == 409 and "no longer on disk" in r.json()["detail"]


def test_a_live_run_cannot_be_unlocked(web):
    c, tmp = web
    mk_run(tmp / "routines" / "rev", "20260709-140000", "running")
    rec = _queued(tmp / "routines", "rev", "20260709-140000", ["main.md — x"])
    r = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert r.status_code == 409 and "FINISHED run" in r.json()["detail"]


def test_the_route_refuses_a_proposal_of_another_kind(web):
    c, tmp = web
    rec = pending.queue(tmp / "routines", kind="goal-reached", routine="rev", run_id="rev:1",
                        fields={"outcomes": []}, summary="finish line reached")
    r = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert r.status_code == 400 and "not a recipe revision" in r.json()["detail"]


def test_discarding_tells_the_proposing_routine(web):
    """The ordinary discard path: the routine learns its revision was not wanted, so its next
    run does not simply propose it again as though nothing had been decided."""
    c, tmp = web
    rec = _queued(tmp / "routines", "rev", "20260709-150000", ["main.md — x"])
    r = c.post(f"/api/pending-creations/{rec['id']}/discard",
               json={"reason": "the step is deliberate"})
    assert r.status_code == 200, r.text
    assert r.json()["notified"] is True
    msg = next((tmp / "routines" / "rev" / "inbox").glob("msg-pending-*.json"))
    assert "the step is deliberate" in read_json(msg)["text"]


def test_a_queued_proposal_shows_on_the_decisions_page_read_model(web):
    """The one surface the operator reads: the proposals band (`decisions_read.open_proposals`),
    which the header badge and the push sender count too."""
    c, tmp = web
    mk_run(tmp / "routines" / "rev", "20260709-160000", "finished")
    _queued(tmp / "routines", "rev", "20260709-160000", ["main.md — tighten step 2"])
    rows = c.get("/api/pending-creations").json()
    assert [row["kind"] for row in rows] == [recipeproposal.KIND]
    assert rows[0]["fields"]["lines"] == ["main.md — tighten step 2"]


def test_the_card_has_its_own_band_rather_than_the_generic_create_it_row():
    """A kind with no band falls through `pending.js summarize()` to the lane label with a
    "create it" button that can only 400 — the failure that module's own comment records. The
    band and its row live in `static/components/recipe-card.js`."""
    root = Path(__file__).resolve().parents[1] / "static" / "components"
    card = (root / "recipe-card.js").read_text(encoding="utf-8")
    assert f'export const RECIPE = "{recipeproposal.KIND}"' in card
    assert "/revise" in card and "apply it" in card
    band = (root / "pending.js").read_text(encoding="utf-8")
    assert "RECIPE, recipeRow" in band and "RECIPE]" in band


async def _resumed(cfg, ts=None, *, reason=""):
    return f"{cfg.slug}:{ts}"


def test_the_operators_edited_lines_replace_the_runs_own(web, monkeypatch):
    """F790, the operator's half of "edit the proposed revision": the card may post HIS wording,
    and that is what the resumed leg is told to implement — the proposal is prose, so rewording
    it is the whole of editing it. The one thing that must not happen is the leg reading his
    words as its own: it would have no way to notice where the two differ, and would defend a
    sentence it never wrote.
    """
    from rsched.engine.revise import REVISE_MARKER

    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", "20260709-170000", "finished")
    rec = _queued(tmp / "routines", "rev", "20260709-170000", ["main.md — tighten step 2"])
    monkeypatch.setattr(c.app.state.runner, "resume_terminal", _resumed)
    r = c.post(f"/api/pending-creations/{rec['id']}/revise",
               json={"lines": ["main.md — delete step 2 entirely", "   "]})
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == ["main.md — delete step 2 entirely"]   # blank lines dropped
    assert r.json()["edited"] is True
    instruction = read_json(run_dir / REVISE_MARKER)["instruction"]
    assert "main.md — delete step 2 entirely" in instruction
    assert "tighten step 2" not in instruction          # his version replaces it, not joins it
    assert "THE LINES BELOW ARE HIS" in instruction     # …and is not passed off as the run's
    assert "writable to you on THIS leg only" in instruction
    msg = next((tmp / "routines" / "rev" / "inbox").glob("msg-*.json"))
    assert "delete step 2 entirely" in read_json(msg)["text"]


def test_edited_lines_are_capped_at_max_lines_server_side(web, monkeypatch):
    """A client-side cap is not a cap. The proposal's own ceiling is `recipeproposal.MAX_LINES`,
    so the operator's rewrite is held to the same one where the lines are read.
    """
    from rsched.engine.revise import REVISE_MARKER

    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", "20260709-171000", "finished")
    rec = _queued(tmp / "routines", "rev", "20260709-171000", ["main.md — x"])
    monkeypatch.setattr(c.app.state.runner, "resume_terminal", _resumed)
    over = [f"main.md — change {i}" for i in range(recipeproposal.MAX_LINES + 3)]
    r = c.post(f"/api/pending-creations/{rec['id']}/revise", json={"lines": over})
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == over[:recipeproposal.MAX_LINES]
    instruction = read_json(run_dir / REVISE_MARKER)["instruction"]
    assert over[recipeproposal.MAX_LINES] not in instruction


def test_an_empty_edit_is_refused_instead_of_applying_the_runs_words(web):
    """An operator who cleared the box did not ask for the run's original wording — answering a
    blank edit with the very lines he deleted would resume the leg on text he rejected.
    """
    c, tmp = web
    mk_run(tmp / "routines" / "rev", "20260709-172000", "finished")
    rec = _queued(tmp / "routines", "rev", "20260709-172000", ["main.md — x"])
    r = c.post(f"/api/pending-creations/{rec['id']}/revise", json={"lines": ["  ", ""]})
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    assert len(pending.load_all(tmp / "routines")) == 1       # still the operator's to decide
