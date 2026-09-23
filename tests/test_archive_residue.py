"""Archiving a routine names the published state it CANNOT reach (R1658, bina 2026-09-21).

`POST /routines/{slug}/archive` tidies exactly what it owns: the directory moves to `.archive/`
and the routine's scoped secrets are dropped (D103 — "the routine's OWN secrets die with it").
Everything the routine published OUTSIDE this machine it has always been silent about, and the
silence is the defect: the one moment any code knows a routine is gone is the one moment its
residue could be named, and that moment was being spent saying nothing.

Three slugs proved it in two weeks — `birthday-admin-admin`, a duplicate "Fourty-Four", and
`bina`, whose steward card outlived it by a day. The hub derives its cards from published store
directories and has no retire operation, so nothing there expires on its own; and the routine
could not clean up after itself even in principle, because archiving drops the very credentials
publishing needs, first.

The scheduler deliberately does NOT reach out and delete anything: it holds no credentials for
that host, and an outbound publish on an archive request would make the daemon a deploy
dependency. It REPORTS. What it reports is derived from config the routine already carries, so
no routine has to declare anything new to be covered.
"""

from __future__ import annotations

import yaml

from rsched.web import api_routine_edit as edit

STEWARD_KIT = "/home/mark/.local/share/routine-scheduler-libraries/web/steward"


def _publishes_to(d, root: str) -> None:
    """Give a routine the read root that marks it as a steward-hub publisher."""
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["fs_read_roots"] = [root]
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


# ---- the inventory ---------------------------------------------------------------------


def test_a_steward_publisher_is_recognised_from_the_config_it_already_carries(make_routine):
    d = make_routine(slug="pubr")
    _publishes_to(d, STEWARD_KIT)
    from rsched.config import load_routine

    cfg, _ = load_routine(d)
    residue = edit.external_residue(cfg)
    assert residue, "a routine holding the steward kit root publishes to the hub"
    assert any("pubr" in item["locator"] for item in residue), \
        "the residue must name WHERE it is, not merely that it exists"
    assert all(item["owner"] for item in residue), \
        "residue nobody owns is residue nobody removes"


def test_the_named_owner_is_someone_who_can_actually_remove_it():
    """An owner who cannot perform the removal is the silence this inventory exists to end.
    The steward store has no delete operation in the kit's api.php and no routine has a way
    onto that host to install one, so the row names the OPERATOR, never a routine — a slug
    here reads as "someone else will handle it" and nothing ever does."""
    for surface in edit.EXTERNAL_SURFACES:
        assert "operator" in surface["owner"], surface["surface"]


def test_a_routine_that_publishes_nothing_external_reports_no_residue(make_routine):
    d = make_routine(slug="quietr")
    from rsched.config import load_routine

    cfg, _ = load_routine(d)
    assert edit.external_residue(cfg) == [], \
        "an ordinary routine must not be given phantom cleanup work"


# ---- the endpoint ----------------------------------------------------------------------


def test_archiving_hands_back_the_residue_it_cannot_reach(api_client, make_routine):
    c, tmp = api_client
    d = make_routine(slug="hubr")
    _publishes_to(d, STEWARD_KIT)

    r = c.post("/api/routines/hubr/archive")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert (tmp / "routines" / ".archive").exists(), "the directory still moves"
    assert body["external_residue"], \
        "archiving a publisher must NAME what it left behind on another host"
    assert any("hubr" in item["locator"] for item in body["external_residue"])


def test_archiving_an_ordinary_routine_says_so_explicitly(api_client, make_routine):
    """An empty list, always present — never a missing key. A caller that must ask whether
    the field exists cannot tell 'nothing left behind' from 'nobody looked'."""
    c, _ = api_client
    make_routine(slug="plainr")

    body = c.post("/api/routines/plainr/archive").json()
    assert body["external_residue"] == []
