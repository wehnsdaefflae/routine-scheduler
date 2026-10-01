"""The one-shot move onto settings patterns (MIGRATION(expires=2026-10-20)): a routine follows
its pattern and keeps what it uses, a domain's live values land in its members' own files, a
conversation's choices are translated, and a second boot changes nothing."""
import json

import pytest
import yaml

from conftest import make_test_server
from rsched import migrate_settings_patterns as mig
from rsched.config import load_routine
from rsched.patterns import drafts


@pytest.fixture
def instance(tmp_path, monkeypatch):
    server = make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"))
    home = server.routines_home
    ards = home / "ards"
    (ards / "state").mkdir(parents=True)
    (ards / "scripts").mkdir()
    (ards / "routine.yaml").write_text(yaml.safe_dump({
        "name": "ARDS", "slug": "ards", "domain": "grp-8bfd2aa6",
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "permissions": ["global-utils", "memory", "outbound-mail", "browser-sessions",
                        "remote-machines", "util-authoring", "util-revision"],
        "capabilities": {"actions": ["memory_read", "write_util", "detach"],
                         "utils": ["remote", "browser-session", "fau-mail-send"],
                         "util_tags": ["smtp"], "confirm": "never", "workflows": "generate",
                         "runs": "last", "reminders": "local"},
        "rules": ["intent-inference", "status-page", "ask-policy"],
        "machines": ["omen"],
        "grants": {"secret:DEEPGRAM_API_KEY": True, "secret:PANGRAM_API_KEY": True},
        "triggers": [{"id": "t-f0677c26", "type": "webhook", "token": "x", "cooldown_s": 900}],
        "fs_read_roots": ["/mnt/sshd_volume1/work/freelance/ARDS"]}), encoding="utf-8")
    (ards / "main.md").write_text("# ARDS\n\nDo the work.\n\n## Completion criteria\n\n"
                                  "- **Per run:** the page is republished.\n\n"
                                  "## Standing practices\n\n"
                                  "- `ask-policy` — ask well\n", encoding="utf-8")
    (ards / "state" / "stopping.json").write_text(json.dumps({"mode": "all", "groups": [],
        "conditions": [
            {"id": "s1", "text": "the page is republished", "scope": "run",
             "last_verdict": "unmet", "note": "the host refused the upload"},
            {"id": "s4", "text": "The deliverables are delivered to Susanne.", "scope": "goal",
             "status": "open"}]}), encoding="utf-8")
    (ards / "state" / "reminders.json").write_text(json.dumps({"reminders": [
        {"id": "r1", "regex": '^script name=store args=\\["set-field", "engagement"',
         "description": "d"},
        {"id": "r2", "regex": "^scrpt:store", "description": "never fires"}]}),
        encoding="utf-8")
    control = home / ".control"
    (control / "group-stores" / "grp-d06ce35e" / "notes" / "ards").mkdir(parents=True)
    (control / "group-stores" / "grp-d06ce35e" / "notes" / "ards" / "note-1.json").write_text(
        json.dumps({"from": "steward-hub-maintainer", "ts": "2026-09-21", "text": "fixed"}))
    (control / "group-stores" / "grp-d06ce35e" / "steward-hub-tab.txt").write_text("FAU\n")
    (control / "domains.json").write_text(json.dumps({"domains": [
        {"id": "grp-8bfd2aa6", "name": "FAU", "config": {}}]}))
    conv = tmp_path / "conversations" / "c-1"
    conv.mkdir(parents=True)
    (conv / "routine.yaml").write_text(yaml.safe_dump({
        "name": "c", "slug": "c-1", "kind": "conversation",
        "rules": ["intent-inference", "root-cause-fix", "web-research", "token-economy"],
        "permissions": ["global-utils", "util-revision", "personal-messaging", "darknet"],
        "capabilities": {"actions": ["memory_read", "detach", "write_util"],
                         "utils": ["signal", "darknet"], "util_tags": ["chat"],
                         "workflows": "catalog", "confirm": "always"},
        "models": {"main": None}}), encoding="utf-8")
    # only ards exists on this instance; every other spec entry is skipped
    monkeypatch.setattr(mig, "ROUTINES", {"ards": mig.ROUTINES["ards"]})
    from rsched import bootstrap
    bootstrap.seed_libraries(server.libraries_home)
    (server.libraries_home / "rules" / "intent-inference.md").write_text("old rule")
    (server.libraries_home / "templates").mkdir()
    return server


def test_a_routine_follows_its_pattern_and_keeps_what_it_uses(instance):
    record = mig.run_migration(instance)
    assert record["failed"] == {}
    rdir = instance.routines_home / "ards"
    raw = yaml.safe_load((rdir / "routine.yaml").read_text())
    assert raw["pattern"] == "project-steward" and "domain" not in raw
    # the pattern's conduct, plus the signed-in browser this routine is seen to use
    assert set(raw["permissions"]) == {"util-authoring", "outbound-mail", "steward-publishing",
                                       "browser-sessions"}
    assert set(raw["capabilities"]["utils"]) == {"gmail:send", "fau-mail:send",
                                                 "browser-session"}
    assert raw["capabilities"]["confirm"] == "creations"
    assert "util_tags" not in raw["capabilities"] and "workflows" not in raw["capabilities"]
    assert "status-page" not in raw["rules"] and "fix-the-cause" in raw["rules"]
    # the domain's live values are its own now; its store is an ordinary root
    assert raw["grants"]["secret:FAU_USER"] is True
    assert "/mnt/sshd_volume1/work" in raw["fs_read_roots"]
    assert "~/routines/.control/group-stores/grp-8bfd2aa6" in raw["fs_write_roots"]
    assert raw["hub_tab"] == "FAU"
    # dead weight goes; the gate is written
    assert raw["machines"] == [] and raw["triggers"] == []
    assert raw["run_gate"]["enabled"] and len(raw["run_gate"]["checks"]) == 5
    cfg, _problems = load_routine(rdir)
    assert cfg is not None and cfg.run_gate.enabled
    # the recipe loses the derived rule index; reminders match the script form runs emit now
    assert "Standing practices" not in (rdir / "main.md").read_text()
    rems = json.loads((rdir / "state" / "reminders.json").read_text())["reminders"]
    assert [r["regex"] for r in rems] == ["^script:store set-field engagement"]
    # the one that could never fire is gone and the routine is told so in its own words
    inbox = [m.read_text() for m in (rdir / "inbox").glob("msg-*.json")]
    assert any("could never fire" in m and "never fires" in m for m in inbox)


def test_judgment_calls_are_proposed_not_applied(instance):
    mig.run_migration(instance)
    raw = yaml.safe_load((instance.routines_home / "ards" / "routine.yaml").read_text())
    assert raw["grants"]["secret:DEEPGRAM_API_KEY"] is True       # still held…
    draft = drafts.read(instance.routines_home, "ards")
    assert draft and draft["message"] == "check the changes i recommend."
    assert "secret:DEEPGRAM_API_KEY" not in draft["changes"]["grants"]["value"]   # …proposed away


def test_a_credential_store_the_recipe_never_reads_is_proposed_away(instance, monkeypatch):
    path = instance.routines_home / "ards" / "routine.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["fs_read_roots"].append("~/.config/routine-scheduler")
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr(mig, "ROUTINES", {"ards": {
        **mig.ROUTINES["ards"], "draft_drop_read_roots": ["~/.config/routine-scheduler"]}})
    mig.run_migration(instance)
    raw = yaml.safe_load(path.read_text())
    assert "~/.config/routine-scheduler" in raw["fs_read_roots"]      # still held…
    change = drafts.read(instance.routines_home, "ards")["changes"]["fs_read_roots"]
    assert "~/.config/routine-scheduler" not in change["value"]       # …proposed away
    assert "credential store" in change["reason"]


def test_the_library_is_converted(instance):
    mig.run_migration(instance)
    lib = instance.libraries_home
    assert not (lib / "rules" / "intent-inference.md").exists()
    assert (lib / "rules" / "fix-the-cause.md").is_file()
    assert (lib / "patterns" / "project-steward.yaml").is_file()
    assert not (lib / "templates").exists()
    assert not (lib / "permissions" / "global-utils.md").exists()


def test_a_conversation_keeps_its_choices_translated(instance):
    mig.run_migration(instance)
    raw = yaml.safe_load((instance.conversations_home / "c-1" / "routine.yaml").read_text())
    assert raw["rules"] == ["fix-the-cause", "web-research"]
    assert raw["permissions"] == ["util-authoring", "darknet"]
    caps = raw["capabilities"]
    assert "util_tags" not in caps and "workflows" not in caps
    assert caps["actions"] == ["write_util"]
    assert caps["utils"] == ["darknet"]           # signal:send has no permission behind it here
    assert raw["models"] == {}


def test_domains_end_and_stranded_notes_arrive(instance):
    record = mig.run_migration(instance)
    control = instance.routines_home / ".control"
    assert not (control / "domains.json").exists()
    assert (control / "domains.retired-2026-09-29.json").is_file()
    assert record["notes_delivered"] == ["ards:note-1.json"]
    # the heading is each routine's own hub_tab now — the store's copy goes
    assert record["hub_tab_files"] == ["grp-d06ce35e"]
    assert not (control / "group-stores" / "grp-d06ce35e" / "steward-hub-tab.txt").exists()
    inbox = [m.read_text() for m in (instance.routines_home / "ards" / "inbox").glob("msg-*.json")]
    assert sum("fixed" in m for m in inbox) == 1


def test_a_commit_that_did_not_land_is_recorded_as_failed(instance):
    """The first run of this migration recorded `failed: {}` while two routines' edits sat
    staged under a stale index lock. A routine migrated on disk and not in its history is a
    failure the record names."""
    from rsched import libgit

    rdir = instance.routines_home / "ards"
    libgit.init_repo(rdir)
    (rdir / ".git" / "index.lock").write_bytes(b"")      # fresh: kept, so the commit fails
    record = mig.run_migration(instance)
    assert record["routines"]["ards"]["commit"] == "failed"
    assert record["failed"]["ards"].startswith("migrated, but the commit did not land: git add")
    assert mig.LIBRARY not in record["failed"]


def test_a_second_boot_changes_nothing(instance):
    mig.run_migration(instance)
    before = (instance.routines_home / "ards" / "routine.yaml").read_text()
    assert mig.run_migration(instance) == {}
    assert (instance.routines_home / "ards" / "routine.yaml").read_text() == before


def test_a_routine_that_would_not_load_is_restored(instance, monkeypatch):
    rdir = instance.routines_home / "ards"
    original = (rdir / "routine.yaml").read_text()
    spec = dict(mig.ROUTINES["ards"], gate={"enabled": True, "checks": []})   # invalid
    monkeypatch.setattr(mig, "ROUTINES", {"ards": spec})
    record = mig.run_migration(instance)
    assert "ards" in record["failed"]
    assert (rdir / "routine.yaml").read_text() == original


@pytest.mark.parametrize(("old", "new"), [
    ("^script name=swap_in .*--apply", "^script:swap_in .*--apply"),
    ('^script name=store args=\\["stage"', "^script:store stage"),
    ("^(script name=gate.*(release)|shell: x)", "^(script:gate.*(release)|shell: x)"),
])
def test_script_reminders_are_rewritten_to_the_canonical_form(old, new):
    assert mig.script_regex(old) == new


# ---- the goal: stopping conditions become a finish line and the recipe's Done when -------------

def test_goal_conditions_become_the_finish_line(instance):
    from rsched.engine import finishline

    mig.run_migration(instance)
    rdir = instance.routines_home / "ards"
    doc = finishline.load(rdir)
    assert [(o["id"], o["judge"], o["text"]) for o in doc["outcomes"]] == [
        ("g1", "run", "The deliverables are delivered to Susanne.")]
    assert not (rdir / "state" / "stopping.json").exists()


def test_the_completion_section_becomes_the_drafted_done_when(instance):
    from rsched import migrate_settings_patterns_goal as goal
    from rsched.engine import donewhen

    record = mig.run_migration(instance)
    main = (instance.routines_home / "ards" / "main.md").read_text(encoding="utf-8")
    drafted = json.loads(goal.DATA.read_text(encoding="utf-8"))["done_when"]["ards"]
    assert "## Completion criteria" not in main and "Standing practices" not in main
    assert drafted["block"].strip() in main and donewhen.problems(main) == []
    # the fixture's section is not the drafted one: replaced anyway and said so for review
    assert record["routines"]["ards"]["goal"]["done_when"] == "changed"


def test_what_the_last_run_left_unmet_reaches_the_inbox_once(instance):
    mig.run_migration(instance)
    inbox = [m.read_text() for m in (instance.routines_home / "ards" / "inbox").glob("msg-*.json")]
    carried = [m for m in inbox if "left unmet" in m]
    assert len(carried) == 1 and "the host refused the upload" in carried[0]


def test_a_drafted_section_found_verbatim_is_replaced_in_place(tmp_path, monkeypatch):
    from rsched import migrate_settings_patterns_goal as goal

    rdir = tmp_path / "r"
    rdir.mkdir()
    old = "## Completion criteria\n\n- done\n\n"
    (rdir / "main.md").write_text("# R\n\n" + old + "## Run status\n\nx\n", encoding="utf-8")
    drafted = {"replaces": old, "block": "## Done when\n\n- d1 — done\n",
               "never": ["never publish unverified numbers"]}
    assert goal._done_when(rdir, drafted) == "exact"
    assert (rdir / "main.md").read_text(encoding="utf-8") == (
        "# R\n\n## Done when\n\n- d1 — done\n\n## Never\n\n"
        "- never publish unverified numbers\n\n## Run status\n\nx\n")

