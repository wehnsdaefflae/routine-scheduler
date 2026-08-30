"""The reverse reading: who depends on a library document, and does a change break them?

The case these exist for is the one the library's own design creates: one copy of every util
and rule, reaching every holder at its next run with no migration. A revision that is correct
for the routine asking can silently break three others, and until now the approval question
named none of them.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from rsched.library_impact import holders, impact, impact_lines


def _server(tmp_path: Path) -> SimpleNamespace:
    lib, routines = tmp_path / "lib", tmp_path / "routines"
    for sub in ("utils", "permissions", "rules"):
        (lib / sub).mkdir(parents=True)
    routines.mkdir()
    return SimpleNamespace(libraries_home=lib, permissions_home=lib / "permissions",
                           rules_home=lib / "rules", routines_home=routines, machines={})


def _util_src(name, *, secrets="(none)", fs="none", calls="(none)") -> str:
    return (f'"""{name} — t.\n\nusage: gu {name}\ncalls: {calls}\ntags: t\n'
            f'secrets: {secrets}\nnet: none\nfs: {fs}\n"""\n')


def _util(server, name, **kw) -> None:
    d = server.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(_util_src(name, **kw), encoding="utf-8")


def _routine(server, slug, **cfg) -> None:
    d = server.routines_home / slug
    d.mkdir(parents=True, exist_ok=True)
    base = {"description": "t", "permissions": [], "rules": [],
            "capabilities": {"actions": [], "utils": [], "util_tags": []}}
    (d / "routine.yaml").write_text(yaml.safe_dump({**base, **cfg}), encoding="utf-8")


@pytest.fixture
def empty_store(monkeypatch):
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)


@pytest.fixture
def client_lib(api_client, monkeypatch):
    """The hermetic app over a one-util library with one holding routine, plus the source
    builder — so the web writer is exercised through the real routes, not a stand-in."""
    from types import SimpleNamespace

    client, _tmp = api_client
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"NEW_PIN": "x"})
    server = client.app.state.server
    shim = SimpleNamespace(libraries_home=server.libraries_home,
                           routines_home=server.routines_home)
    for sub in ("utils", "permissions", "rules"):
        (server.libraries_home / sub).mkdir(parents=True, exist_ok=True)
    _util(shim, "sig")
    _routine(shim, "holder", capabilities={"utils": ["sig"]})
    return client, _util_src


@pytest.mark.usefixtures("empty_store")
def test_holders_finds_routines_by_kind(tmp_path):
    server = _server(tmp_path)
    _util(server, "poster")
    _routine(server, "holder", capabilities={"utils": ["poster"]}, rules=["care"],
             permissions=["messaging"])
    _routine(server, "bystander")
    assert holders(server, "util", "poster") == ["holder"]
    assert holders(server, "rule", "care") == ["holder"]
    assert holders(server, "permission", "messaging") == ["holder"]
    assert holders(server, "util", "nobody-holds-this") == []


@pytest.mark.usefixtures("empty_store")
def test_holders_reaches_through_the_calls_tree(tmp_path):
    """A util's `calls:` line pulls its callees into the same jail and env, so revising a util
    a routine never NAMES can still change what the util it does name requires."""
    server = _server(tmp_path)
    _util(server, "leaf")
    _util(server, "top", calls="leaf")
    _routine(server, "holder", capabilities={"utils": ["top"]})
    assert holders(server, "util", "leaf") == ["holder"]


@pytest.mark.usefixtures("empty_store")
def test_a_new_secret_declaration_names_who_it_breaks(tmp_path, monkeypatch):
    """The headline case: routine-improver revises a util, adds a secret, and two routines that
    nobody touched stop working at their next run."""
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"NEW_PIN": "x"})
    server = _server(tmp_path)
    _util(server, "signal")
    _routine(server, "granted", capabilities={"utils": ["signal"]},
             grants={"secret:NEW_PIN": True})
    _routine(server, "undecided", capabilities={"utils": ["signal"]})
    _routine(server, "declined", capabilities={"utils": ["signal"]},
             grants={"secret:NEW_PIN": False})

    result = impact(server, "util", "signal", _util_src("signal", secrets="NEW_PIN"))
    assert sorted(result["holders"]) == ["declined", "granted", "undecided"]
    broken = {b["slug"] for b in result["breaks"]}
    assert broken == {"undecided", "declined"}
    assert result["unaffected"] == ["granted"]
    lines = impact_lines(result)
    assert any("BREAKS undecided" in ln and "NEW_PIN" in ln for ln in lines)


@pytest.mark.usefixtures("empty_store")
def test_a_harmless_revision_breaks_nobody(tmp_path):
    server = _server(tmp_path)
    _util(server, "poster")
    _routine(server, "holder", capabilities={"utils": ["poster"]})
    result = impact(server, "util", "poster", _util_src("poster") + "# a comment\n")
    assert result["breaks"] == [] and result["unaffected"] == ["holder"]
    assert impact_lines(result) == ["binds: holder", "breaks none of them"]


@pytest.mark.usefixtures("empty_store")
def test_a_new_private_store_breaks_holders_without_the_root(tmp_path):
    server = _server(tmp_path)
    _util(server, "sig")
    _routine(server, "rooted", capabilities={"utils": ["sig"]},
             fs_write_roots=["/srv/store"])
    _routine(server, "rootless", capabilities={"utils": ["sig"]})
    result = impact(server, "util", "sig", _util_src("sig", fs="rw /srv/store"))
    assert [b["slug"] for b in result["breaks"]] == ["rootless"]


@pytest.mark.usefixtures("empty_store")
def test_a_rule_gaining_an_expectation_is_an_interrupt_not_a_break(tmp_path):
    """`expects:` stays advisory even here: a rule revision can warn its holders, never fail
    them, or the soft edge would acquire teeth through the back door."""
    server = _server(tmp_path)
    (server.rules_home / "status-page.md").write_text(
        "---\ntags: [a, b, c]\n---\n# rule: status page — x\n", encoding="utf-8")
    _routine(server, "publisher", rules=["status-page"])
    result = impact(server, "rule", "status-page",
                    "---\ntags: [a, b, c]\nexpects:\n  fs-write: ['*']\n---\n"
                    "# rule: status page — x\n")
    assert [b["slug"] for b in result["breaks"]] == ["publisher"]
    assert all(g.startswith("interrupts:") for g in result["breaks"][0]["gains"])


@pytest.mark.usefixtures("empty_store")
def test_deletion_is_the_same_question_with_no_content(tmp_path):
    server = _server(tmp_path)
    _util(server, "leaf", secrets="LEAF_TOKEN")
    _util(server, "top", calls="leaf")
    _routine(server, "holder", capabilities={"utils": ["top"]})
    # deleting `leaf` removes the secret requirement its caller inherited — strictly fewer
    # unmet rows, so nothing BREAKS even though the library lost a document
    result = impact(server, "util", "leaf", None)
    assert result["holders"] == ["holder"] and result["breaks"] == []


@pytest.mark.usefixtures("empty_store")
def test_the_digest_changes_with_the_answer(tmp_path):
    """The confirm token: a library that moved between preview and save must re-prompt rather
    than let somebody approve an impact they were never shown."""
    server = _server(tmp_path)
    _util(server, "sig")
    _routine(server, "a", capabilities={"utils": ["sig"]})
    first = impact(server, "util", "sig", _util_src("sig"))
    assert first["digest"] == impact(server, "util", "sig", _util_src("sig"))["digest"]
    _routine(server, "b", capabilities={"utils": ["sig"]})
    assert impact(server, "util", "sig", _util_src("sig"))["digest"] != first["digest"]


@pytest.mark.usefixtures("empty_store")
def test_the_real_library_is_never_touched(tmp_path):
    """The shadow is symlinks into a temp dir; a preview that mutated the library would be a
    preview nobody could trust."""
    server = _server(tmp_path)
    _util(server, "sig", secrets="OLD")
    _routine(server, "holder", capabilities={"utils": ["sig"]})
    impact(server, "util", "sig", _util_src("sig", secrets="NEW"))
    live = (server.libraries_home / "utils" / "sig" / "main.py").read_text()
    assert "OLD" in live and "NEW" not in live


@pytest.mark.usefixtures("empty_store")
def test_nothing_held_yields_a_plain_answer(tmp_path):
    server = _server(tmp_path)
    _util(server, "fresh")
    assert impact_lines(impact(server, "util", "fresh", _util_src("fresh"))) == [
        "binds no routine yet"]


# --- the second writer: the Library tab, which had no approval to hang this on -------------

def test_the_library_save_refuses_a_breaking_change_without_the_token(client_lib):
    """The engine's authoring path has an approval question; the Library tab has none, so the
    confirm digest is what stops a hand edit reaching every holder unannounced."""
    client, src = client_lib
    breaking = src("sig", secrets="NEW_PIN")

    preview = client.post("/api/library/utils/sig/impact", json={"content": breaking})
    assert preview.status_code == 200
    result = preview.json()
    assert [b["slug"] for b in result["breaks"]] == ["holder"]

    blind = client.put("/api/library/utils/sig", json={"content": breaking})
    assert blind.status_code == 409
    assert "NEW_PIN" in blind.text and result["digest"] in blind.text

    stale = client.put("/api/library/utils/sig",
                       json={"content": breaking, "impact_digest": "0" * 16})
    assert stale.status_code == 409


def test_a_harmless_save_needs_no_token(client_lib):
    """Only a BREAKING change is gated. Requiring a round-trip for every edit would train the
    operator to paste the token without reading it, which is worse than no gate."""
    client, src = client_lib
    ok = client.put("/api/library/utils/sig", json={"content": src("sig") + "# tweak\n"})
    assert ok.status_code == 200, ok.text
