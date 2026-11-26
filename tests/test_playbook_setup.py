"""The setup a playbook PROPOSES for the conversation it seeds (D313): sanitized against the
live library, credential-free by construction, and carrying nothing outside the allowlist. The
operator authorized visible, editable prefill with one explicit confirmation (2026-11-25,
option b) under three boundaries he stated himself — no silent grants, no credential material,
and the normal grant validator still applying — and these are the two of the three a Python
test can hold: what the proposal CONTAINS, and what it refuses to contain. That selecting a
playbook changes nothing until the confirmation is the browser's half
(tests/ui/test_playbook_setup_confirm.py), because the confirmation is a click.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import authed_client, seeded_server
from rsched import playbook_setup, playbooks

LIB_RULES = ["ask-policy", "fix-the-cause", "verify-independently"]
LIB_PERMS = ["shell", "browser-sessions"]


def _prop(setup, **kw):
    kw.setdefault("rules", LIB_RULES)
    kw.setdefault("permissions", LIB_PERMS)
    return playbook_setup.proposal({"setup": setup}, **kw)


def test_a_shell_bearing_setup_is_proposed_whole():
    """The operator declined pre-filling only the harmless fields, so `shell` is proposed like
    everything else — what protects it is the confirmation, not withholding the field."""
    out = _prop({"permissions": ["shell"], "rules": ["fix-the-cause", "ask-policy"],
                 "workdir": "/srv/data", "fs_write_roots": ["/srv/data"],
                 "fs_read_roots": ["/etc"], "note": "shell is the\n  point here"})
    assert out["permissions"] == ["shell"]
    assert out["rules"] == ["fix-the-cause", "ask-policy"]       # the document's own order
    assert out["workdir"] == "/srv/data"
    assert out["fs_write_roots"] == ["/srv/data"]
    assert out["fs_read_roots"] == ["/etc"]
    assert out["note"] == "shell is the point here"              # one line, for one row
    assert out["declares"] is True
    assert out["unknown"] == [] and out["unresolved"] == []


def test_connections_come_back_as_provider_names_and_never_an_account():
    """Boundary 2, as a property of the return value: a `{provider: account}` map loses the
    accounts, so a confirmed proposal has no account to bind and cannot carry an identity."""
    out = _prop({"connections": {"google": "mark@example.com", "notion": "ws-7781"}})
    assert out["connections"] == ["google", "notion"]
    # the account labels, not substrings of them: "work" would match the `workdir` KEY and the
    # assertion would red on the shape of the answer rather than on a leak
    assert "mark@example.com" not in repr(out) and "ws-7781" not in repr(out)


def test_a_key_outside_the_allowlist_is_dropped_and_named():
    """A playbook is hand-editable and model-written, so `setup:` is untrusted input: a
    capability or a secret declared there reaches nothing, and says so on the page."""
    out = _prop({"capabilities": {"actions": ["shell"]}, "secrets": ["OPENROUTER_API_KEY"],
                 "rules": ["ask-policy"]})
    assert out["rules"] == ["ask-policy"]
    assert out["unknown"] == ["setup.capabilities", "setup.secrets"]
    assert "capabilities" not in out and "secrets" not in out
    assert "OPENROUTER_API_KEY" not in repr(out)


def test_a_slug_the_library_does_not_hold_is_not_proposed():
    """The create path 400s on an unknown rule, so a pre-fill that cannot be submitted is worse
    than an empty one: it is dropped and named instead."""
    out = _prop({"rules": ["ask-policy", "no-such-rule"], "permissions": ["shell", "invented"]})
    assert out["rules"] == ["ask-policy"] and out["permissions"] == ["shell"]
    assert out["unknown"] == ["permissions:invented", "rules:no-such-rule"]


def test_an_unresolved_placeholder_is_never_offered_as_a_path():
    """`{{workdir}}` is a parameter for the person to fill (the machine-shell-task convention);
    offered literally it would be a grant nobody meant to a directory that does not exist."""
    out = _prop({"workdir": "{{workdir}}", "fs_write_roots": ["{{workdir}}", "/srv/real"]})
    assert out["workdir"] == "" and out["fs_write_roots"] == ["/srv/real"]
    assert out["unresolved"] == ["{{workdir}}"]
    assert out["declares"] is True        # there IS something to show: the parameter to fill


def test_a_playbook_with_no_setup_declares_nothing():
    """The console mounts nothing then — an empty panel is a thing a person reads and dismisses."""
    for meta in ({}, {"setup": None}, {"setup": {}}, {"setup": "shell"}):
        out = playbook_setup.proposal(meta, rules=LIB_RULES, permissions=LIB_PERMS)
        assert out["declares"] is False and out["permissions"] == [] and out["rules"] == []


def test_a_string_where_a_list_belongs_is_one_entry_not_one_per_character():
    """A hand-edited `permissions: shell` is five letters to a naive iterator — how the
    distiller once tagged a playbook `e v a l u a` (workflows/lint.lint_playbook_text)."""
    out = _prop({"permissions": "shell", "rules": "ask-policy"})
    assert out["permissions"] == ["shell"] and out["rules"] == ["ask-policy"]


def test_from_config_keeps_the_providers_and_drops_the_accounts():
    """F954's save half: a saved playbook records the settings its conversation actually needed,
    so the next one is offered them — minus anything naming whose account the work ran as."""
    cfg = SimpleNamespace(permissions=["shell"], rules=["fix-the-cause", "ask-policy"],
                          workdir="/srv/data", fs_write_roots=["/srv/data"], fs_read_roots=[],
                          connections={"google": "mark@example.com"})
    out = playbook_setup.from_config(cfg)
    assert out["connections"] == {"google": ""}
    assert "mark@example.com" not in repr(out)
    assert out["rules"] == ["ask-policy", "fix-the-cause"] and out["permissions"] == ["shell"]
    assert "fs_read_roots" not in out            # an empty field is no declaration


def test_from_config_on_a_bare_conversation_writes_no_setup_block():
    """Nothing to preserve means no `setup:` key at all, rather than a block of empty lists."""
    assert playbook_setup.from_config(SimpleNamespace()) == {}


# ---- the API surface the console reads ---------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    server = seeded_server(tmp_path, kinds=("workflows", "rules", "permissions", "playbooks"))
    with authed_client(server) as c:
        yield c, server


def test_the_catalog_carries_each_playbooks_proposed_setup(client):
    """The picker and the Library tab read ONE proposal, computed where the live library's rule
    and permission slugs are known — not in JavaScript, and not twice."""
    c, _server = client
    r = c.get("/api/playbooks")
    assert r.status_code == 200
    items = {p["slug"]: p for p in r.json()["playbooks"]}
    shell = items["machine-shell-task"]["setup"]
    assert shell["declares"] is True
    assert shell["permissions"] == ["shell"]
    assert "fix-the-cause" in shell["rules"] and "ask-policy" in shell["rules"]
    assert shell["note"].startswith("`shell` is the permission")
    # the seed writes `workdir: '{{workdir}}'` — a parameter, so nothing is pre-filled for it
    assert shell["workdir"] == "" and "{{workdir}}" in shell["unresolved"]
    assert shell["unknown"] == []


def test_no_catalog_entry_carries_an_account_label(client):
    """Every seeded playbook, read through the route the console calls: `connections` is a list
    of provider names, so there is no account anywhere in what the browser receives."""
    c, _ = client
    for p in c.get("/api/playbooks").json()["playbooks"]:
        conns = p["setup"]["connections"]
        assert isinstance(conns, list)
        assert all(isinstance(x, str) and "@" not in x for x in conns), p["slug"]


def test_a_playbook_declaring_a_capability_gets_no_capability_from_the_catalog(client):
    """The one door to a capability is holding the permission doc that requires it. A playbook
    that writes one into `setup:` is answered with a name in `unknown`."""
    c, server = client
    playbooks.write_playbook(server.libraries_home, "sneaky", main=(
        "---\nslug: sneaky\ntitle: Sneaky\nwhen: never\naxis: nothing\ntags:\n- x\n"
        "setup:\n  capabilities:\n    actions:\n    - shell\n  permissions:\n  - shell\n"
        "---\n\n## Instructions\n1. do it\n"))
    entry = {p["slug"]: p for p in c.get("/api/playbooks").json()["playbooks"]}["sneaky"]
    assert entry["setup"]["unknown"] == ["setup.capabilities"]
    assert entry["setup"]["permissions"] == ["shell"]      # proposed for confirmation, not held
    assert "capabilities" not in entry["setup"]
