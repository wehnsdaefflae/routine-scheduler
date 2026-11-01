"""A conversation's DECLINED access — listed and cleared from its own surface (F783).

A Decisions-page `deny_forever` writes a `{entity: false}` tombstone into the answering
subject's own routine.yaml (`web/grants_apply.apply_forever` → `config.routine.record_grants`),
and the engine then refuses that entity and stops asking for it. That is the same mechanism for
a routine and for a conversation — but only the ROUTINE page ever had the editor that REMOVES
such a row (`static/views/routine-config-access.js`, "Declined access"), and `grants` was absent
from `CONVERSATION_PATCH_FIELDS`, so even an API caller was refused. One mis-click denied a
capability to that conversation for the life of the thread.

The surface under test is therefore a parallel ROUTER over the same `grants:` mapping, which
clears ONE entity rather than replacing the map. Both choices are load-bearing and pinned here:

* one entity, not a wholesale replace — the routine page can replace the map safely because it
  is a DRAFT form the operator accepts, while a conversation's capabilities panel writes on
  click, so a replace would drop a row written between the page's load and the press; the rows
  a Decisions click writes into that same mapping include `secret:` ALLOW-forever grants;
* clearing only ever LOOSENS a denial (back to undecided/requestable) and can grant nothing,
  which is why this route needs no capability cascade — and the write is still operator-only.
"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, authed_client, make_test_server
from rsched.web.app import create_app


def _server(tmp_path, **overrides):
    """make_test_server does NOT override `conversations_home` — it resolves to the REAL one
    (`/home/mark/conversations` on the deployment), so a test that creates a conversation
    there either writes into the live home or dies on Errno 13 depending on the sandbox it
    runs in. Always name a tmp home, as `conftest.seeded_server` does.
    """
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            **overrides)


def _conversation(server, slug: str = "c-20261030-120000", grants: dict | None = None):
    """The minimum on-disk shape `registry.info` reads as a conversation, with `grants:` rows
    exactly as a forever-decision leaves them."""
    conv = server.conversations_home / slug
    conv.mkdir(parents=True, exist_ok=True)
    cfg: dict = {"slug": slug, "name": "a conversation"}
    if grants is not None:
        cfg["grants"] = grants
    (conv / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (conv / "main.md").write_text("# converse\n", encoding="utf-8")
    return conv


def _grants(conv) -> dict:
    """What the conversation's routine.yaml says NOW — where a clear has to show up."""
    raw = yaml.safe_load((conv / "routine.yaml").read_text(encoding="utf-8")) or {}
    return raw.get("grants") or {}


@pytest.fixture
def conv(tmp_path):
    server = _server(tmp_path)
    c_dir = _conversation(server, grants={"util:signal": False, "action:shell": False,
                                          "secret:NTFY_URL": True})
    with authed_client(server) as c:
        yield c, c_dir, c_dir.name


def test_the_declined_rows_are_listed_and_the_allow_rows_are_not(conv):
    """The tombstones are the FALSE rows. A true row is an allow-forever grant (`secret:` is
    the one class with no native switch) and is not this surface's business."""
    c, _c_dir, slug = conv
    body = c.get(f"/api/conversations/{slug}/declined").json()
    assert body["slug"] == slug
    assert body["declined"] == ["action:shell", "util:signal"]


def test_clearing_a_row_returns_it_to_undecided_and_keeps_every_other_grant_row(conv):
    c, c_dir, slug = conv
    out = c.post(f"/api/conversations/{slug}/declined/clear", json={"entity": "util:signal"})
    assert out.status_code == 200, out.text
    assert out.json()["declined"] == ["action:shell"]
    # The other rows survive, which is why this is a clear-one and not a replace: the
    # allow-forever row a Decisions click writes into the same mapping would otherwise be
    # dropped by any save from a page that cannot re-read the file first.
    assert _grants(c_dir) == {"action:shell": False, "secret:NTFY_URL": True}


def test_the_tombstone_a_deny_forever_click_leaves_is_what_this_clears(tmp_path):
    """The finding's own path, end to end: `grants_apply.apply_forever` is what the Decisions
    page runs for a deny_forever, and its row is the row this route removes."""
    from rsched.web import grants_apply

    server = _server(tmp_path)
    c_dir = _conversation(server)
    grants_apply.apply_forever(server, c_dir, ["util:discord"], "deny_forever")
    assert _grants(c_dir) == {"util:discord": False}
    with authed_client(server) as c:
        listed = c.get(f"/api/conversations/{c_dir.name}/declined").json()
        assert listed["declined"] == ["util:discord"]
        cleared = c.post(f"/api/conversations/{c_dir.name}/declined/clear",
                         json={"entity": "util:discord"})
        assert cleared.status_code == 200, cleared.text
    assert _grants(c_dir) == {}


def test_a_row_the_conversation_never_declined_is_404(conv):
    c, c_dir, slug = conv
    missing = c.post(f"/api/conversations/{slug}/declined/clear", json={"entity": "util:mail"})
    assert missing.status_code == 404
    # An ALLOW row is not a declined row either: this surface only ever loosens a denial, so it
    # must not become a way to delete a grant.
    assert c.post(f"/api/conversations/{slug}/declined/clear",
                  json={"entity": "secret:NTFY_URL"}).status_code == 404
    assert _grants(c_dir) == {"util:signal": False, "action:shell": False,
                              "secret:NTFY_URL": True}


def test_a_withheld_secret_is_listed_and_clearable_here_unlike_on_the_routine_page(tmp_path):
    """A `secret:NAME: FALSE` row is a denial too — `engine/secretgate.secret_state` reads it as
    denied and the util call is refused — and this surface is the only one that can clear it for
    a conversation.

    The routine page leaves secret rows out of its *Declined access* block
    (`static/components/declined-access.js: declinedIds`) because it ALSO carries a *Secret
    exposure* dial that can put a withheld name back to "ask on first use". A conversation has
    no such dial (its own-secrets card is the private `secrets.d` store, a different thing), so
    filtering them here would reproduce this very finding for the secret class.
    """
    server = _server(tmp_path)
    c_dir = _conversation(server, grants={"secret:NTFY_URL": False, "util:signal": False})
    with authed_client(server) as c:
        slug = c_dir.name
        assert c.get(f"/api/conversations/{slug}/declined").json()["declined"] == [
            "secret:NTFY_URL", "util:signal"]
        out = c.post(f"/api/conversations/{slug}/declined/clear",
                     json={"entity": "secret:NTFY_URL"})
        assert out.status_code == 200, out.text
    assert _grants(c_dir) == {"util:signal": False}


def test_an_unknown_conversation_is_404_and_creates_nothing(tmp_path):
    """A typo'd slug must 404 rather than materialize a config file for a conversation that
    does not exist — the same refusal the conversation secrets router makes."""
    server = _server(tmp_path)
    _conversation(server)
    with authed_client(server) as c:
        assert c.get("/api/conversations/c-nope/declined").status_code == 404
        assert c.post("/api/conversations/c-nope/declined/clear",
                      json={"entity": "util:signal"}).status_code == 404
    assert not (server.conversations_home / "c-nope").exists()


def test_a_routine_slug_is_not_reachable_through_the_conversation_route(conv, make_routine):
    """The two surfaces stay separate: this router resolves against conversations_home only, so
    it cannot be used to clear another ROUTINE's tombstone."""
    c, _c_dir, _slug = conv
    make_routine("grantr")
    assert c.get("/api/conversations/grantr/declined").status_code == 404
    assert c.post("/api/conversations/grantr/declined/clear",
                  json={"entity": "util:signal"}).status_code == 404


def test_the_clear_is_refused_to_the_routine_token(tmp_path):
    """A run must not be able to clear the denial the operator set ON it — that would make
    every tombstone advisory. `app.ROUTINE_TOKEN_MUTATIONS` is empty, so no write is reachable
    with a run's token; pinned here because this route is the one a denied run would reach for.
    """
    server = _server(tmp_path, routine_token="routine-tok")
    c_dir = _conversation(server, grants={"util:signal": False})
    with TestClient(create_app(server)) as c:
        denied = c.post(f"/api/conversations/{c_dir.name}/declined/clear",
                        json={"entity": "util:signal"},
                        headers={"Authorization": "Bearer routine-tok"})
        assert denied.status_code == 403
        ok = c.post(f"/api/conversations/{c_dir.name}/declined/clear",
                    json={"entity": "util:signal"},
                    headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert ok.status_code == 200, ok.text
    assert _grants(c_dir) == {}


# ---------------------------------------------------------------------------------------------
# The ALLOW half of the same mapping — D216 rank 1.
#
# F783 (above) gave the operator the DENIALS. A `grants:` TRUE row — what a Decisions
# `allow_forever` writes for the one class with no native switch, `secret:`
# (entities.TRUE_ROW_CLASSES, web/grants_apply.apply_forever) — had no surface at all: the
# engine honours it for a conversation exactly as for a routine (engine/secretgate.secret_state
# reads the row and the util call receives the value), while the console could neither show what
# a thread had been granted nor take it back short of a hand edit of routine.yaml. The routine
# page has both halves (its *Secret exposure* dial returns a name to "ask on first use").
#
# The two clears are SIGN-LOCKED (`_stored_key(..., value=...)`): each refuses a row of the
# other sign, so the grant route can never lift a denial the operator set, and the declined
# route can never withdraw a grant. Both pinned here.
# ---------------------------------------------------------------------------------------------


def test_the_held_rows_are_listed_and_the_declined_rows_are_not(conv):
    """What the conversation HOLDS is the TRUE rows. A false row is a tombstone and belongs to
    the declined half, which has its own reader and its own press."""
    c, _c_dir, slug = conv
    body = c.get(f"/api/conversations/{slug}/grants").json()
    assert body["slug"] == slug
    assert body["held"] == ["secret:NTFY_URL"]


def test_withdrawing_a_held_grant_returns_it_to_undecided_and_keeps_every_other_row(conv):
    c, c_dir, slug = conv
    out = c.post(f"/api/conversations/{slug}/grants/clear", json={"entity": "secret:NTFY_URL"})
    assert out.status_code == 200, out.text
    assert out.json()["held"] == []
    assert out.json()["cleared"] == "secret:NTFY_URL"
    # One named row, never a wholesale replace: the tombstones in the same mapping survive, so a
    # withdrawal from this click-to-write panel cannot drop a deny row that landed in between.
    assert _grants(c_dir) == {"util:signal": False, "action:shell": False}


def test_the_row_an_allow_forever_click_leaves_is_what_this_withdraws(tmp_path):
    """The finding's own path, end to end: `grants_apply.apply_forever` is what the Decisions
    page runs for an allow_forever, and its true row is the row this route removes."""
    from rsched.web import grants_apply

    server = _server(tmp_path)
    c_dir = _conversation(server)
    grants_apply.apply_forever(server, c_dir, ["secret:NTFY_URL"], "allow_forever")
    assert _grants(c_dir) == {"secret:NTFY_URL": True}
    with authed_client(server) as c:
        listed = c.get(f"/api/conversations/{c_dir.name}/grants").json()
        assert listed["held"] == ["secret:NTFY_URL"]
        cleared = c.post(f"/api/conversations/{c_dir.name}/grants/clear",
                         json={"entity": "secret:NTFY_URL"})
        assert cleared.status_code == 200, cleared.text
    assert _grants(c_dir) == {}


def test_neither_clear_can_do_the_other_s_job(conv):
    """The sign lock. A denial is not a held grant and a held grant is not a denial, so each
    route 404s the other's rows — otherwise this surface would become a way to lift a denial
    the operator set, which is exactly what the declined route's own press is for."""
    c, c_dir, slug = conv
    assert c.post(f"/api/conversations/{slug}/grants/clear",
                  json={"entity": "util:signal"}).status_code == 404
    assert c.post(f"/api/conversations/{slug}/declined/clear",
                  json={"entity": "secret:NTFY_URL"}).status_code == 404
    assert c.post(f"/api/conversations/{slug}/grants/clear",
                  json={"entity": "secret:NOPE"}).status_code == 404
    assert _grants(c_dir) == {"util:signal": False, "action:shell": False,
                              "secret:NTFY_URL": True}


def test_a_held_row_is_matched_in_the_spelling_the_file_carries(tmp_path):
    """A row the operator can SEE has to be a row the operator can remove: the canonical form is
    tried as well, so a request naming `fs-read:~/x` reaches the expanded row a Decisions click
    wrote — the same matching the declined half does."""
    from pathlib import Path

    server = _server(tmp_path)
    expanded = str(Path.home() / "data-x")
    c_dir = _conversation(server, grants={f"fs-read:{expanded}": True})
    with authed_client(server) as c:
        slug = c_dir.name
        assert c.get(f"/api/conversations/{slug}/grants").json()["held"] == [
            f"fs-read:{expanded}"]
        out = c.post(f"/api/conversations/{slug}/grants/clear",
                     json={"entity": "fs-read:~/data-x"})
        assert out.status_code == 200, out.text
    assert _grants(c_dir) == {}


def test_the_grants_half_is_unreachable_for_an_unknown_slug_or_a_routine(conv, make_routine):
    """The two homes stay separate and a typo materializes nothing — the same refusals the
    declined half makes, because it is the same router."""
    c, _c_dir, _slug = conv
    make_routine("grantr2")
    assert c.get("/api/conversations/grantr2/grants").status_code == 404
    assert c.post("/api/conversations/grantr2/grants/clear",
                  json={"entity": "secret:NTFY_URL"}).status_code == 404
    assert c.get("/api/conversations/c-nope/grants").status_code == 404


def test_the_withdrawal_is_refused_to_the_routine_token(tmp_path):
    """A run must not be able to withdraw its own grant either — every write on this router is
    operator-only (`app.ROUTINE_TOKEN_MUTATIONS` is empty)."""
    server = _server(tmp_path, routine_token="routine-tok")
    c_dir = _conversation(server, grants={"secret:NTFY_URL": True})
    with TestClient(create_app(server)) as c:
        denied = c.post(f"/api/conversations/{c_dir.name}/grants/clear",
                        json={"entity": "secret:NTFY_URL"},
                        headers={"Authorization": "Bearer routine-tok"})
        assert denied.status_code == 403
        ok = c.post(f"/api/conversations/{c_dir.name}/grants/clear",
                    json={"entity": "secret:NTFY_URL"},
                    headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert ok.status_code == 200, ok.text
    assert _grants(c_dir) == {}
