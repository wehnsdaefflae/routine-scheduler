"""WHO MAY CALL a util, which is not who HOLDS it.

The operator, 2026-11-18, looking at `code-search`'s editor: *"noone holds this util?! that's
not true"*. The panel said "no routine holds this yet — a change here reaches nobody" over a
util every routine on the instance can call, and that the engine reaches for itself: the
`code_search` and `websearch` ACTION KINDS are rewritten into that util call (D290,
`engine/kindaliases.py`) before any gate sees them.

The emptiness was real and its reading was inverted. `holders` answers "who holds a RESERVED
grant", and the library reserves a util only where a permission doc's `requires.utils` names
it — 7 of today's 56. `reach` is the second fact, beside the holder list rather than merged
into it, because a holder is a routine the BREAK analysis can reason about and an open util's
needs enter no routine's surface (`readmodels/surface._held_utils`).

The sibling cases live in test_library_impact.py; these are the reach ones, split out because
that file was at the 350-line bar.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from helpers import util_src, write_util
from rsched.library_impact import impact, impact_lines


def _server(tmp_path: Path) -> SimpleNamespace:
    lib, routines = tmp_path / "lib", tmp_path / "routines"
    for sub in ("utils", "permissions", "rules"):
        (lib / sub).mkdir(parents=True)
    routines.mkdir()
    return SimpleNamespace(libraries_home=lib, permissions_home=lib / "permissions",
                           rules_home=lib / "rules", routines_home=routines, machines={})


def _routine(server, slug, **cfg) -> None:
    d = server.routines_home / slug
    d.mkdir(parents=True, exist_ok=True)
    base = {"description": "t", "permissions": [], "rules": [],
            "capabilities": {"actions": [], "utils": []}}
    (d / "routine.yaml").write_text(yaml.safe_dump({**base, **cfg}), encoding="utf-8")

def _permission(server, slug: str, utils: list[str]) -> None:
    """A library permission doc whose `requires:` reserves `utils` — the only thing that makes
    a util un-callable by a routine that does not hold it."""
    (server.permissions_home).mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump({"effect": {"with": "w", "without": "o", "when": "n"},
                            "tags": ["t", "u", "v"], "requires": {"utils": utils}})
    (server.permissions_home / f"{slug}.md").write_text(
        f"---\n{front}---\n# permission: {slug} — t\n\nbody\n", encoding="utf-8")


@pytest.mark.usefixtures("empty_store")
def test_a_util_no_doc_reserves_reaches_every_routine_rather_than_nobody(tmp_path):
    """The operator's case, 2026-11-18: *"noone holds this util?! that's not true"*.

    The Library tab told him a change to `code-search` "reaches nobody" — while every routine
    in the fleet can call it, and the engine rewrites the `code_search` ACTION KIND (D290) into
    exactly that util call before any gate sees it. `holders` answers "who holds a RESERVED
    grant", which is empty for every open util in the library, and the page read that emptiness
    as "nobody is affected" — the most dangerous reading available, since the same computation
    gates DELETION.

    Only a util some permission doc RESERVES has holders at all; one no doc reserves is callable
    by all of them, so the reach is the whole fleet.
    """
    server = _server(tmp_path)
    write_util(server, "grep-ish")
    _routine(server, "a")
    _routine(server, "b")

    result = impact(server, "util", "grep-ish", util_src("grep-ish"))
    assert result["holders"] == []          # no RESERVED grant, which is what holders means
    assert result["reach"]["open_to_all"] is True
    assert result["reach"]["gated_by"] == []
    assert result["reach"]["fleet"] == 2
    assert impact_lines(result) == [
        "reserved by no permission doc — every routine (2) may call it"]


@pytest.mark.usefixtures("empty_store")
def test_a_reserved_util_still_reads_as_its_holders_alone(tmp_path):
    """The control: a util a doc reserves whole is callable only by the routines granted it, so
    the holder list IS the reach and nothing about this reading changes."""
    server = _server(tmp_path)
    write_util(server, "poster")
    _permission(server, "messaging", ["poster"])
    _routine(server, "holder", capabilities={"utils": ["poster"]})
    _routine(server, "bystander")

    result = impact(server, "util", "poster", util_src("poster"))
    assert result["holders"] == ["holder"]
    assert result["reach"]["open_to_all"] is False
    assert result["reach"]["gated_by"] == ["messaging"]
    assert impact_lines(result)[0] == "binds: holder"


@pytest.mark.usefixtures("empty_store")
def test_a_verb_reserved_util_is_held_by_some_and_callable_by_all(tmp_path):
    """`mail:send` reserves ONE verb and leaves the rest open (grants.py), so both readings are
    true at once: the holders hold the reserved verb, and every routine may call the others.
    A page that printed only one of them would be wrong in whichever direction it chose."""
    server = _server(tmp_path)
    write_util(server, "mailish")
    _permission(server, "outbound-mail", ["mailish:send"])
    _routine(server, "sender", capabilities={"utils": ["mailish:send"]})
    _routine(server, "reader")

    result = impact(server, "util", "mailish", util_src("mailish"))
    assert result["holders"] == ["sender"]
    assert result["reach"]["open_to_all"] is True      # its other verbs are open to everyone
    assert result["reach"]["gated_verbs"] == ["send"]
    assert any("send" in ln and "every routine" in ln for ln in impact_lines(result))


@pytest.mark.usefixtures("empty_store")
def test_a_rule_nobody_holds_still_reads_as_binding_nobody(tmp_path):
    """Reach is a UTIL question. A rule or a permission is held by naming it in routine.yaml,
    so an unheld one really does reach nobody and that wording must survive."""
    server = _server(tmp_path)
    _routine(server, "a")
    (server.rules_home).mkdir(parents=True, exist_ok=True)
    (server.rules_home / "care.md").write_text("# rule: care — t\n", encoding="utf-8")

    result = impact(server, "rule", "care", "# rule: care — t\n")
    assert result["holders"] == []
    assert result["reach"] is None
    assert impact_lines(result) == ["binds no routine yet"]
