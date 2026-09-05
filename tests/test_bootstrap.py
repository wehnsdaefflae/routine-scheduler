"""One-time adoption of new default permissions (bootstrap.py)."""

import json

import yaml

from rsched import bootstrap
from rsched.bootstrap import _ADOPTED_MARKER, adopt_permissions

PERM = ("---\ntags: [a, b, c]\nrequires:\n  actions: [memory_read, memory_write]\n---\n"
        "# permission: memory — test notes\nbody\n")


def _mk_library(tmp_path):
    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)
    (perms / "memory.md").write_text(PERM, encoding="utf-8")
    return perms


def _set_permissions(routine_dir, slugs):
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["permissions"] = slugs
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_adopt_appends_slug_once(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r1")
    _set_permissions(d, ["util-authoring"])
    perms = _mk_library(tmp_path)
    home = tmp_path / "routines"

    assert adopt_permissions(home, perms) == 1
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["permissions"] == ["util-authoring", "memory"]
    assert json.loads((home / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["memory"]

    # The user revokes it later: adoption is marker-gated, so the next boot must NOT re-add it.
    _set_permissions(d, ["util-authoring"])
    assert adopt_permissions(home, perms) == 0
    assert yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["permissions"] \
        == ["util-authoring"]


def test_adopt_leaves_implicit_default_lists_alone(make_routine, tmp_path, monkeypatch):
    # No `permissions:` key = the routine follows DEFAULT_PERMISSIONS (which now includes
    # the slug). Writing an explicit list would SHRINK its held set.
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r2")
    perms = _mk_library(tmp_path)
    assert adopt_permissions(tmp_path / "routines", perms) == 0
    assert "permissions" not in yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))


def test_adopt_skips_dot_dirs_and_already_active(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r3")
    _set_permissions(d, ["memory"])
    hidden = tmp_path / "routines" / ".scratch-20260712-000000"
    hidden.mkdir(parents=True)
    (hidden / "routine.yaml").write_text("permissions: [util-authoring]\n", encoding="utf-8")
    perms = _mk_library(tmp_path)

    assert adopt_permissions(tmp_path / "routines", perms) == 0
    assert "memory" not in (hidden / "routine.yaml").read_text(encoding="utf-8")
    # already-adopted slugs are still marked done so the next boot skips the scan
    assert json.loads((tmp_path / "routines" / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["memory"]


def test_adopt_waits_for_a_library(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    make_routine(slug="r4")
    missing = tmp_path / "libraries" / "permissions"     # never created → no library yet
    assert adopt_permissions(tmp_path / "routines", missing) == 0
    assert not (tmp_path / "routines" / _ADOPTED_MARKER).exists()   # retried next boot


def test_adopt_seeds_missing_library_copy_from_repo_seed(make_routine, tmp_path, monkeypatch):
    # An existing library repo predates the permission: the repo seed is copied in (never
    # overwriting) so the library copy exists as the grants authority.
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r5")
    _set_permissions(d, [])
    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)

    assert adopt_permissions(tmp_path / "routines", perms) == 1
    assert (perms / "memory.md").exists()
    assert "memory" in yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["permissions"]


# ------------------------------------------------------------------ seed syncing


def test_sync_seed_utils_installs_missing_never_overwrites(tmp_path, monkeypatch):
    """A util added to util-seed after bootstrap reaches the live catalog at daemon boot;
    an existing (possibly locally-modified) util is never touched."""
    from rsched import bootstrap
    fake_repo = tmp_path / "repo"
    for name in ("newutil", "oldutil"):
        (fake_repo / "util-seed" / "utils" / name).mkdir(parents=True)
        (fake_repo / "util-seed" / "utils" / name / "main.py").write_text(
            f"# seed {name}\n", encoding="utf-8")
    lib = tmp_path / "lib"
    (lib / "utils" / "oldutil").mkdir(parents=True)
    (lib / "utils" / "oldutil" / "main.py").write_text("# locally modified\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "repo_root", lambda: fake_repo)
    assert bootstrap.sync_seed_utils(lib) == 1
    assert (lib / "utils" / "newutil" / "main.py").read_text(encoding="utf-8") == "# seed newutil\n"
    assert (lib / "utils" / "oldutil" / "main.py").read_text(encoding="utf-8") == "# locally modified\n"
    # second boot: nothing new, nothing touched
    assert bootstrap.sync_seed_utils(lib) == 0


def test_sync_seed_utils_no_library_yet(tmp_path, monkeypatch):
    """Before seed_libraries has created utils/, the sync is a silent no-op."""
    from rsched import bootstrap
    fake_repo = tmp_path / "repo"
    (fake_repo / "util-seed" / "utils" / "x").mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "repo_root", lambda: fake_repo)
    assert bootstrap.sync_seed_utils(tmp_path / "nolib") == 0


def test_adopt_library_edits_commits_out_of_band_writes(tmp_path):
    """R332/R335: files written into the live library by a conversation's fs grant (or
    the user's editor) have no committing writer — boot adopts them so they get history.
    """
    from rsched import libgit
    from rsched.bootstrap import adopt_library_edits

    home = tmp_path / "libraries"
    (home / "rules").mkdir(parents=True)
    (home / "rules" / "seeded.md").write_text("# rule: seeded — x\n", encoding="utf-8")
    libgit.init_repo(home, first_commit="seed library repo")
    assert adopt_library_edits(home) is False           # clean repo → nothing to adopt
    (home / "rules" / "loose.md").write_text("# rule: loose — y\n", encoding="utf-8")
    (home / "rules" / "seeded.md").write_text("# rule: seeded — edited\n", encoding="utf-8")
    assert adopt_library_edits(home) is True            # untracked + modified both adopted
    assert libgit.git(home, "status", "--porcelain").stdout.strip() == ""
    assert adopt_library_edits(home) is False           # idempotent on the next boot
    assert adopt_library_edits(tmp_path / "nogit") is False   # no repo → no-op


DIAL_PERM = ("---\ntags: [a, b, c]\nrequires:\n  reminders: local\n  runs: last\n---\n"
             "# permission: reminders — test dial\nbody\n")


def test_adopt_raises_every_dial_the_doc_requires(make_routine, tmp_path, monkeypatch):
    """The adopt cascade used to be a PRIVATE copy of `grants.capabilities_for` that knew four
    keys of nine — `actions`, `utils`, `runs`, `confirm`. A permission whose `requires:` named
    any other dial was adopted with its capability left at the default: the doc in
    `permissions:`, the capability off, and the engine (which enforces from capabilities alone)
    behaving as though the permission had never been adopted. 0.309.0 shipped `reminders` "on
    by default" to zero of 32 live routines that way, with nothing anywhere to say so.
    """
    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)
    (perms / "reminders.md").write_text(DIAL_PERM, encoding="utf-8")
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["reminders"])
    d = make_routine(slug="r1")
    _set_permissions(d, [])
    # an EXPLICIT block, which is what every live routine has — a routine with no block at all
    # follows DEFAULT_CAPABILITIES and adopt deliberately does not materialize one for it
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["capabilities"] = {"actions": [], "utils": [], "confirm": "always", "runs": "none",
                           "workflows": "catalog"}
    (d / "routine.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert adopt_permissions(tmp_path / "routines", perms) == 1
    caps = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["capabilities"]
    assert caps["reminders"] == "local"          # the dial the private copy never knew about
    assert caps["runs"] == "last"                # the one it did — still raised


def test_the_implicit_default_block_carries_the_reminders_dial():
    """The third place the same hole appeared. A routine with no `capabilities:` key means
    DEFAULT_CAPABILITIES, and adopt deliberately does not write a block for one (writing it
    would freeze what is meant to follow the defaults) — so if the default itself omits the
    dial, that routine is off too, with nothing to adopt it later.
    """
    from rsched.config.base import DEFAULT_CAPABILITIES, DEFAULT_PERMISSIONS

    assert DEFAULT_CAPABILITIES["reminders"] == "local"
    # and the floor keeps it only while the backing permission is held, which it is by default
    assert "reminders" in DEFAULT_PERMISSIONS


def test_migration_converges_a_routine_the_old_cascade_left_off(make_routine, tmp_path):
    """MIGRATION(expires=2026-12-01): the one-shot that repairs what the old adopt already
    wrote. The 32 live routines are marked adopted, so the adopt pass will never revisit them.
    """
    from rsched.migrate_reminders_rollout import converge_routines

    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)
    (perms / "reminders.md").write_text(DIAL_PERM, encoding="utf-8")
    d = make_routine(slug="r1")
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["permissions"] = ["reminders"]                 # held...
    raw["capabilities"] = {"actions": [], "utils": [], "util_tags": [], "confirm": "always",
                           "rule_confirm": "always", "runs": "none",
                           "workflows": "catalog"}     # ...and the dial absent, as adopt left it
    (d / "routine.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    notes = converge_routines(tmp_path / "routines", perms)
    assert len(notes) == 1 and "reminders='local'" in notes[0]
    caps = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["capabilities"]
    assert caps["reminders"] == "local" and caps["runs"] == "last"
    # idempotent: it applies the same raise-then-floor the ordinary save runs, so a converged
    # routine comes out unchanged and is not rewritten a second time
    assert converge_routines(tmp_path / "routines", perms) == []


def test_migration_gives_live_templates_the_dials_the_seed_names(tmp_path):
    """The seed sync only ever ADDS files, so fixing `library-seed/templates/*.md` reaches no
    live instance. A template that names every dial but the newest two is what an operator
    reads as "this is what the routine will be".
    """
    from rsched.migrate_reminders_rollout import converge_templates

    lib = tmp_path / "lib"
    (lib / "templates").mkdir(parents=True)
    (lib / "templates" / "basic.md").write_text(
        "---\ntags: [a, b, c]\nconfig:\n  permissions:\n  - memory\n  capabilities:\n"
        "    confirm: always\n    rule_confirm: always\n    runs: last\n---\n"
        "# template: basic — t\nbody\n", encoding="utf-8")
    notes = converge_templates(lib)
    text = (lib / "templates" / "basic.md").read_text(encoding="utf-8")
    assert len(notes) == 1
    assert "\n    remind_confirm: always\n" in text and "\n    reminders: local\n" in text
    assert "\n  - reminders\n" in text
    assert converge_templates(lib) == []                        # idempotent
    # a deliberate value is a DECISION; a migration repairing an omission may not overrule one
    (lib / "templates" / "basic.md").write_text(
        text.replace("    reminders: local", "    reminders: none"), encoding="utf-8")
    assert converge_templates(lib) == []
    assert "reminders: none" in (lib / "templates" / "basic.md").read_text(encoding="utf-8")
