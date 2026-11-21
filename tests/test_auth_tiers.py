"""The two bearer tiers, on the READ half (R94)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server
from rsched.web.app import (
    ROUTINE_TOKEN_DENIED_READS,
    ROUTINE_TOKEN_MUTATIONS,
    _in_subtree,
    create_app,
)

ROUTINE_TOKEN = "routine-tok"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="tiered")
    server = make_test_server(tmp_path, routine_token=ROUTINE_TOKEN)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        yield c


def _as_routine(client, path: str):
    return client.get(path, headers={"Authorization": f"Bearer {ROUTINE_TOKEN}"})


def _as_operator(client, path: str):
    return client.get(path, headers={"Authorization": f"Bearer {TEST_TOKEN}"})


def test_no_mutation_is_open_to_the_routine_tier():
    """A new endpoint is born sealed; opening one is an explicit allowlist entry with its
    reason. Empty is the whole seal."""
    assert ROUTINE_TOKEN_MUTATIONS == ()


@pytest.mark.parametrize("path", [
    "/api/fs/list?path={dir}",
    "/api/settings/secrets",
    "/api/debug/slow",
    "/api/search?q=token",
])
def test_the_reads_that_defeat_the_sandbox_take_the_operator_token(client, tmp_path, path):
    """`{dir}` is the test's OWN directory, not `/` (F534). The claim here is about the
    TIER — routine denied, operator served — and probing the filesystem root made the
    operator half depend on where the suite runs: `Path("/").iterdir()` raises EACCES
    inside the Landlock jail a util subprocess gets, so `api_fs` answered its own 403 and
    this test failed for the one runner that executes the gate from inside a sandbox. Two
    release entries reported green from the unjailed partition while it was red.
    """
    assert _as_routine(client, path).status_code == 403
    assert _as_operator(client, path.format(dir=tmp_path)).status_code == 200


def test_a_tier_refusal_is_distinguishable_from_an_ordinary_403(client):
    """static/api.js drops the stored token on `insufficient_scope` alone, so a browser
    holding the routine token is never stranded with an unactionable toast."""
    r = _as_routine(client, "/api/search?q=token")
    assert "insufficient_scope" in r.headers.get("www-authenticate", "")
    assert "cross-routine search" in r.json()["detail"]


def test_the_reads_a_run_actually_uses_still_answer(client):
    """The 2026-08-05 rsched-api usage survey: items, questions, the routine cards, the runs
    index, status and stats. Closing a door a run walks through daily is the failure mode on
    the other side of this."""
    for path in ("/api/routines", "/api/status", "/api/items", "/api/questions"):
        assert _as_routine(client, path).status_code == 200, path


def test_a_curator_denied_the_reminder_route_is_told_its_own_one(client):
    """F914: `rules-review` held `capabilities.reminders: global`, met this 403 on
    `DELETE /api/routines/{slug}/reminders/{rid}`, concluded no curator lever existed, and
    left an operator-approved cleanup unapplied — proposing a duplicate route instead.
    """
    r = client.delete("/api/routines/tiered/reminders/rem-20260101-000000-1",
                      headers={"Authorization": f"Bearer {ROUTINE_TOKEN}"})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "remind" in detail
    # it must name the FIELD as the route, not merely mention the word
    assert "side field" in detail
    assert "insufficient_scope" in r.headers.get("www-authenticate", "")


def test_the_operator_still_owns_the_console_delete(client):
    """The fix is prose in a refusal, so the tier itself must not have moved: the reminder
    route stays shut to the routine tier and open to the operator's token."""
    assert ROUTINE_TOKEN_MUTATIONS == ()
    r = client.delete("/api/routines/tiered/reminders/rem-20260101-000000-1",
                      headers={"Authorization": f"Bearer {TEST_TOKEN}"})
    assert r.status_code != 403        # 404 (no such reminder) — reached the handler


def test_the_deny_list_matches_subtrees_not_prefixes():
    """`/api/fs` must not swallow a future `/api/fsomething`, and must catch `/api/fs/list`
    — a bare startswith would silently deny (or open) any sibling route sharing the prefix."""
    assert "/api/fs" in ROUTINE_TOKEN_DENIED_READS
    assert _in_subtree("/api/fs", "/api/fs") and _in_subtree("/api/fs/list", "/api/fs")
    assert not _in_subtree("/api/fsomething", "/api/fs")


def test_a_star_segment_stands_for_exactly_one_segment():
    """`/api/routines/*/secrets` reaches every routine's own secret store and nothing beside
    it: the routine card and its sibling routes stay readable."""
    pattern = "/api/routines/*/secrets"
    assert pattern in ROUTINE_TOKEN_DENIED_READS
    assert _in_subtree("/api/routines/alpha/secrets", pattern)
    assert _in_subtree("/api/routines/alpha/secrets/SFTP_USER", pattern)
    assert not _in_subtree("/api/routines/alpha", pattern)
    assert not _in_subtree("/api/routines/alpha/secretsx", pattern)
    assert not _in_subtree("/api/routines/secrets", pattern)


def test_the_tier_judges_the_path_the_router_dispatches(client):
    """An encoded `?` (or `#`) inside a segment used to end the path the tier judged:
    `request.url.path` is RE-PARSED from the decoded path, so `/api/runs/events%3Fx/transcript`
    routed to the transcript route while auth read `/api/runs/events` — then a stream path,
    which a URL-carriable ticket admitted. Auth passed (the handler's own 400 proved it). A
    ticket now admits no HTTP route at all, and only the socket it was minted for."""
    from starlette.websockets import WebSocketDisconnect

    from rsched.web.streams import CLOSE_GONE

    def mint() -> str:
        return client.post("/api/stream-ticket",
                           headers={"Authorization": f"Bearer {TEST_TOKEN}"}).json()["ticket"]

    for encoded in ("%3F", "%23"):
        r = client.get(f"/api/runs/events{encoded}x/transcript?ticket={mint()}")
        assert r.status_code == 401, (encoded, r.status_code, r.text)
    # the stream the ticket exists for still opens on it (CLOSE_GONE: admitted, no such run)
    with client.websocket_connect(f"/api/runs/ghost:00000000-000000/events?ticket={mint()}"
                                  ) as ws, pytest.raises(WebSocketDisconnect) as gone:
        ws.receive_json()
    assert gone.value.code == CLOSE_GONE


def test_two_mints_purging_one_expired_ticket_do_not_collide(client):
    """A mint runs on a worker thread, and every tab reconnecting after a restart mints at
    once: two purges saw the same expired ticket and the slower `del` raised a 500. The
    dict below loses its expired entries the moment it is listed — the other mint winning
    the purge between this one's listing and its delete."""
    import time

    class RacingStore(dict):
        def items(self):
            listed = list(super().items())
            for key, expiry in listed:
                if expiry < time.monotonic():
                    super().pop(key, None)
            return listed

    client.app.state.stream_tickets = RacingStore({"expired": 0.0})
    r = client.post("/api/stream-ticket", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
    assert r.status_code == 200
    assert "expired" not in client.app.state.stream_tickets
