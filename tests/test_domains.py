"""DOMAINS — the shared-surface axis: the config block a routine inherits, the shared store
its runs get as an fs root, and the membership it declares in its OWN routine.yaml.

The temporal axis is a LANE, with its own module and its own file (tests/test_lanes.py). When a
set of routines fires is a different question from what those routines share. One record
answering both quantizes the looser question by the stricter one, because timing forces
exclusivity: four cadences over one shared surface then mean four byte-identical config blocks.

The invariant these pin is that a routine has AT MOST ONE domain and names it itself. A routine
free to sit in several shared-config records has to merge them under some order, every such
order is arbitrary, and what it inherits then turns on where rows happen to sit in a JSON file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rsched import domains

TS = "20260805-070000"


def _home_with_domain(tmp_path: Path, own: dict, shared: dict | None,
                      *, name: str = "FAU") -> tuple[Path, str]:
    """A routines home with one routine that (optionally) names a domain sharing config."""
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    did = ""
    if shared is not None:
        did = domains.create(home, name=name, config=shared)["id"]
        own = {**own, "domain": did}
    d = home / "demo"
    d.mkdir(exist_ok=True)
    (d / "routine.yaml").write_text(
        yaml.safe_dump({"slug": "demo", "description": "d", **own}), encoding="utf-8")
    return d, did


# -- the store ------------------------------------------------------------------------------

def test_store_root_is_zero_or_one_and_created_lazily(tmp_path):
    """`member_store_roots` returns zero roots or exactly one, because a routine has at most
    one domain — a list rather than an Optional only because every caller splices it into the
    run's fs roots.
    """
    home = tmp_path
    rec = domains.create(home, name="Morning", config={})
    roots = domains.member_store_roots(home, rec["id"])
    assert roots == [domains.store_dir(home, rec["id"])]
    assert not roots[0].exists()                       # lookup alone materializes nothing
    assert domains.member_store_roots(home, rec["id"], create=True)[0].is_dir()
    assert domains.member_store_roots(home, "") == []          # no domain, no root
    assert domains.member_store_roots(home, "dom-gone") == []   # a stale id is not a root


def test_a_run_in_a_domain_reads_and_writes_the_shared_store(make_routine, scripted):
    """End to end: the routine's own `domain:` key gets .control/group-stores/<id>/ as an
    injected fs read+write root — file actions on it succeed with no grant dance.

    An id is an ADDRESS, not a label. A domain's id may read `dom-` or `grp-`, the same id may
    name a lane as well, and neither the kind of object nor the store it lives in is derivable
    from it — live routines address these paths out of their own memory, so the path and the
    ids are held stable rather than made descriptive.
    """
    from rsched.engine.runtime import run_routine
    from rsched.engine.transcript import read_events
    from test_loop import _server

    d = make_routine(slug="member")
    server = _server(d)
    rec = domains.create(server.routines_home, name="Pipeline", config={})
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["domain"] = rec["id"]
    (d / "routine.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    store = domains.store_dir(server.routines_home, rec["id"])
    scripted([
        {"say": "leave a note for the others", "kind": "write_file",
         "path": str(store / "member-status.md"), "content": "ingest done\n"},
        {"say": "read it back", "kind": "read_file", "path": str(store / "member-status.md")},
        {"say": "done", "kind": "finish", "status": "ok",
         "summary": "wrote and re-read the shared note, eight words here now yes ok done"},
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert (store / "member-status.md").read_text() == "ingest done\n"
    events = read_events(run_dir / "transcript.jsonl")[0]
    reads = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "read_file"]
    assert reads and not reads[0]["payload"].get("error")


def test_a_run_with_no_domain_has_no_store_root(tmp_path):
    from types import SimpleNamespace

    from rsched.config import ServerConfig
    from rsched.engine.budgets_config import Budgets
    from rsched.engine.run_context import RunContext
    from rsched.engine.transcript import Transcript

    routine = SimpleNamespace(slug="solo", dir=tmp_path / "solo",
                              fs_read_roots=[], fs_write_roots=[])
    (tmp_path / "solo").mkdir()
    ctx = RunContext(routine=routine, server=ServerConfig(), registry=None,
                     run_ts=TS, run_dir=tmp_path / "solo" / "runs" / "x",
                     transcript=Transcript(tmp_path / "t.jsonl"),
                     budgets=Budgets(max_turns=1, max_wall_clock_min=1,
                                     max_total_tokens=1, max_subruns=1,
                                     max_subrun_depth=1, ask_timeout_min=1))
    assert ctx.domain_store_roots == []
    assert ctx.read_roots() == [] and ctx.write_roots() == []


# -- membership -----------------------------------------------------------------------------

def test_membership_is_read_from_the_routines_not_stored_on_the_domain(tmp_path):
    """One place, so it cannot disagree with itself — and a routine deleted from disk is out
    of the domain by construction rather than by a cascade nobody remembered to write.

    Sorted, because the caller list is display and comparison surfaces (the domain page, the
    notes contract line, `rsched validate`) and directory order is not a meaning.

    Reading membership off N files is also N chances to hit one mid-edit, which is why every
    unreadable shape has to be a NON-MEMBER rather than an exception: a domain page must not
    fail because somebody is halfway through saving a routine.yaml.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    rec = domains.create(home, name="FAU", config={})
    for slug, dom in (("a", rec["id"]), ("b", rec["id"]), ("c", ""), ("d", "dom-other")):
        (home / slug).mkdir()
        (home / slug / "routine.yaml").write_text(
            yaml.safe_dump({"slug": slug, **({"domain": dom} if dom else {})}), encoding="utf-8")
    assert domains.members(home, rec["id"]) == ["a", "b"]
    # a file that parses but is not a mapping has no `domain:` to read
    (home / "scalar").mkdir()
    (home / "scalar" / "routine.yaml").write_text("just a sentence\n", encoding="utf-8")
    assert domains.members(home, rec["id"]) == ["a", "b"]
    # …and neither does one that does not parse at all (read_yaml raises yaml.YAMLError,
    # which is the whole point of `read_yaml` not swallowing errors — the leniency belongs
    # here, at the caller that can afford it)
    (home / "broken").mkdir()
    (home / "broken" / "routine.yaml").write_text("{{ not yaml", encoding="utf-8")
    assert domains.members(home, rec["id"]) == ["a", "b"]


def test_deleting_a_domain_leaves_its_store_on_disk(tmp_path):
    """It holds files members wrote. A config record disappearing is not consent to delete
    data nobody asked about.
    """
    home = tmp_path
    rec = domains.create(home, name="FAU", config={})
    store = domains.store_dir(home, rec["id"])
    store.mkdir(parents=True)
    (store / "conventions.md").write_text("shared\n", encoding="utf-8")
    assert domains.delete(home, rec["id"]) is True
    assert domains.delete(home, rec["id"]) is False            # idempotent
    assert (store / "conventions.md").read_text() == "shared\n"


# -- the shared config (D82) -----------------------------------------------------------------

def test_config_keeps_known_keys_and_drops_the_rest(tmp_path):
    rec = domains.create(tmp_path, name="G")
    assert rec["config"] == {}
    out = domains.update(tmp_path, rec["id"], config={
        "permissions": ["memory", "", 7], "grants": {"secret:X": True},
        "enabled": False, "schedule": {"cron": "0 7 * * *"}, "slug": "nope"})
    # identity/lifecycle keys are NOT shareable; junk inside a list is dropped
    assert out["config"] == {"permissions": ["memory"], "grants": {"secret:X": True}}
    # config REPLACES wholesale — dropping a key hands it back to the members
    assert domains.update(tmp_path, rec["id"], config={})["config"] == {}


def test_a_routine_inherits_its_domains_config_and_its_own_keys_win(tmp_path):
    from rsched.config.routine import load_routine

    d, _ = _home_with_domain(
        tmp_path,
        {"permissions": ["memory"], "capabilities": {"actions": ["memory_read"],
                                                     "confirm": "never"},
         "budgets": {"max_turns": 5}},
        {"permissions": ["global-utils"],
         "capabilities": {"actions": ["memory_write"], "utils": ["shell"], "confirm": "always"},
         "budgets": {"max_turns": 99, "max_wall_clock_min": 60},
         "grants": {"secret:FAU_USER": True},
         "fs_read_roots": ["/srv/shared"]})
    cfg, _ = load_routine(d)
    assert cfg.permissions == ["memory", "global-utils"]          # lists UNION
    assert cfg.capabilities["actions"] == ["memory_read", "memory_write"]
    assert cfg.capabilities["utils"] == ["shell"]                 # only the domain set it
    assert cfg.capabilities["confirm"] == "never"                 # the routine's dial wins
    assert cfg.budgets["max_turns"] == 5                          # routine wins per key
    assert cfg.budgets["max_wall_clock_min"] == 60                # …and inherits the rest
    assert cfg.grants == {"secret:FAU_USER": True}
    assert [str(p) for p in cfg.fs_read_roots] == ["/srv/shared"]
    assert cfg.inherited_from == "FAU"
    assert set(cfg.inherited) == {"permissions", "capabilities", "budgets", "grants",
                                  "fs_read_roots"}


def test_without_a_domain_the_routine_is_exactly_its_own_file(tmp_path):
    """Leaving a domain must return every setting to routine.yaml — nothing is written back."""
    from rsched.config.routine import load_routine

    d, _ = _home_with_domain(tmp_path, {"permissions": ["memory"],
                                        "budgets": {"max_turns": 5}}, None)
    cfg, _ = load_routine(d)
    assert cfg.permissions == ["memory"] and not cfg.inherited and cfg.inherited_from == ""
    assert yaml.safe_load((d / "routine.yaml").read_text())["permissions"] == ["memory"]


def test_a_stale_domain_id_inherits_nothing_rather_than_raising(tmp_path):
    """A routine naming a deleted domain inherits nothing — it is exactly its own file. The
    setup surface reports the dangling reference; a dangling reference must never be what
    stops a run booting.
    """
    from rsched.config.routine import load_routine

    d, did = _home_with_domain(tmp_path, {"permissions": ["memory"]},
                               {"permissions": ["global-utils"]})
    domains.delete(tmp_path / "routines", did)
    cfg, problems = load_routine(d)
    assert cfg.permissions == ["memory"] and cfg.inherited_from == ""
    assert not [p for p in problems if "domain" in p.lower()]


def test_identity_keys_are_never_inherited(tmp_path):
    from rsched.config.routine import load_routine

    d, _ = _home_with_domain(tmp_path, {"enabled": True, "name": "Mine"},
                             {"permissions": ["memory"]})
    cfg, _ = load_routine(d)
    assert cfg.enabled is True and cfg.name == "Mine"
    assert "enabled" not in cfg.inherited and "name" not in cfg.inherited


def test_saving_permissions_keeps_what_the_domain_covers(tmp_path):
    """Inherited permissions count for the FLOOR (they raise nothing). Regression, found
    moving the FAU routines onto a shared block: a floor computed from the routine's OWN
    permissions strips every capability the shared layer supplies — and the explicit "off"
    the save then writes shadows that layer forever, because the routine's own key wins.
    """
    from rsched.grants import capabilities_for, floor_capabilities, read_library_requires

    home = tmp_path / "library" / "permissions"
    home.mkdir(parents=True)
    (home / "run-history.md").write_text(
        "---\nrequires:\n  runs: last\n---\n# permission: run history — read previous runs\nb\n",
        encoding="utf-8")
    lib = read_library_requires(home)
    own, shared = [], ["run-history"]
    base = {"runs": "last"}
    # own-only floor loses the depth the DOMAIN's permission covers …
    assert floor_capabilities(own, lib, capabilities_for(own, lib, base))["runs"] == "none"
    # … counting the domain's keeps it, while the raise still comes from the routine alone
    assert floor_capabilities([*own, *shared], lib,
                              capabilities_for(own, lib, base))["runs"] == "last"


def test_a_dial_matching_the_domain_is_not_recorded_on_the_routine(tmp_path):
    """The raise/floor pair always emits a concrete dial, so without stripping, a save records
    e.g. `runs: none` nobody chose — and that copy shadows the domain forever, since the
    routine's own key wins. List members are NOT stripped: they union, so a redundant entry is
    harmless and keeping it preserves what the user ticked.
    """
    from rsched.config.domainconfig import strip_shared_dials

    shared = {"runs": "last", "workflows": "generate", "confirm": "never",
              "actions": ["memory_read"]}
    caps = {"runs": "none", "workflows": "catalog", "confirm": "always",
            "actions": ["memory_read", "detach"]}
    submitted = {"workflows": "catalog", "confirm": "always"}
    assert strip_shared_dials(caps, shared, submitted) == {
        "workflows": "catalog", "confirm": "always",
        "actions": ["memory_read", "detach"]}
    assert "runs" not in strip_shared_dials({"runs": "last"}, shared, {"runs": "last"})
    assert strip_shared_dials({"runs": "all"}, shared, {"runs": "all"}) == {"runs": "all"}
    assert strip_shared_dials(caps, {}, submitted) == caps
