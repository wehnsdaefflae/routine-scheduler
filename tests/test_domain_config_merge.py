"""A domain PATCH MERGES its config and removes only what it names (D140).

`PATCH /api/domains/{id}` passed `config` straight through to `domains.update`, which replaced
the shared block wholesale. A partial patch therefore DELETED every key it did not mention: one
such patch silently dropped FAU's 12 shared rules, 4 secret grants, all 8 budget dials, 3
fs_read_roots and rule_confirm (R1745, raised by config-optimizer).

The asymmetry is the part that bites. `RoutinePatch` is `extra='forbid'` and field-wise, so one
verb meant opposite things on the two surfaces — and the routine one is the one every caller
learns first.

But merge alone would have broken the editor invisibly. `static/components/domainconfig.js`
PATCHes the WHOLE config on every control and uses omission AS its removal mechanism, so under
merge, unticking a rule or clearing budgets would become a silent no-op. Hence the pair: merge
semantics PLUS an explicit removal form, with the editor moved onto it in the same change.

Two facts made the data loss unrecoverable rather than annoying: `.control/domains.json` is in
no git repo, and FAU came back only because config-optimizer's run happened to snapshot the
payload first.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server


@pytest.fixture
def client(tmp_path):
    from rsched.web.app import create_app

    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c, tmp_path


# Deliberately keys that need no library lookup: this file pins the MERGE contract, and a
# validator failure would mask it. Rule/permission validation has its own tests.
FULL = {
    "tags": ["fau", "grants"],
    "budgets": {"max_turns": 40, "max_wall_clock_min": 30},
    "grants": {"secret:FOO_KEY": True},
    "fs_read_roots": ["/srv/shared/a", "/srv/shared/b"],
}


def _make(c, config=None):
    r = c.post("/api/domains", json={"name": "FAU", "config": config or FULL})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---- merge ------------------------------------------------------------------------------


def test_a_partial_patch_keeps_every_key_it_does_not_mention(client):
    """The R1745 case, as a test: patching ONE key must not delete the other four."""
    c, _ = client
    did = _make(c)

    r = c.patch(f"/api/domains/{did}", json={"config": {"budgets": {"max_turns": 80}}})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]

    assert cfg["budgets"]["max_turns"] == 80, "the patched value lands"
    assert cfg["tags"] == FULL["tags"], "tags survived a patch that never mentioned them"
    assert cfg["grants"] == FULL["grants"]
    assert cfg["fs_read_roots"] == FULL["fs_read_roots"]


def test_patching_a_name_alone_touches_no_config_key(client):
    c, _ = client
    did = _make(c)

    r = c.patch(f"/api/domains/{did}", json={"name": "FAU (renamed)"})
    assert r.status_code == 200, r.text
    assert r.json()["config"] == FULL


# ---- removal, said out loud --------------------------------------------------------------


def test_remove_names_the_keys_to_drop(client):
    """Merge makes omission mean 'leave alone', so removal has to be SAID. Without this the
    editor's unticking would become a silent no-op — the reason this is one change."""
    c, _ = client
    did = _make(c)

    r = c.patch(f"/api/domains/{did}", json={"remove": ["grants", "fs_read_roots"]})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]

    assert "grants" not in cfg and "fs_read_roots" not in cfg
    assert cfg["tags"] == FULL["tags"], "removal drops what it names and nothing else"
    assert cfg["budgets"] == FULL["budgets"]


def test_an_explicit_null_removes_one_key_in_the_same_patch(client):
    """The per-key form, so one payload can set and clear together."""
    c, _ = client
    did = _make(c)

    r = c.patch(f"/api/domains/{did}",
                json={"config": {"budgets": {"max_turns": 99}, "grants": None}})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]

    assert cfg["budgets"]["max_turns"] == 99
    assert "grants" not in cfg
    assert cfg["tags"] == FULL["tags"]


def test_removing_everything_is_possible_but_must_be_asked_for(client):
    c, _ = client
    did = _make(c)

    r = c.patch(f"/api/domains/{did}", json={"remove": list(FULL)})
    assert r.status_code == 200, r.text
    assert r.json()["config"] == {}


# ---- the applied-field contract (R102) ---------------------------------------------------


def test_the_reply_says_which_of_the_three_landed(client):
    """A caller that cannot tell an applied key from an ignored one reports success for a
    change that never happened — the config bridge verifies every key against this list."""
    c, _ = client
    did = _make(c)

    r = c.patch(f"/api/domains/{did}",
                json={"name": "FAU2", "config": {"budgets": {"max_turns": 10}},
                      "remove": ["grants"]})
    assert r.status_code == 200, r.text
    assert set(r.json()["updated"]) == {"name", "config", "remove"}


def test_removing_a_key_that_is_not_there_is_not_an_error(client):
    """Idempotent: the editor may unset something already unset, and a retry must not 400."""
    c, _ = client
    did = _make(c, {"tags": ["fau"]})

    r = c.patch(f"/api/domains/{did}", json={"remove": ["budgets"]})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["tags"] == ["fau"]


# ---- the undo point (D140) ---------------------------------------------------------------


def test_every_save_keeps_the_previous_version(tmp_path):
    """`.control/domains.json` lives in no git repo, so a bad write had no undo at all: when a
    partial patch wiped a domain's whole shared block it came back only because the calling
    routine happened to snapshot its own payload first (R1745). Now the file keeps its own
    history beside it."""
    from rsched import domains

    rec = domains.create(tmp_path, name="FAU", config={"tags": ["one"]})
    # The FIRST write has nothing to preserve — there was no store before it.
    assert not list(domains.backups_dir(tmp_path).glob("domains-*.json"))

    domains.update(tmp_path, rec["id"], config={"tags": ["two"]})
    kept = sorted(domains.backups_dir(tmp_path).glob("domains-*.json"))
    assert kept, "the save that replaced a version kept the one it replaced"

    import json
    previous = json.loads(kept[-1].read_text(encoding="utf-8"))
    assert previous["domains"][0]["config"]["tags"] == ["one"], \
        "the backup holds what the store said BEFORE the save that replaced it"


def test_the_history_is_capped_so_it_cannot_grow_without_bound(tmp_path):
    from rsched import domains

    rec = domains.create(tmp_path, name="FAU", config={"tags": ["x"]})
    for i in range(domains.BACKUP_KEEP + 6):
        domains.update(tmp_path, rec["id"], config={"tags": [f"v{i}"]})

    kept = list(domains.backups_dir(tmp_path).glob("domains-*.json"))
    assert len(kept) <= domains.BACKUP_KEEP
