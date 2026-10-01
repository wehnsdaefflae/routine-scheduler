"""Settings patterns: the vocabulary, the immutable store, pending changes and the one accept."""
import pytest

from rsched.config import load_routine
from rsched.paths import read_yaml
from rsched.patterns import drafts, fields, store


def test_sets_compare_as_sets_and_triggers_ignore_their_identity():
    assert fields.equal("rules", ["b", "a"], ["a", "b", "a"])
    one = [{"id": "t-1", "type": "report", "cooldown_s": 900, "created": "x"}]
    two = [{"id": "t-9", "type": "report", "cooldown_s": 900, "created": "y"}]
    assert fields.equal("triggers", one, two)
    assert not fields.equal("triggers", one, [])


def test_diff_names_added_and_removed_items():
    out = fields.diff({"rules": ["a", "b"]}, {"rules": ["b", "c"]}, ["rules"])
    assert out["rules"]["added"] == ["c"] and out["rules"]["removed"] == ["a"]


def test_overrides_are_only_governed_fields_that_differ():
    mine = {"rules": ["a"], "keep_runs": 30, "tags": ["x"]}
    pattern = {"rules": ["a"], "keep_runs": 10}
    assert fields.overrides(mine, pattern) == ["keep_runs"]


def test_a_finish_line_compares_on_what_a_person_wrote_not_what_a_run_reported():
    mine = {"until": "", "outcomes": [
        {"id": "g1", "text": "submitted", "judge": "run", "date": "", "status": "open",
         "distance": "two sections left", "distance_run": "r:3", "distance_ts": "t"}]}
    theirs = {"outcomes": [{"text": "submitted", "judge": "run"}]}
    assert fields.equal("finish_line", mine, theirs)
    assert not fields.equal("finish_line", mine, {"outcomes": [{"text": "submitted",
                                                                "judge": "you"}]})


@pytest.mark.parametrize("caps", [
    {"actions": [], "utils": []},                          # leaves every setting out
    {"actions": [], "utils": [], "runs": "none", "confirm": "never"},
    {"actions": [], "utils": [], "runs": "all", "reminders": "global",
     "rule_confirm": "creations", "remind_confirm": "never"}])
def test_the_page_reads_a_setting_the_way_the_run_enforces_it(tmp_path, caps):
    """A capabilities mapping that leaves a setting out holds what the RUN reads for it — the
    approval dials at `always`, the reminder layer off, run history at its `last` floor. The
    page read DEFAULT_CAPABILITIES instead (`confirm: creations`, `reminders: local`), so it
    showed a routine — and compared it with its pattern — as holding what the engine never
    gave it."""
    from rsched.policyload import load_policy

    shown = fields.effective_capabilities(caps)
    policy = load_policy(tmp_path / "permissions", [], caps)
    assert (shown["confirm"], shown["rule_confirm"], shown["remind_confirm"],
            shown["reminders"], shown["runs"]) == (
        policy.confirm, policy.rule_confirm, policy.remind_confirm, policy.reminders,
        policy.run_history)


def test_snapshot_reads_every_field(make_routine):
    cfg, _ = load_routine(make_routine())
    snap = fields.snapshot(cfg)
    assert set(snap) == set(fields.BY_KEY)
    assert snap["schedule"]["friendly"]["frequency"] == "weekly"


def pattern_doc(**settings):
    return {"title": "Watcher", "summary": "watches things", "workflow": "watch-and-digest",
            "settings": settings or {"rules": ["ask-policy"], "keep_runs": 20}}


def test_patterns_are_created_never_overwritten(tmp_path):
    lib = tmp_path / "lib"
    stored = store.create(lib, "watcher", pattern_doc())
    assert stored["settings"]["keep_runs"] == 20 and stored["problems"] == []
    with pytest.raises(FileExistsError):
        store.create(lib, "watcher", pattern_doc())
    assert [p["slug"] for p in store.list_all(lib)] == ["watcher"]
    assert store.delete(lib, "watcher") and store.read(lib, "watcher") is None


@pytest.mark.parametrize("doc", [
    {"title": "", "summary": "s", "workflow": "w", "settings": {"keep_runs": 3}},
    {"title": "t", "summary": "s", "workflow": "w", "settings": {}},
    {"title": "t", "summary": "s", "workflow": "w", "settings": {"name": "mine"}},
    {"title": "t", "summary": "s", "workflow": "w", "settings": {"nope": 1}},
    {"title": "t", "summary": "s", "workflow": "w", "settings": {"keep_runs": 3},
     "asks": [{"field": "nope", "question": "?"}]}])
def test_an_unsound_pattern_is_refused(tmp_path, doc):
    with pytest.raises(ValueError):
        store.create(tmp_path / "lib", "p", doc)


def test_a_pattern_file_that_is_no_pattern_is_named_by_lint_and_breaks_no_listing(tmp_path):
    """One hand-broken file (a merge conflict, a typo) used to raise out of every listing — each
    routine's settings page, the Library tab, creation's catalog, `rsched lint` itself — while
    a file that parsed to a list was silently left out of all of them, lint included."""
    from rsched.workflows.lint import lint_patterns

    lib = tmp_path / "lib"
    store.create(lib, "watcher", pattern_doc())
    (store.home(lib) / "broken.yaml").write_text("title: [unclosed\n", encoding="utf-8")
    (store.home(lib) / "listy.yaml").write_text("- a\n- b\n", encoding="utf-8")
    (store.home(lib) / "Bad Name.yaml").write_text("title: t\n", encoding="utf-8")
    assert [p["slug"] for p in store.list_all(lib)] == ["watcher"]
    assert store.read(lib, "broken") is None
    found = lint_patterns(lib, ["ask-policy"], [])
    assert "invalid YAML" in found["patterns/broken.yaml"][0]
    assert "a pattern is a mapping" in found["patterns/listy.yaml"][0]
    assert "kebab-case" in found["patterns/Bad Name.yaml"][0]


def test_a_pattern_never_grants_a_credential_store(tmp_path):
    """Creation copies a pattern's folder grants into the new routine past the PATCH guard that
    refuses one by hand — the seed instance-auditor pattern once handed the console's config dir
    to every routine made from it. A pattern naming a store is a problem the lint reports, a
    "Save as new pattern" refuses, and creation writes without that root."""
    from rsched.patterns import apply
    from rsched.workflows.lint import lint_patterns

    lib = tmp_path / "lib"
    risky = {"fs_read_roots": ["~/.ssh", "~/notes"], "fs_write_roots": ["~/.credentials/x"],
             "keep_runs": 20}
    with pytest.raises(ValueError, match="credential store"):
        store.create(lib, "auditor", pattern_doc(**risky))
    store.home(lib).mkdir(parents=True)        # a library copy that predates the check
    (store.home(lib) / "auditor.yaml").write_text(
        "title: Auditor\nsummary: s\nworkflow: w\nsettings:\n  fs_read_roots: [~/.ssh, ~/notes]\n"
        "  fs_write_roots: [~/.credentials/x]\n", encoding="utf-8")
    found = lint_patterns(lib, [], [])["patterns/auditor.yaml"]
    assert [f for f in found if "credential store" in f] == [
        ("patterns/auditor.yaml: settings.fs_read_roots: ~/.ssh is a credential store — a "
         "pattern never grants one"),
        ("patterns/auditor.yaml: settings.fs_write_roots: ~/.credentials/x is a credential "
         "store — a pattern never grants one")]
    out = apply.routine_yaml(store.read(lib, "auditor")["settings"], tz="UTC")
    assert out["fs_read_roots"] == ["~/notes"] and out["fs_write_roots"] == []


def test_a_routine_yaml_that_does_not_parse_follows_nothing(tmp_path):
    home = tmp_path / "routines"
    for slug, text in (("good", "pattern: watcher\n"), ("bad", "pattern: [watcher\n")):
        (home / slug).mkdir(parents=True)
        (home / slug / "routine.yaml").write_text(text, encoding="utf-8")
    assert store.followers(home, "watcher") == ["good"]


def test_creation_proposes_a_patterns_access_decisions_and_writes_none(tmp_path):
    """`grants` is ask-first: a pattern deciding which secrets a routine receives would be a
    pattern granting them. So the new routine is saved WITHOUT the pattern's grants, and they
    wait as a pending change for the person's click."""
    import shutil
    from pathlib import Path

    from rsched.config import ServerConfig
    from rsched.workflows.scaffold import scaffold

    seed = Path(__file__).resolve().parents[1] / "library-seed"
    server = ServerConfig()
    server.libraries_home = tmp_path / "library"
    for kind in ("workflows", "rules", "permissions"):
        shutil.copytree(seed / kind, server.libraries_home / kind,
                        ignore=shutil.ignore_patterns("__pycache__"))
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    store.create(server.libraries_home, "granting", {
        **pattern_doc(rules=["ask-policy"], grants={"secret:FOO_KEY": True}),
        "workflow": "general-task"})
    d = scaffold(server, slug="newbie", name="Newbie", instruction="Do the thing.",
                 workflow_slug="general-task", pattern="granting")
    raw = read_yaml(d / "routine.yaml")
    assert raw["pattern"] == "granting" and not raw.get("grants")
    assert raw["rules"] == ["ask-policy"]                    # the rest of the pattern is saved
    draft = drafts.read(server.routines_home, "newbie")
    assert draft is not None and draft["changes"]["grants"]["value"] == {"secret:FOO_KEY": True}


def test_drafts_prune_what_already_landed(tmp_path):
    home = tmp_path / "routines"
    drafts.write(home, "r", changes={"keep_runs": {"value": 10, "reason": "less history"},
                                     "rules": {"value": ["a"], "reason": "x"}},
                 pattern="watcher", message="check", source="test")
    left = drafts.prune(home, "r", {"keep_runs": 10, "rules": []}, assigned="")
    assert set(left["changes"]) == {"rules"}
    assert drafts.prune(home, "r", {"keep_runs": 10, "rules": ["a"]}, assigned="watcher") is None
    assert drafts.read(home, "r") is None


# ------------------------------------------------------------------ the web surface


@pytest.fixture
def client(api_client, make_routine):
    c, tmp = api_client
    make_routine("alpha")
    return c, tmp


def test_following_a_pattern_is_proposed_not_written(client):
    c, tmp = client
    store.create(tmp / "library", "watcher", pattern_doc(keep_runs=12))
    before = (tmp / "routines/alpha/routine.yaml").read_text()
    out = c.post("/api/routines/alpha/settings/follow", json={"pattern": "watcher"}).json()
    assert out["draft"]["pattern"] == "watcher"
    assert out["draft"]["changes"]["keep_runs"]["value"] == 12
    assert (tmp / "routines/alpha/routine.yaml").read_text() == before


def test_one_accept_applies_every_kept_change_and_clears_the_draft(client):
    c, tmp = client
    store.create(tmp / "library", "watcher", pattern_doc(keep_runs=12))
    c.post("/api/routines/alpha/settings/follow", json={"pattern": "watcher"})
    r = c.post("/api/routines/alpha/settings", json={
        "changes": {"keep_runs": 12, "improve": False,
                    "triggers": [{"type": "report", "cooldown_s": 900,
                                  "max_fires_per_day": 24}],
                    "finish_line": {"outcomes": [{"text": "digest sent", "judge": "run"}],
                                    "until": "2027-01-01"}},
        "pattern": "watcher"})
    assert r.status_code == 200, r.text
    body = r.json()
    raw = read_yaml(tmp / "routines/alpha/routine.yaml")
    assert raw["retention"]["keep_runs"] == 12 and raw["improve"] is False
    assert raw["pattern"] == "watcher"
    assert [t["type"] for t in raw["triggers"]] == ["report"]
    assert body["draft"] is None and body["overrides"] == []
    assert body["fields"]["finish_line"]["outcomes"][0]["text"] == "digest sent"
    assert body["fields"]["finish_line"]["until"] == "2027-01-01"


def test_accepting_keeps_an_existing_triggers_identity(client):
    """The page sends back the rows it was given — validated, defaults spelled out — while the
    file leaves them out; the webhook a third party holds must keep its URL either way."""
    c, tmp = client
    c.post("/api/routines/alpha/settings", json={"changes": {"triggers": [
        {"type": "webhook", "cooldown_s": 60, "max_fires_per_day": 0}]}})
    token = read_yaml(tmp / "routines/alpha/routine.yaml")["triggers"][0]["token"]
    shown = c.get("/api/routines/alpha/settings").json()["fields"]["triggers"]
    r = c.post("/api/routines/alpha/settings", json={"changes": {"triggers": [
        *shown, {"type": "report", "cooldown_s": 900, "max_fires_per_day": 24}]}})
    assert r.status_code == 200, r.text
    rows = read_yaml(tmp / "routines/alpha/routine.yaml")["triggers"]
    assert {t["type"] for t in rows} == {"webhook", "report"}
    assert next(t for t in rows if t["type"] == "webhook")["token"] == token


@pytest.mark.parametrize("refused", [
    {"finish_line": {"outcomes": [{"text": "launched", "judge": "date"}]}},   # no date
    {"triggers": [{"type": "imap", "cooldown_s": 60, "max_fires_per_day": 0}]},
])
def test_an_accept_one_owner_refuses_lands_nothing(client, refused):
    """The accept routes each field to the writer that owns it, and the triggers list and the
    finish line were checked only AFTER routine.yaml had been rewritten and committed: the
    page got a 400 for an accept that had half landed, its draft still showing every change
    as pending. Everything is checked before anything is written."""
    c, tmp = client
    path = tmp / "routines/alpha/routine.yaml"
    before = path.read_text()
    r = c.post("/api/routines/alpha/settings", json={"changes": {"keep_runs": 12, **refused}})
    assert r.status_code in (400, 422), r.text
    assert path.read_text() == before
    assert not (tmp / "routines/alpha/state/finish-line.json").exists()


def test_a_trigger_row_compares_equal_with_or_without_its_defaults():
    bare = [{"id": "t-1", "type": "webhook", "token": "x", "cooldown_s": 60}]
    spelled = [{"type": "webhook", "cooldown_s": 60, "max_fires_per_day": 0}]
    assert fields.equal("triggers", bare, spelled)
    assert not fields.equal("triggers", bare, [{**spelled[0], "max_fires_per_day": 5}])


def test_an_unknown_setting_is_refused(client):
    c, _ = client
    assert c.post("/api/routines/alpha/settings",
                  json={"changes": {"colour": "red"}}).status_code == 422


def test_save_as_new_pattern_then_the_button_goes_quiet(client):
    c, tmp = client
    first = c.get("/api/routines/alpha/settings").json()
    assert first["save_as_enabled"] is True and first["pattern"] is None
    out = c.post("/api/routines/alpha/patterns",
                 json={"title": "Alpha kind", "summary": "what alpha is"}).json()
    assert out["pattern"]["slug"] == "alpha-kind"
    assert out["save_as_enabled"] is False and out["identical"] == ["alpha-kind"]
    assert read_yaml(tmp / "routines/alpha/routine.yaml")["pattern"] == "alpha-kind"
    again = c.post("/api/routines/alpha/patterns", json={"title": "Copy", "summary": "same"})
    assert again.status_code == 409


def test_deleting_a_pattern_releases_its_followers_and_keeps_their_values(client):
    c, tmp = client
    c.post("/api/routines/alpha/patterns", json={"title": "Alpha kind", "summary": "s"})
    before = read_yaml(tmp / "routines/alpha/routine.yaml")
    out = c.delete("/api/patterns/alpha-kind").json()
    assert out["released"] == ["alpha"]
    after = read_yaml(tmp / "routines/alpha/routine.yaml")
    assert "pattern" not in after
    assert {k: v for k, v in before.items() if k != "pattern"} == after
    assert c.get("/api/patterns").json()["patterns"] == []


def test_a_broken_pattern_file_leaves_every_settings_surface_up(client):
    c, tmp = client
    store.create(tmp / "library", "watcher", pattern_doc())
    (store.home(tmp / "library") / "broken.yaml").write_text("summary: [x\n", encoding="utf-8")
    listed = c.get("/api/patterns")
    assert listed.status_code == 200
    assert [p["slug"] for p in listed.json()["patterns"]] == ["watcher"]
    assert c.get("/api/routines/alpha/settings").status_code == 200


def test_the_pattern_list_carries_the_field_vocabulary_the_library_renders(client):
    c, _ = client
    meta = c.get("/api/patterns").json()["meta"]
    assert [f["key"] for f in meta] == [f.key for f in fields.FIELDS]
    assert next(f for f in meta if f["key"] == "fs_write_roots")["label"] == "Read-write roots"


# --- the shipped patterns --------------------------------------------------------------------

SEED = __import__("pathlib").Path(__file__).resolve().parents[1] / "library-seed"


def _seed_patterns() -> list[dict]:
    return store.list_all(SEED)


def test_every_shipped_pattern_is_sound_and_governs_every_field():
    """A pattern is a whole settings document: a field it leaves out is a field nobody can
    see a routine depart from. `name` and `description` are the routine's own."""
    shipped = _seed_patterns()
    assert len(shipped) == 14
    for p in shipped:
        assert p["problems"] == [], (p["slug"], p["problems"])
        assert set(p["settings"]) == set(fields.GOVERNABLE), p["slug"]


def test_every_shipped_pattern_names_only_what_the_library_ships():
    from rsched import library_docs
    from rsched.workflows.library import list_workflows

    rules = set(library_docs.slugs(SEED / "rules"))
    permissions = set(library_docs.slugs(SEED / "permissions"))
    workflows = {w["slug"] for w in list_workflows(SEED)}
    for p in _seed_patterns():
        s = p["settings"]
        assert set(s["rules"]) <= rules, (p["slug"], set(s["rules"]) - rules)
        assert set(s["permissions"]) <= permissions, (p["slug"],
                                                      set(s["permissions"]) - permissions)
        assert p["workflow"] in workflows, (p["slug"], p["workflow"])
        for ask in p["asks"]:
            assert ask["field"] in fields.GOVERNABLE, (p["slug"], ask)


def test_a_shipped_patterns_capabilities_survive_its_own_permissions():
    """Accepting a pattern writes its permissions AND its capabilities; if the save-time floor
    took a capability away, the routine would read as departing from its pattern the moment it
    followed it."""
    from rsched.grants import floor_capabilities, read_library_requires

    lib = read_library_requires(SEED / "permissions")
    for p in _seed_patterns():
        s = p["settings"]
        floored = floor_capabilities(s["permissions"], lib, dict(s["capabilities"]))
        assert fields.equal("capabilities", floored, s["capabilities"]), p["slug"]


def test_a_shipped_pattern_carries_every_budget():
    from rsched.config.base import DEFAULT_BUDGETS

    for p in _seed_patterns():
        assert set(p["settings"]["budgets"]) == set(DEFAULT_BUDGETS), p["slug"]


def test_a_proposal_the_accept_button_would_refuse_is_not_offered():
    """recommend.py's own contract: "a proposal the accept button would refuse is not a
    proposal". The accept route validates `connections` / `machines` / `models` SEMANTICALLY
    (web/config_fields.py) after the patch model has checked their shape, so a value that is
    shaped right and meaningless — the operator's live case, `connections: {"fau-mail":
    {"scopes": [...]}}` on ewsan-bohrdaten-steward, where `fau-mail` is a util and not an OAuth
    provider — must be dropped here rather than offered. One refused field 400s the WHOLE
    accept, so offering it costs the person every other change in the draft.
    """
    from types import SimpleNamespace

    from rsched.patterns.recommend import valid

    # connections is the OAuth-provider map: an unknown provider, and a non-string account.
    assert not valid("connections", {"fau-mail": {"scopes": ["read", "draft", "send"]}})
    assert not valid("connections", {"fau-mail": "mark"})
    assert not valid("connections", {"google": {"scopes": ["read"]}})
    assert valid("connections", {"google": "mark@gmail.com"})
    assert valid("connections", {})

    # `machines` and `models` are catalog-checked, and the catalog lives on the server: with
    # one, an off-catalog name is refused; without one the semantic half is SKIPPED rather
    # than guessed, because a missing catalog is not evidence the value is wrong.
    srv = SimpleNamespace(machines={"real-box": {}}, models={"real-model": {}})
    assert not valid("machines", ["no-such-box"], srv)
    assert valid("machines", ["real-box"], srv)
    assert valid("machines", ["no-such-box"])
    assert not valid("models", {"main": "no-such-model"}, srv)


def test_the_recommender_is_told_what_each_gate_parameter_means(tmp_path):
    """The prompt spells the SCHEDULE's weekday numbering (0 = Sunday), while a gate's
    `weekdays` check numbers from Monday. Listing the checks' parameter NAMES only left the
    model one convention to copy: a duty proposed for Monday landed on Tuesday, and the gate
    answered "no standing duty is due today" on the real day."""
    from types import SimpleNamespace

    from rsched.patterns.recommend import _prompt

    server = SimpleNamespace(permissions_home=tmp_path / "p", rules_home=tmp_path / "r")
    text = _prompt(server, "r", {}, None, "a weekly duty", ["run_gate", "schedule"])
    assert "0 = Monday" in text and "0=Sunday" in text
    assert "json:<dotted.path>" in text                        # a select's syntax, not its name
    assert "max_quiet: " in text and "days (int, required)" in text


def test_a_refused_connection_names_the_field_and_the_way_out():
    """The accept is whole-draft, so this 400 refuses every OTHER change the person kept. A
    message naming only the provider (the operator's 2026-09-30 report: "i get 'unknown
    connection provider 'fau-mail''") leaves them guessing which of nine proposed blocks to
    revert, so it must name the field, the known providers and the way forward.
    """
    from fastapi import HTTPException

    from rsched.web.config_fields import validate_connections

    with pytest.raises(HTTPException) as exc:
        validate_connections({"fau-mail": {"scopes": ["read"]}})
    detail = str(exc.value.detail)
    assert detail.startswith("connections:")
    assert "fau-mail" in detail and "revert" in detail

    with pytest.raises(HTTPException) as exc:
        validate_connections({"google": {"scopes": ["read"]}})
    assert "account label" in str(exc.value.detail)
