"""A conversation can set the same ceilings a routine can — D216 (c).

The operator ordered the remaining parity gaps *"All four, in the order (c), (b), (a), (d)"*, and
(c) is this: `max_cost` and `ask_timeout_min` reached NEITHER conversation surface, and the
header's save sent three keys of the six, so two more were frozen at create.

`max_cost` is the one that matters most and the reason it is first: a conversation is created with
every ceiling at -1 (`conversations.CONVERSATION_BUDGETS`), so the one run type with a human
driving it, unlimited turns and unlimited tokens was also the only one with NO spend backstop —
while a routine has had a dollar ceiling since F758. `ask_timeout_min` is meaningful here for the
opposite reason: a conversation's medium IS questions, and that field decides how long a blocking
one waits before the reply continues on its stated default.

Nothing in the ENGINE changes: both keys are already in `DEFAULT_BUDGETS`, `BudgetsPatch`
validates against that dict rather than a copied list, and the ceilings are read per reply from
the conversation's own config. This is a surface gap, and these tests pin it closed at both ends.
"""

from __future__ import annotations

from pathlib import Path

from conftest import authed_client, make_test_server
from rsched.config import DEFAULT_BUDGETS
from rsched.web.api_conversation_create import _BUDGET_FIELDS, ComposerForm

_HEAD = Path(__file__).resolve().parents[1] / "static" / "views" / "conversations-head.js"
_NEW = Path(__file__).resolve().parents[1] / "static" / "views" / "conversations-new.js"


def test_the_composer_carries_every_budget_the_loader_keeps():
    """`_BUDGET_FIELDS` is what the composer may set, and the gap was that it listed four of the
    six names `DEFAULT_BUDGETS` carries for a run's ceilings. The two per-RUN structural ones
    (`max_subruns`, `max_subrun_depth`) are deliberately not composer fields — they bound a run
    tree, not a reply — so this asserts the two that were MISSING are present rather than
    asserting equality with a dict that holds more.
    """
    assert "max_cost" in _BUDGET_FIELDS
    assert "ask_timeout_min" in _BUDGET_FIELDS
    for name in _BUDGET_FIELDS:
        assert name in DEFAULT_BUDGETS, f"{name} is not a budget the loader keeps"


def test_the_form_declares_both_new_fields_because_it_forbids_extras():
    """`ComposerForm` is `extra="forbid"`: a field the form does not declare is a 422, not a
    silently dropped value. So the declaration IS the feature.
    """
    assert ComposerForm.model_config.get("extra") == "forbid"
    for name in ("max_cost", "ask_timeout_min"):
        assert name in ComposerForm.model_fields, f"the composer would 422 on {name}"


def test_a_conversation_is_created_with_the_spend_backstop_the_form_sent(tmp_path):
    """End to end through the real route: the composer's `max_cost` reaches the conversation's
    own config, where every reply reads its ceilings from.
    """
    server = make_test_server(tmp_path)
    with authed_client(server) as client:
        r = client.post("/api/conversations",
                        files={"files": ("x.txt", b"", "text/plain")},
                        data={"text": "cap my spend", "max_cost": "25",
                              "ask_timeout_min": "90"})
        assert r.status_code == 200, r.text
        slug = r.json()["slug"]
        detail = client.get(f"/api/conversations/{slug}").json()
    assert detail["budgets"]["max_cost"] == 25
    assert detail["budgets"]["ask_timeout_min"] == 90


def test_both_ceilings_are_patchable_after_create(tmp_path):
    """The header's editor is a PATCH, and `budgets` is already a live-classified field — so what
    was missing was only that the body named these keys. A ceiling settable only at create is the
    shape this gap had: `max_total_turns` was settable and then frozen for the same reason.
    """
    server = make_test_server(tmp_path)
    with authed_client(server) as client:
        slug = client.post("/api/conversations",
                           files={"files": ("x.txt", b"", "text/plain")},
                           data={"text": "start plain"}).json()["slug"]
        r = client.patch(f"/api/conversations/{slug}",
                         json={"budgets": {"max_cost": 10, "ask_timeout_min": 30}})
        assert r.status_code == 200, r.text
        budgets = client.get(f"/api/conversations/{slug}").json()["budgets"]
    assert (budgets["max_cost"], budgets["ask_timeout_min"]) == (10, 30)


def test_the_header_editor_sends_every_ceiling_it_shows():
    """The defect was in the save BODY, not in the route: three keys were sent where the editor
    now shows five, so two inputs would have been read, edited and silently discarded. Pinned on
    the source because the browser test cannot prove what a PATCH body omits.
    """
    source = _HEAD.read_text(encoding="utf-8")
    body = source[source.index("saveBudgets.onclick"):source.index("const budgetField")]
    for key in ("max_turns", "max_wall_clock_min", "max_total_tokens", "max_cost",
                "ask_timeout_min"):
        assert key in body, f"the header's save body does not carry {key}"


def test_the_ask_timeout_is_never_lifted_by_a_blank_field():
    """Every other ceiling reads `+input.value || -1`, where -1 means unlimited and a blank field
    therefore means "no ceiling". For `ask_timeout_min` that reading is wrong in a way that would
    be invisible: an unlimited wait is exactly what a stated default exists to end, so a blank
    field keeps the server's value instead of lifting the bound.
    """
    source = _HEAD.read_text(encoding="utf-8")
    assert "ask_timeout_min: +askIn.value || -1" not in source
    assert "+askIn.value > 0 ? { ask_timeout_min" in source


def test_the_composer_shows_both_fields_with_the_servers_own_defaults():
    """A placeholder read off `/api/conversations/defaults` is how the composer says what happens
    if you leave a field blank. Pinned so a new ceiling cannot be added as an input with no
    default shown — which reads as "no ceiling" to anyone looking at it.
    """
    source = _NEW.read_text(encoding="utf-8")
    assert 'fd.append("max_cost"' in source
    assert 'fd.append("ask_timeout_min"' in source
    assert "costIn.placeholder" in source and "askIn.placeholder" in source
    # and both reach the rendered row, not only the DOM
    assert "$ / reply" in source and "ask timeout (min)" in source
