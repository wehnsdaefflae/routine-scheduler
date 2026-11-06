"""A conversation can be supervised the way a routine can — D216 (d), the last part.

The operator ordered the remaining parity gaps *"All four, in the order (c), (b), (a), (d)"*, and
(d) is this: the escalation LADDER and the `supervisor` model role reached neither conversation
surface.

Nothing in the ENGINE changes, and that is what makes this a surface gap rather than a feature.
`engine/ladder.ladder_settings` reads `ctx.routine.ladder` with no conversation gate; a
conversation IS a `RoutineConfig`, so the field exists and the engine honours it. What was
missing: `ladder` and its two turn knobs were absent from `CONVERSATION_PATCH_FIELDS` and from
`ConversationPatch` (which is `extra="forbid"`, so naming one was a 422 — proven red-first), the
conversation GET served neither, and the header's model save built a body of
main/tool_call/uncensored/decision — dropping a stored `supervisor` on every save it made.

That last one is the defect worth stating plainly: a `supervisor` role put into a conversation's
`routine.yaml` by hand was ERASED the next time anyone saved its models from the header, because
`validate_models` replaces the block wholesale and the body is built from scratch. D216 (b) was a
no-op in the engine and shipped as the pin that proved it; this part is not — two of its three
halves move real code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import authed_client, make_test_server
from rsched import configflow
from rsched.config import ROUTINE_MODEL_ROLES
from rsched.config.base import DEFAULT_LADDER
from rsched.web.api_conversation_config import ConversationPatch

_HEAD = Path(__file__).resolve().parents[1] / "static" / "views" / "conversations-head.js"


def _conversation(tmp_path, slug: str = "c-20261106-120000"):
    """A conversation as the registry reads one, written to disk.

    NOT `POST /api/conversations`: that route spawns the first reply as a real engine
    subprocess, which `tests/production_guard.py` refuses (a fresh interpreter inherits none of
    the test's redirections and would load the PRODUCTION config). `conversations_home` is named
    explicitly for the same reason `conftest.seeded_server` names it — `make_test_server` leaves
    it pointing at the live `/home/mark/conversations`.
    """
    server = make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"))
    conv = server.conversations_home / slug
    conv.mkdir(parents=True, exist_ok=True)
    (conv / "routine.yaml").write_text(
        yaml.safe_dump({"slug": slug, "name": "a conversation"}), encoding="utf-8")
    (conv / "main.md").write_text("# converse\n", encoding="utf-8")
    return server, conv


@pytest.fixture
def conv(tmp_path):
    server, conv_dir = _conversation(tmp_path)
    with authed_client(server) as client:
        yield client, conv_dir


def _stored(conv_dir) -> dict:
    """What the conversation's routine.yaml says NOW — where a config save has to show up."""
    return yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8")) or {}


def test_the_conversation_vocabulary_names_the_ladder_and_its_two_knobs():
    """`configflow.CONVERSATION_PATCH_FIELDS` is what a run's `config_patch` may name for a
    conversation, and `tests/test_configflow.py` pins it to `ConversationPatch` one-to-one — so
    a field missing here is refused at FILING time, before any route sees it.
    """
    for key in ("ladder", "ladder_turn_interval", "oversight_turns"):
        assert key in configflow.CONVERSATION_PATCH_FIELDS, f"a patch naming {key} is refused"
        assert key in ConversationPatch.model_fields, f"the PATCH would 422 on {key}"


def test_the_conversation_detail_serves_the_ladder_the_way_the_routine_page_does(conv):
    """The routine GET serves `ladder`, `ladder_turn_interval`, `oversight_turns` and the
    DERIVED value a blank field falls back to. A control cannot render a knob the payload does
    not carry, so the GET is half the gap.
    """
    client, conv_dir = conv
    detail = client.get(f"/api/conversations/{conv_dir.name}").json()
    assert detail["ladder"] == {**DEFAULT_LADDER}
    assert detail["ladder_turn_interval"] >= 1
    assert "oversight_turns" in detail           # null means DERIVED, not unset
    assert detail["oversight_turns_derived"] >= 1
    assert "max_subrun_depth" in detail          # the ceiling the switch warns against


def test_the_ladder_is_patchable_and_lands_where_the_engine_reads_it(conv):
    """End to end: `ladder` is CONFIG (routine.yaml, the user's authority surface) and the two
    turn knobs are TUNING (tuning.yaml, recipe-classed) — the same split the routine PATCH
    writes, so "supervise this conversation" means the same thing in both homes.
    """
    client, conv_dir = conv
    r = client.patch(f"/api/conversations/{conv_dir.name}",
                     json={"ladder": {"enabled": True, "rung_height": 2},
                           "ladder_turn_interval": 12})
    assert r.status_code == 200, r.text
    assert _stored(conv_dir)["ladder"] == {"enabled": True, "rung_height": 2}
    tuning = yaml.safe_load((conv_dir / "tuning.yaml").read_text(encoding="utf-8")) or {}
    assert tuning["ladder_turn_interval"] == 12
    detail = client.get(f"/api/conversations/{conv_dir.name}").json()
    assert detail["ladder"]["enabled"] is True
    assert detail["ladder_turn_interval"] == 12


def test_a_bad_rung_height_is_refused_rather_than_saved_as_a_disabled_ladder(conv):
    """`patch_merge.apply_ladder` refuses every bad value instead of letting the loader degrade
    it toward OFF — a user told "saved" who got a silently disabled ladder is the failure it
    exists to prevent. The conversation PATCH routes through that same helper, not a copy, and
    the turn knobs through the same `MIN_OVERSIGHT_TURNS` floor the routine PATCH applies.
    """
    client, conv_dir = conv
    r = client.patch(f"/api/conversations/{conv_dir.name}", json={"ladder": {"rung_height": 0}})
    assert r.status_code == 400, r.text
    assert "rung_height" in r.text
    assert "ladder" not in _stored(conv_dir), "a refused patch wrote anyway"
    bad = client.patch(f"/api/conversations/{conv_dir.name}", json={"ladder_turn_interval": 1})
    assert bad.status_code == 400, bad.text


def test_the_supervisor_role_is_a_conversation_role_too(conv):
    """`config.base.MODEL_KINDS` has carried `supervisor` since D164 and the conversation GET
    serves every `ROUTINE_MODEL_ROLES` key — so the role was already half-present. What it
    lacked was a surface that keeps it.
    """
    assert "supervisor" in ROUTINE_MODEL_ROLES
    client, conv_dir = conv
    assert "supervisor" in client.get(f"/api/conversations/{conv_dir.name}").json()["models"]


def test_the_header_save_keeps_a_stored_supervisor_instead_of_erasing_it():
    """THE REAL DEFECT, pinned on the source because a browser cannot prove what a PATCH body
    OMITS: the header builds `models` from scratch (main / tool_call / uncensored / decision),
    so any other save erased a stored `supervisor`. `validate_models` replaces the block
    wholesale, so an omitted role is a deletion — not a no-op.
    """
    source = _HEAD.read_text(encoding="utf-8")
    body = source[source.index("const models = {}"):source.index("body: { models, efforts }")]
    assert "supervisor" in body, "the header's model save drops a stored supervisor role"


def test_the_header_mounts_the_ladder_controls_the_routine_page_uses():
    """One component, two homes: `ladderSwitch` + `ladderIntervals` already exist and already
    speak the server's vocabulary (its `MIN_TURNS` mirrors `engine/ladder.MIN_OVERSIGHT_TURNS`,
    its derived-turns hint mirrors `oversight_turns_for`). A second copy of those controls would
    drift from those floors, which is exactly what `ladder-settings.js` was extracted to stop.

    So this pins the CHAIN, not a name: the header imports the panel from that module, and the
    panel composes the two existing controls rather than reimplementing them. The routine page
    mounts them separately because its two authority classes save in different groups; a
    conversation has one panel and no accept bar.
    """
    source = _HEAD.read_text(encoding="utf-8")
    assert "ladder-settings.js" in source, "the header does not import the ladder component"
    assert "ladderPanel" in source, "the header does not mount the ladder panel"
    panel = (_HEAD.parent.parent / "components" / "ladder-settings.js").read_text(
        encoding="utf-8")
    body = panel[panel.index("export function ladderPanel"):]
    assert "ladderSwitch(" in body and "ladderIntervals(" in body, \
        "ladderPanel reimplements the controls instead of composing them"
