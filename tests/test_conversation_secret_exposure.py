"""A CONVERSATION's SECRET EXPOSURE — the three-state dial over the SHARED store (D216 part a).

A ROUTINE's settings page has carried a three-state control per central-store secret for the
life of D103: `static/views/routine-config-access.js` renders, for every name in the store,
"ask on first use" (no row), "expose" (`secret:<NAME>: true`) and "withhold"
(`secret:<NAME>: false`), written into routine.yaml's `grants:` mapping through the settings
DRAFT the operator accepts. A conversation had NONE of it: `api_conversation_grants.py` could
only CLEAR a row that already existed (F783's tombstones, D216 rank 1's held grants), so the
only way a thread ever got a secret was the engine asking at first use — and the only way one
was withheld in advance was a hand edit of routine.yaml. `grants` is absent from
`CONVERSATION_PATCH_FIELDS` and `ConversationPatch` forbids extras, so even an API caller was
refused.

The surface under test is therefore a PRE-EXPOSURE write beside the two clears, in the same
router, and its two shape choices are load-bearing and pinned here:

* ONE named row per write, never a wholesale `grants` replace — the conversation's capabilities
  panel has no draft and no accept (every card writes on click), so a replace would silently
  drop a row that arrived in between, and the rows in that same mapping include the deny
  tombstones the engine honours;
* CLASS-LOCKED to `secret:` — the key is built server-side from a validated store NAME, so this
  route cannot reach a row of any other class. That is what keeps the two clears' sign lock
  intact: a tombstone of another class is still only clearable by its own press. Within the
  `secret:` class the dial deliberately reaches all three states, exactly as the routine page's
  does — that IS the control this part adds.

And it is operator-only (`app.ROUTINE_TOKEN_MUTATIONS` is empty): a run that was refused a
secret must not be able to expose it to itself.
"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, authed_client, make_test_server
from rsched.web.app import create_app

EXPOSE = "/api/conversations/{slug}/secret-exposure"


def _server(tmp_path, **overrides):
    """A tmp conversations home — `make_test_server` does not override it, so without this the
    test writes into the real one (tests/test_conversation_grants.py carries the same note)."""
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            **overrides)


def _conversation(server, slug: str = "c-20261105-120000", grants: dict | None = None):
    conv = server.conversations_home / slug
    conv.mkdir(parents=True, exist_ok=True)
    cfg: dict = {"slug": slug, "name": "a conversation"}
    if grants is not None:
        cfg["grants"] = grants
    (conv / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (conv / "main.md").write_text("# converse\n", encoding="utf-8")
    return conv


def _grants(conv) -> dict:
    raw = yaml.safe_load((conv / "routine.yaml").read_text(encoding="utf-8")) or {}
    return raw.get("grants") or {}


@pytest.fixture
def conv(tmp_path):
    """A conversation carrying one row of every sign already — what a write must not disturb."""
    server = _server(tmp_path)
    c_dir = _conversation(server, grants={"util:signal": False, "secret:ZULIP_API_KEY": True})
    with authed_client(server) as c:
        yield c, c_dir, c_dir.name


def test_a_secret_can_be_pre_exposed_before_any_run_asks(conv):
    """The gap itself: nothing had asked for NTFY_URL and no row existed, and the operator
    can now hand it over in advance — the engine reads the true row and the util call receives
    the value (`engine/secretgate.secret_state`)."""
    c, c_dir, slug = conv
    out = c.put(EXPOSE.format(slug=slug), json={"name": "NTFY_URL", "expose": True})
    assert out.status_code == 200, out.text
    assert out.json()["exposure"] == {"NTFY_URL": True, "ZULIP_API_KEY": True}
    assert _grants(c_dir)["secret:NTFY_URL"] is True


def test_the_three_states_are_all_reachable_and_undecided_removes_the_row(conv):
    """expose / withhold / ask on first use — the routine dial's three states, and the third is
    the ABSENCE of a row, so it has to delete rather than write `null`."""
    c, c_dir, slug = conv
    c.put(EXPOSE.format(slug=slug), json={"name": "NTFY_URL", "expose": False})
    assert _grants(c_dir)["secret:NTFY_URL"] is False
    # withheld → exposed, in one press, as the routine page's select does
    c.put(EXPOSE.format(slug=slug), json={"name": "NTFY_URL", "expose": True})
    assert _grants(c_dir)["secret:NTFY_URL"] is True
    back = c.put(EXPOSE.format(slug=slug), json={"name": "NTFY_URL", "expose": None})
    assert back.status_code == 200, back.text
    assert "secret:NTFY_URL" not in _grants(c_dir)
    assert back.json()["exposure"] == {"ZULIP_API_KEY": True}


def test_one_named_row_is_written_and_every_other_row_survives(conv):
    """Never a wholesale replace: this panel writes on click and cannot re-read the file first,
    so a save that rewrote the mapping would drop whatever row landed in between — including
    the deny tombstones the engine honours."""
    c, c_dir, slug = conv
    assert c.put(EXPOSE.format(slug=slug),
                 json={"name": "GMAIL_ADDRESS", "expose": True}).status_code == 200
    assert _grants(c_dir) == {"util:signal": False, "secret:ZULIP_API_KEY": True,
                              "secret:GMAIL_ADDRESS": True}
    assert c.put(EXPOSE.format(slug=slug),
                 json={"name": "ZULIP_API_KEY", "expose": None}).status_code == 200
    assert _grants(c_dir) == {"util:signal": False, "secret:GMAIL_ADDRESS": True}


def test_the_row_it_writes_is_the_row_a_decisions_allow_forever_writes(tmp_path):
    """Pre-exposure and an `allow_forever` click must leave the SAME row, or the engine would
    honour one and not the other (`web/grants_apply.apply_forever` is the Decisions path)."""
    from rsched.web import grants_apply

    server = _server(tmp_path)
    by_click = _conversation(server, slug="c-20261105-130000")
    grants_apply.apply_forever(server, by_click, ["secret:NTFY_URL"], "allow_forever")

    by_dial = _conversation(server, slug="c-20261105-140000")
    with authed_client(server) as c:
        out = c.put(EXPOSE.format(slug=by_dial.name), json={"name": "NTFY_URL", "expose": True})
        assert out.status_code == 200, out.text
    assert _grants(by_dial) == _grants(by_click) == {"secret:NTFY_URL": True}


def test_no_class_but_secret_is_reachable_through_this_route(conv):
    """The key is built server-side from a validated store NAME, so this route cannot write or
    clear a row of any other class. That is what leaves the two clears' sign lock intact: a
    `util:` tombstone is still only clearable by its own press, and a true row of another class
    (which `entities.normalize_grants` rejects outright) can never be created here."""
    c, c_dir, slug = conv
    for bad in ("util:signal", "secret:NTFY_URL", "fs-read:~/x", "NOT A NAME", "", "9LIVES"):
        out = c.put(EXPOSE.format(slug=slug), json={"name": bad, "expose": True})
        assert out.status_code == 400, (bad, out.status_code, out.text)
    assert _grants(c_dir) == {"util:signal": False, "secret:ZULIP_API_KEY": True}


def test_a_stray_key_in_the_body_is_refused(conv):
    """Every config body on this router forbids extras: a silently-dropped stray reads as
    "written" while the mapping is unchanged."""
    c, c_dir, slug = conv
    out = c.put(EXPOSE.format(slug=slug),
                json={"name": "NTFY_URL", "expose": True, "entity": "secret:NTFY_URL"})
    assert out.status_code == 422, out.text
    assert "secret:NTFY_URL" not in _grants(c_dir)


def test_the_reader_answers_with_the_rows_this_conversation_carries(conv):
    """The dial needs the rows; the store's NAMES come from `/api/settings/secrets`, which is
    denied to the routine token (`app.ROUTINE_TOKEN_DENIED_READS`) and must not be mirrored
    here. So the reader answers with this conversation's own `secret:` rows alone — a tombstone
    of another class is the declined card's business."""
    c, _c_dir, slug = conv
    body = c.get(EXPOSE.format(slug=slug)).json()
    assert body["slug"] == slug
    assert body["exposure"] == {"ZULIP_API_KEY": True}


def test_the_exposure_write_is_refused_to_the_routine_token(tmp_path):
    """The one thing this must never allow: a run granting itself a secret. Every write on this
    router is operator-only, because `app.ROUTINE_TOKEN_MUTATIONS` is empty — pinned here
    because an exposure write is precisely what a refused run would reach for."""
    server = _server(tmp_path, routine_token="routine-tok")
    c_dir = _conversation(server)
    with TestClient(create_app(server)) as c:
        denied = c.put(EXPOSE.format(slug=c_dir.name), json={"name": "NTFY_URL", "expose": True},
                       headers={"Authorization": "Bearer routine-tok"})
        assert denied.status_code == 403
        assert _grants(c_dir) == {}
        ok = c.put(EXPOSE.format(slug=c_dir.name), json={"name": "NTFY_URL", "expose": True},
                   headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert ok.status_code == 200, ok.text
    assert _grants(c_dir) == {"secret:NTFY_URL": True}


def test_an_unknown_slug_or_a_routine_is_unreachable_and_creates_nothing(conv, make_routine):
    """The two homes stay separate (this router resolves against conversations_home alone) and
    a typo must not materialize a config file holding grants for a thread that does not exist."""
    c, _c_dir, _slug = conv
    make_routine("expr")
    assert c.get(EXPOSE.format(slug="expr")).status_code == 404
    assert c.put(EXPOSE.format(slug="expr"),
                 json={"name": "NTFY_URL", "expose": True}).status_code == 404
    assert c.get(EXPOSE.format(slug="c-nope")).status_code == 404
    assert c.put(EXPOSE.format(slug="c-nope"),
                 json={"name": "NTFY_URL", "expose": True}).status_code == 404
