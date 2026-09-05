"""MIGRATION(expires=2026-12-01): one `group` becomes a LANE plus a DOMAIN.

The fixtures are the live topology in miniature, because that shape is the evidence the split
rests on: `Instance ·` groups carrying byte-identical config blocks, `Professional ·` ones doing
the same, one group with config and no members, one with members and no clock. The document
being converted holds 14 groups, 31 memberships and zero routines in more than one group.
"""

from __future__ import annotations

import json

import yaml

from rsched import domains
from rsched.migrate_group_split import plan, run

INSTANCE_CFG = {"permissions": ["global-utils"], "rules": ["ask-policy"],
                "budgets": {"turns": 40}}
PRO_CFG = {"rules": ["evidence-discipline"]}
FAU_CFG = {"permissions": ["global-utils", "outbound-mail"], "rules": ["ask-policy"]}


def _groups(tmp_path):
    control = tmp_path / ".control"
    control.mkdir(parents=True, exist_ok=True)
    doc = {"default_on_failure": "continue", "groups": [
        {"id": "grp-fau", "name": "FAU", "cron": "0 10 * * 1-5", "tz": "Europe/Berlin",
         "members": [{"slug": "nanogeofeld"}, {"slug": "ards"}], "config": FAU_CFG,
         "on_failure": "continue", "created": "2026-07-31T10:00:00+00:00"},
        {"id": "grp-nightly", "name": "Instance · Nightly · Maintenance", "cron": "0 2 * * *",
         "members": [{"slug": "library-sync"}, {"slug": "self-audit"}], "config": INSTANCE_CFG,
         "on_failure": "continue", "tz": "Europe/Berlin", "created": ""},
        {"id": "grp-tri", "name": "Instance · Triweekly · Maintenance", "cron": "0 5 * * 1,3,5",
         "members": [{"slug": "routine-improver"}], "config": INSTANCE_CFG,
         "on_failure": "continue", "tz": "Europe/Berlin", "created": ""},
        {"id": "grp-utils", "name": "Instance · Weekly · Utils", "cron": "0 3 * * 2",
         "members": [], "config": INSTANCE_CFG, "on_failure": "continue", "tz": "", "created": ""},
        {"id": "grp-pro-daily", "name": "Professional · Daily", "cron": "30 6 * * *",
         "members": [{"slug": "freelance-radar"}], "config": PRO_CFG,
         "on_failure": "continue", "tz": "Europe/Berlin", "created": ""},
        {"id": "grp-pro-bi", "name": "Professional · Biweekly", "cron": "30 8 * * 2,4",
         "members": [{"slug": "grants-radar"}], "config": PRO_CFG,
         "on_failure": "continue", "tz": "Europe/Berlin", "created": ""},
        {"id": "grp-ondemand", "name": "On demand", "cron": "",
         "members": [{"slug": "sprind"}], "config": {}, "on_failure": "continue",
         "tz": "", "created": ""},
        {"id": "grp-personal", "name": "Personal · Daily", "cron": "0 0 * * *",
         "members": [{"slug": "bina"}], "config": {}, "on_failure": "continue",
         "tz": "Europe/Berlin", "created": ""},
    ]}
    (control / "groups.json").write_text(json.dumps(doc), encoding="utf-8")
    # grp-pro-daily is the Professional group that actually holds shared files
    store = control / "group-stores" / "grp-pro-daily"
    store.mkdir(parents=True)
    (store / "contract.md").write_text("shared\n", encoding="utf-8")
    return tmp_path


def _amend(home, *records):
    """Append group records to the fixture document, for the shapes one test needs alone."""
    src = home / ".control" / "groups.json"
    doc = json.loads(src.read_text(encoding="utf-8"))
    doc["groups"].extend(records)
    src.write_text(json.dumps(doc), encoding="utf-8")
    return home


def _routine(tmp_path, slug, **extra):
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(
        yaml.safe_dump({"slug": slug, "name": slug, **extra}, sort_keys=False),
        encoding="utf-8")
    return d


def test_identical_config_blocks_collapse_to_one_domain(tmp_path):
    """D82's own failure mode, recreated by the cadence split: the `Instance ·` groups carry
    byte-identical config blocks because the temporal axis demands one record per cadence and
    the config axis has nowhere of its own to live. Clustering by the block's exact content
    needs nothing guessed about intent.
    """
    p = plan(_groups(tmp_path))
    by_name = {d["name"]: d for d in p["domains"]}
    assert set(by_name) == {"FAU", "Instance", "Professional"}
    assert sorted(by_name["Instance"]["from"]) == ["grp-nightly", "grp-tri", "grp-utils"]
    assert by_name["Instance"]["config"] == INSTANCE_CFG
    # the name is the leading segment the contributing names share
    assert by_name["Professional"]["config"] == PRO_CFG


def test_a_domain_inherits_the_id_of_the_group_that_holds_the_files(tmp_path):
    """No directory of shared files may move. Routines address these paths in their OWN
    memory — one live routine carries "READ /control/group-stores/grp-8bfd2aa6/…" as a
    standing prevention rule it wrote after an incident — so a moved store would silently
    falsify agent-authored notes rather than fail loudly.
    """
    p = plan(_groups(tmp_path))
    pro = next(d for d in p["domains"] if d["name"] == "Professional")
    assert pro["id"] == "grp-pro-daily"        # the one with contract.md, not the other


def test_one_id_can_name_both_a_lane_and_a_domain(tmp_path):
    """Whichever store it lands in, a migrated object keeps the id of the group it came from,
    because an id is an opaque handle nothing parses and store paths are addressed from routine
    memory. The consequence is the thing to hold on to: an id says neither which kind of object
    it names nor which store it lives in.
    """
    p = plan(_groups(tmp_path))
    lane_ids = {lane["id"] for lane in p["lanes"]}
    domain_ids = {d["id"] for d in p["domains"]}
    assert lane_ids & domain_ids == {"grp-fau", "grp-nightly", "grp-pro-daily"}
    assert all(i.startswith("grp-") for i in lane_ids | domain_ids)


def test_a_group_with_no_clock_becomes_a_tag_not_a_lane(tmp_path):
    """`On demand` names a set of routines and fires none of them. As a lane it would be a row
    that can never do anything; as a tag it is exactly what it is.
    """
    p = plan(_groups(tmp_path))
    assert [lane["id"] for lane in p["lanes"]] == [
        "grp-fau", "grp-nightly", "grp-tri", "grp-pro-daily", "grp-pro-bi", "grp-personal"]
    assert p["tags"] == {"sprind": ["on-demand"]}
    dropped = {name: why for _, name, why in p["dropped"]}
    assert dropped["Instance · Weekly · Utils"] == "no members"
    assert "became a tag" in dropped["On demand"]


def test_a_group_with_a_shared_block_and_no_clock_is_a_domain_and_a_tag(tmp_path):
    """The clock branch reads the CLOCK alone, so a group with members, a config block and no
    cron is BOTH: its block joins a domain, its members are stamped `domain:`, and its name
    still lands on them as a tag. That is the one outcome "no cron, so not a lane" does not
    predict, so it is pinned here rather than left to be discovered on a live instance.
    """
    home = _amend(_groups(tmp_path),
                  {"id": "grp-shelf", "name": "Shelf", "cron": "",
                   "members": [{"slug": "sprind"}], "config": FAU_CFG,
                   "on_failure": "continue", "tz": "", "created": ""})
    _routine(home, "sprind", tags=["research"])

    p = plan(home)
    assert "grp-shelf" not in [lane["id"] for lane in p["lanes"]]
    assert p["tags"]["sprind"] == ["on-demand", "shelf"]
    assert p["domain_of"]["sprind"] == "grp-fau"        # the same block, so the same domain

    run(home)
    cfg = yaml.safe_load((home / "sprind" / "routine.yaml").read_text(encoding="utf-8"))
    assert cfg["domain"] == "grp-fau"
    assert cfg["tags"] == ["research", "on-demand", "shelf"]


def test_a_slug_two_scheduled_groups_hold_keeps_the_first_lane(tmp_path):
    """The lane store enforces at-most-one (`lanes._claimed_elsewhere`), so a conversion that
    emitted a document violating it would converge on this instance and then refuse every
    later edit to the second lane. The first lane in file order keeps the slug and the drop is
    RECORDED so the boot log names the routine that moved.
    """
    home = _amend(_groups(tmp_path),
                  {"id": "grp-dup", "name": "Instance · Weekend", "cron": "0 9 * * 6",
                   "members": [{"slug": "self-audit"}, {"slug": "sprind"}],
                   "config": INSTANCE_CFG, "on_failure": "continue",
                   "tz": "Europe/Berlin", "created": ""})

    p = plan(home)
    by_id = {lane["id"]: lane for lane in p["lanes"]}
    assert [m["slug"] for m in by_id["grp-nightly"]["members"]] == ["library-sync", "self-audit"]
    assert [m["slug"] for m in by_id["grp-dup"]["members"]] == ["sprind"]
    why = next(w for gid, _, w in p["dropped"] if gid == "grp-dup")
    assert "self-audit" in why and "Instance · Nightly · Maintenance" in why
    # every lane's members are disjoint, which is what the store will accept
    seen: list[str] = [m["slug"] for lane in p["lanes"] for m in lane["members"]]
    assert len(seen) == len(set(seen))


def test_run_writes_both_stores_and_stamps_each_member(tmp_path):
    home = _groups(tmp_path)
    for slug in ("nanogeofeld", "ards", "library-sync", "self-audit", "routine-improver",
                 "freelance-radar", "grants-radar", "bina"):
        _routine(home, slug)
    _routine(home, "sprind", tags=["research"])

    assert run(home) > 0
    lanes = json.loads((home / ".control" / "lanes.json").read_text())
    domain_docs = json.loads((home / ".control" / "domains.json").read_text())
    assert lanes["default_on_failure"] == "continue"
    assert len(lanes["lanes"]) == 6 and len(domain_docs["domains"]) == 3
    assert all("config" not in lane for lane in lanes["lanes"])      # a lane shares nothing
    assert all("members" not in d for d in domain_docs["domains"])   # membership is per-routine

    def cfg(slug):
        return yaml.safe_load((home / slug / "routine.yaml").read_text(encoding="utf-8"))

    inst = next(d["id"] for d in domain_docs["domains"] if d["name"] == "Instance")
    assert cfg("library-sync")["domain"] == inst
    assert cfg("routine-improver")["domain"] == inst      # a different LANE, the same domain
    assert cfg("grants-radar")["domain"] == "grp-pro-daily"
    assert "domain" not in cfg("bina")                    # its group shared no config
    assert cfg("sprind")["tags"] == ["research", "on-demand"]   # kept its own, gained the label

    # groups.json is retired, so a second boot is a no-op rather than a second conversion
    assert not (home / ".control" / "groups.json").exists()
    assert run(home) == 0


def test_a_migrated_domain_is_the_document_the_store_itself_would_write(tmp_path):
    """A migrated record and one created through the web have to be indistinguishable to every
    reader. `domains.load` normalizes what it finds, so a record written without `created`
    reads back blank beside dated neighbours, while a block carrying a key `clean_config` drops
    leaves a setting on disk that nothing ever applies. Round-tripping the written file through
    the store catches both at once.
    """
    home = _groups(tmp_path)
    src = home / ".control" / "groups.json"
    doc = json.loads(src.read_text(encoding="utf-8"))
    doc["groups"][0]["config"] = {**FAU_CFG, "enabled": False, "schedule": "0 9 * * *"}
    src.write_text(json.dumps(doc), encoding="utf-8")

    run(home)
    written = json.loads((home / ".control" / "domains.json").read_text())["domains"]
    assert written == domains.load(home)["domains"]      # nothing is normalized away on read
    fau = next(d for d in written if d["name"] == "FAU")
    assert fau["config"] == FAU_CFG                      # enabled/schedule are not shareable
    assert all(d["created"] for d in written)
    assert domains.get(home, "grp-fau")["created"].startswith("20")


def test_a_hand_edited_source_costs_the_record_not_the_boot(tmp_path):
    """This runs on the daemon's upgrade boot with nothing catching what it raises, so a record
    it cannot read must cost that record alone. Each unreadable shape is skipped and NAMED in
    `dropped`, which is what the boot log prints — the same floor `lanes.load` and
    `domains.load` put under their own documents.
    """
    control = tmp_path / ".control"
    control.mkdir(parents=True)
    doc = {"default_on_failure": "whatever", "groups": [
        "a bare string where a record belongs",
        {"name": "no id at all", "cron": "0 1 * * *", "members": [{"slug": "orphan"}]},
        {"id": "grp-ok", "name": "Fine", "cron": "0 2 * * *",
         "members": ["bare-string-member", {"slug": "keeper"}, {"nope": 1}],
         "config": {"unknown-key": ["x"]}},
        {"id": "grp-oddmembers", "name": "Odd", "cron": "0 3 * * *",
         "members": {"slug": "one"}, "config": FAU_CFG},
    ]}
    (control / "groups.json").write_text(json.dumps(doc), encoding="utf-8")

    p = plan(tmp_path)
    assert [lane["id"] for lane in p["lanes"]] == ["grp-ok"]
    assert [m["slug"] for m in p["lanes"][0]["members"]] == ["keeper"]
    assert p["default_on_failure"] == "stop"        # an unknown policy is not a policy
    said = [(name, why) for _, name, why in p["dropped"]]
    assert ("record #0", "not a group object") in said
    assert ("no id at all", "no id") in said
    assert ("Fine", "2 membership entries named no routine") in said
    assert ("Fine", "its config block holds nothing a domain may share") in said
    assert ("Odd", "1 membership entry named no routine") in said

    # one lane, one domain (`Odd`'s block outlives its unreadable membership), no exception
    assert run(tmp_path) == 2
    assert not (control / "groups.json").exists()


def test_every_routine_lands_in_exactly_one_domain(tmp_path):
    """Why at most one, in one case. Merging several records' blocks cannot be made coherent —
    one such merge took "the first record's value wins the whole key" while unioning WITHIN a
    single record, so what a routine inherited depended on the order rows happened to sit in a
    JSON file. One domain per routine leaves no order to depend on.
    """
    p = plan(_groups(tmp_path))
    assert p["domain_of"] == {"nanogeofeld": "grp-fau", "ards": "grp-fau",
                              "library-sync": "grp-nightly", "self-audit": "grp-nightly",
                              "routine-improver": "grp-nightly",
                              "freelance-radar": "grp-pro-daily",
                              "grants-radar": "grp-pro-daily"}


def test_an_already_split_instance_is_converged_not_reconverted(tmp_path):
    """MIGRATION(expires=2026-12-01): the daemon boots from a bind-mounted checkout, so a
    restart while this module was being written ran an earlier draft of it against the live
    stores. Those domain records carry no `created` stamp and hold each group's config block
    verbatim rather than what `domains.clean_config` would keep — so the file says something
    the store never surfaces, and every domain born since carries a timestamp they lack.

    Repairing it here rather than by hand is the point: the fix is the same on every instance
    and it can run twice.
    """
    from rsched.migrate_group_split import run

    home = tmp_path
    (home / ".control").mkdir(parents=True)
    # what the earlier draft wrote: no `created`, and a key no domain may share
    (home / ".control" / "domains.json").write_text(json.dumps({"domains": [
        {"id": "grp-a", "name": "FAU", "config": {"rules": ["ask-policy"], "enabled": False}},
        {"id": "dom-b", "name": "Later", "config": {"rules": ["web-research"]},
         "created": "2026-09-01T10:00:00+00:00"},
    ]}), encoding="utf-8")

    assert run(home) == 2                                  # one stamp, one dropped key
    doc = json.loads((home / ".control" / "domains.json").read_text())
    fau, later = doc["domains"]
    assert fau["created"]                                  # stamped
    assert fau["config"] == {"rules": ["ask-policy"]}       # `enabled` is not shareable
    assert later["created"] == "2026-09-01T10:00:00+00:00"  # an existing stamp is not reset
    assert run(home) == 0                                  # idempotent


def test_a_stale_chain_run_directory_is_removed_not_converted(tmp_path):
    """In-flight chain records are ephemeral and the daemon drains every running chain before
    it restarts, so a record under the old directory is one nothing will read again.
    """
    from rsched.migrate_group_split import run

    home = tmp_path
    stale = home / ".control" / "group-runs"
    stale.mkdir(parents=True)
    (stale / "grp-a.json").write_text("{}", encoding="utf-8")
    run(home)
    assert not stale.exists()
