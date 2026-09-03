"""The setup surface — the forward reading of the dependency graph.

Every case here is one the console could not see before: a held permission whose capability is
off, a util whose private store no grant covers, a secret declined forever, a rule that presumes
a write root. The point of the read model is that these stop being discoverable only by a run
failing at 3am, so the tests are written as "what would the operator have been told".
"""
from __future__ import annotations

import pathlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsched.readmodels.surface import (
    BLOCKS,
    BOOT_SEVERITIES,
    INTERRUPTS,
    NOTE,
    routine_surface,
    surface_lines,
)


def _server(tmp_path: Path, machines: dict | None = None) -> SimpleNamespace:
    lib = tmp_path / "lib"
    (lib / "permissions").mkdir(parents=True)
    (lib / "rules").mkdir(parents=True)
    (lib / "utils").mkdir(parents=True)
    routines = tmp_path / "routines"
    routines.mkdir(exist_ok=True)
    return SimpleNamespace(libraries_home=lib, permissions_home=lib / "permissions",
                           rules_home=lib / "rules", machines=machines or {},
                           routines_home=routines)


def _cfg(tmp_path: Path, **over) -> SimpleNamespace:
    # a scheduled, enabled routine by default: the schedule join has something coherent to
    # read, so a "ready" fixture keeps reporting nothing
    base = {"slug": "r", "dir": tmp_path / "r", "permissions": [], "rules": [],
            "capabilities": {"actions": [], "utils": [], "util_tags": []}, "grants": {},
            "fs_read_roots": [], "fs_write_roots": [], "machines": [], "connections": {},
            "inherited": {}, "inherited_from": "", "enabled": True, "cron": "0 7 * * 1",
            "triggers": []}
    base.update(over)
    return SimpleNamespace(**base)


def _util(server, name, *, secrets="(none)", fs="none", calls="(none)", net="none") -> None:
    d = server.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(
        f'"""{name} — t.\n\nusage: gu {name}\ncalls: {calls}\ntags: t\n'
        f'secrets: {secrets}\nnet: {net}\nfs: {fs}\n"""\n', encoding="utf-8")


def _doc(server, kind, slug, body: str) -> None:
    (server.libraries_home / kind / f"{slug}.md").write_text(body, encoding="utf-8")


def _by_id(surface, eid):
    return next((n for n in surface["nodes"] if n["id"] == eid), None)


@pytest.fixture
def empty_store(monkeypatch):
    """No secrets in the store unless a test says otherwise — the surface reads the live one."""
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)


def test_a_ready_routine_reports_nothing(tmp_path):
    server = _server(tmp_path)
    surface = routine_surface(server, _cfg(tmp_path))
    assert surface["verdict"]["ready"] is True
    assert surface_lines(surface) == []


@pytest.mark.usefixtures("empty_store")
def test_a_utils_private_store_with_no_covering_grant_blocks(tmp_path):
    """The voice-model-trainer case: a routine holds a messenger whose session directory — the
    credential itself — no granted root covers, so the util cannot run at all."""
    server = _server(tmp_path)
    _util(server, "signal", fs="roots, rw /srv/signal-sessions")
    cfg = _cfg(tmp_path, capabilities={"utils": ["signal"]})
    surface = routine_surface(server, cfg)
    node = _by_id(surface, "fs-write:/srv/signal-sessions")
    assert node and node["severity"] == BLOCKS
    assert "cannot reach it" in node["effect"]
    assert surface["verdict"]["ready"] is False

    granted = routine_surface(server, _cfg(tmp_path, capabilities={"utils": ["signal"]},
                                           fs_write_roots=["/srv/signal-sessions"]))
    assert _by_id(granted, "fs-write:/srv/signal-sessions")["severity"] == "ok"


@pytest.mark.usefixtures("empty_store")
def test_an_unset_env_var_in_a_declaration_is_not_a_gap(tmp_path, monkeypatch):
    """A messenger declares both `$X_SESSION_DIR` and its literal default; only one resolves,
    so the unresolved one must not be reported as a missing root."""
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    server = _server(tmp_path)
    _util(server, "sig", fs="rw $NOT_SET_ANYWHERE, rw /srv/store")
    surface = routine_surface(server, _cfg(tmp_path, capabilities={"utils": ["sig"]},
                                           fs_write_roots=["/srv/store"]))
    assert not [n for n in surface["nodes"] if "$" in n["id"]]
    assert surface["verdict"]["ready"] is True


@pytest.mark.parametrize(("grant", "severity", "needle"), [
    (None, INTERRUPTS, "stops the run to ask"),
    (False, BLOCKS, "declined forever"),
    (True, "ok", ""),
])
def test_a_declared_secret_reports_what_its_state_will_cost(tmp_path, monkeypatch, grant,
                                                            severity, needle):
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"FOO_TOKEN": "x"})
    server = _server(tmp_path)
    _util(server, "poster", secrets="FOO_TOKEN")
    grants = {} if grant is None else {"secret:FOO_TOKEN": grant}
    surface = routine_surface(server, _cfg(tmp_path, capabilities={"utils": ["poster"]},
                                           grants=grants))
    node = _by_id(surface, "secret:FOO_TOKEN")
    assert node["severity"] == severity
    assert needle in node["effect"]


@pytest.mark.usefixtures("empty_store")
def test_a_secret_absent_from_the_store_blocks(tmp_path):
    server = _server(tmp_path)
    _util(server, "poster", secrets="MISSING_TOKEN")
    surface = routine_surface(server, _cfg(tmp_path, capabilities={"utils": ["poster"]}))
    assert _by_id(surface, "secret:MISSING_TOKEN")["severity"] == BLOCKS


@pytest.mark.usefixtures("empty_store")
def test_optional_and_engine_injected_secrets_are_not_reported(tmp_path):
    """A `?`-marked secret is withheld silently by design, and the machine vars come from a
    BINDING rather than the store — reporting either as a missing credential would be noise
    that trains the operator to ignore the panel."""
    server = _server(tmp_path)
    _util(server, "tg", secrets="TELEGRAM_2FA_PASSWORD?")
    _util(server, "remote", secrets="RSCHED_MACHINES, RSCHED_MACHINE_KEYS")
    surface = routine_surface(server, _cfg(tmp_path,
                                           capabilities={"utils": ["tg", "remote"]}))
    assert [n for n in surface["nodes"] if n["id"].startswith("secret:")] == []


@pytest.mark.usefixtures("empty_store")
def test_secrets_resolve_transitively_over_calls(tmp_path):
    server = _server(tmp_path)
    _util(server, "leaf", secrets="LEAF_TOKEN")
    _util(server, "top", calls="leaf")
    surface = routine_surface(server, _cfg(tmp_path, capabilities={"utils": ["top"]}))
    node = _by_id(surface, "secret:LEAF_TOKEN")
    # the HELD util is named, not the declarer: it is the one the operator granted
    assert node and node["why"] == "needed by top"


@pytest.mark.usefixtures("empty_store")
def test_a_held_doc_whose_capability_is_off_fails_closed(tmp_path):
    """Enforcement reads capabilities ONLY, so a doc held without its capability is not a
    cosmetic inconsistency — every call it teaches is rejected."""
    server = _server(tmp_path)
    _util(server, "discord")
    _doc(server, "permissions", "messaging-discord",
         "---\ntags: [a]\nrequires:\n  utils: [discord]\n---\n# permission: x — y\n")
    surface = routine_surface(server, _cfg(tmp_path, permissions=["messaging-discord"]))
    node = _by_id(surface, "permission:messaging-discord")
    assert node and node["severity"] == BLOCKS and "util:discord" in node["effect"]


@pytest.mark.usefixtures("empty_store")
def test_a_tag_gate_satisfies_a_docs_named_requirement(tmp_path):
    """`util_tags` covers a whole class, including utils the library gains later — a doc naming
    one of them is satisfied, and reporting it as missing would be a false alarm."""
    server = _server(tmp_path)
    d = server.libraries_home / "utils" / "mailer"
    d.mkdir(parents=True)
    (d / "main.py").write_text('"""mailer — t.\n\nusage: gu mailer\ncalls: (none)\n'
                               'tags: smtp\nsecrets: (none)\nnet: none\nfs: none\n"""\n',
                               encoding="utf-8")
    _doc(server, "permissions", "outbound-mail",
         "---\ntags: [a]\nrequires:\n  utils: [mailer]\n---\n# permission: x — y\n")
    surface = routine_surface(server, _cfg(tmp_path, permissions=["outbound-mail"],
                                           capabilities={"util_tags": ["smtp"]}))
    assert _by_id(surface, "permission:outbound-mail") is None


@pytest.mark.usefixtures("empty_store")
def test_expects_is_advisory_and_never_blocks(tmp_path):
    """The soft edge stays soft. A rule presuming a write root the routine does not have is
    worth saying; it is not a failure, or `expects:` would be a worse `requires:`."""
    server = _server(tmp_path)
    _doc(server, "rules", "status-page",
         "---\ntags: [a, b, c]\nexpects:\n  fs-write: ['*']\n---\n# rule: status page — y\n")
    surface = routine_surface(server, _cfg(tmp_path, rules=["status-page"]))
    node = _by_id(surface, "fs-write:*")
    assert node and node["severity"] == INTERRUPTS
    assert surface["verdict"]["blocks"] == 0


@pytest.mark.usefixtures("empty_store")
def test_a_permission_expecting_a_machine_reports_the_unbound_case(tmp_path):
    server = _server(tmp_path)
    _util(server, "remote")
    _doc(server, "permissions", "remote-machines",
         "---\ntags: [a]\nrequires:\n  utils: [remote]\nexpects:\n  machine: ['*']\n---\n"
         "# permission: x — y\n")
    cfg = _cfg(tmp_path, permissions=["remote-machines"], capabilities={"utils": ["remote"]})
    node = _by_id(routine_surface(server, cfg), "machine:*")
    assert node["severity"] == INTERRUPTS and "no machine is bound" in node["effect"]
    cfg.machines = ["gpu-box"]
    assert _by_id(routine_surface(server, cfg), "machine:*")["severity"] == "ok"


@pytest.mark.usefixtures("empty_store")
def test_recipe_authoring_is_a_note_not_a_problem(tmp_path):
    """A routine that may rewrite its own instructions is never WRONG — it is an improver's
    whole job — but it is the one capability whose effect is the routine itself, so it is
    always said out loud.

    It is also no longer implied by a write root over the routine's own dir: that coupling
    meant granting write access to a working directory silently granted the right to reword
    the task. The note names the switch because there now is one."""
    server = _server(tmp_path)
    own = tmp_path / "r"
    plain = routine_surface(server, _cfg(tmp_path, fs_write_roots=[str(own)]))
    assert _by_id(plain, "action:write_recipe") is None      # a root implies nothing now

    surface = routine_surface(server, _cfg(tmp_path,
                                           capabilities={"actions": ["write_recipe"]}))
    node = _by_id(surface, "action:write_recipe")
    assert node["severity"] == NOTE and "own instructions" in node["why"]
    assert surface["verdict"]["ready"] is True


@pytest.mark.usefixtures("empty_store")
def test_surface_lines_renders_only_unmet_rows_worst_first(tmp_path):
    server = _server(tmp_path)
    _util(server, "poster", secrets="GONE_TOKEN")
    _doc(server, "rules", "status-page",
         "---\ntags: [a, b, c]\nexpects:\n  fs-write: ['*']\n---\n# rule: status page — y\n")
    lines = surface_lines(routine_surface(server, _cfg(
        tmp_path, rules=["status-page"], capabilities={"utils": ["poster"]})))
    assert lines[0].startswith("FAIL ") and "GONE_TOKEN" in lines[0]
    assert any(ln.startswith("WARN ") for ln in lines)


# --- the fourth evaluation moment: the run learns its gaps before turn one ----------------

@pytest.mark.usefixtures("empty_store")
def test_boot_files_an_engine_note_naming_the_setup_gaps(tmp_path, monkeypatch):
    """Advisory, and addressed to the RUN rather than to a person: without it a run spends
    turns planning around a capability it does not really have, then fails at turn nine."""
    from types import SimpleNamespace

    from rsched.engine import boot as boot_mod

    server = _server(tmp_path)
    _util(server, "poster", secrets="GONE_TOKEN")
    cfg = _cfg(tmp_path, capabilities={"utils": ["poster"]})
    messages: list[dict] = []
    events: list[tuple] = []
    loop = SimpleNamespace(messages=messages, ctx=SimpleNamespace(
        depth=0, server=server, routine=cfg,
        transcript=SimpleNamespace(event=lambda k, p: events.append((k, p)))))

    boot_mod._setup_gap_note(loop)
    assert len(messages) == 1
    assert "GONE_TOKEN" in messages[0]["content"]
    assert messages[0]["content"].startswith("ENGINE NOTE:")
    assert events and events[0][0] == "user_injection"

    # a ready routine says nothing at all — a note per boot would train the model to skip it
    messages.clear()
    loop.ctx.routine = _cfg(tmp_path)
    boot_mod._setup_gap_note(loop)
    assert messages == []

    # a CHILD inherits its parent's resources, not its config — the note would be wrong there
    loop.ctx.depth = 1
    loop.ctx.routine = cfg
    boot_mod._setup_gap_note(loop)
    assert messages == []


def test_boot_note_never_stops_a_run_when_the_library_is_broken(tmp_path, monkeypatch):
    """A diagnostic that can kill a run is worse than the gap it reports."""
    from types import SimpleNamespace

    from rsched.engine import boot as boot_mod

    loop = SimpleNamespace(messages=[], ctx=SimpleNamespace(
        depth=0, server=SimpleNamespace(), routine=_cfg(tmp_path),
        transcript=SimpleNamespace(event=lambda k, p: None)))
    boot_mod._setup_gap_note(loop)      # server has no libraries_home at all
    assert loop.messages == []


# --- the inverse misconfiguration: a capability no held doc asks for ----------------------

@pytest.mark.usefixtures("empty_store")
def test_a_capability_no_held_doc_requires_is_reported(tmp_path):
    """Three deliberate designs each correctly decline to catch this one: the floor binds a
    routine's OWN mapping at save; a group's block is not floored (a member may hold the doc);
    and enforcement is capabilities-only so prose can never widen anything. So a group can hand
    a member a reserved util with no conduct doc behind it and every layer stays silent."""
    server = _server(tmp_path)
    _util(server, "discord")
    _doc(server, "permissions", "messaging-discord",
         "---\ntags: [a]\nrequires:\n  utils: [discord]\n---\n# permission: x — y\n")
    orphan = _cfg(tmp_path, permissions=[], capabilities={"utils": ["discord"]})
    node = _by_id(routine_surface(server, orphan), "util:discord")
    assert node and node["severity"] == NOTE
    assert "no held conduct doc requires it" in node["why"]
    # nothing is BROKEN — the routine really can call it, which is the point of reporting
    assert routine_surface(server, orphan)["verdict"]["ready"] is True

    covered = _cfg(tmp_path, permissions=["messaging-discord"],
                   capabilities={"utils": ["discord"]})
    assert _by_id(routine_surface(server, covered), "util:discord") is None


@pytest.mark.usefixtures("empty_store")
def test_an_orphan_capability_names_the_group_it_came_from(tmp_path):
    """Provenance is the whole value here: 'you did not set this, your group did' is what
    turns an unexplained capability into a fixable one."""
    server = _server(tmp_path)
    _util(server, "discord")
    cfg = _cfg(tmp_path, capabilities={"utils": ["discord"]})
    cfg.inherited = {"capabilities": "1 from the group"}
    cfg.inherited_from = "Morning Brief"
    node = _by_id(routine_surface(server, cfg), "util:discord")
    assert "Morning Brief" in node["why"]


@pytest.mark.usefixtures("empty_store")
def test_a_gated_action_with_no_covering_doc_is_reported_too(tmp_path):
    server = _server(tmp_path)
    cfg = _cfg(tmp_path, capabilities={"actions": ["write_util"]})
    assert _by_id(routine_surface(server, cfg), "action:write_util")["severity"] == NOTE
    # the library-predates-the-kind fallback still counts: holding the canonical source doc
    # covers the kind even when that doc's requires: never named it
    covered = _cfg(tmp_path, permissions=["util-authoring"],
                   capabilities={"actions": ["write_util"]})
    assert _by_id(routine_surface(server, covered), "action:write_util") is None


@pytest.mark.usefixtures("empty_store")
def test_one_row_per_entity(tmp_path):
    """Two checks can legitimately reach the same id — a capability worth naming in its own
    right that is ALSO uncovered by any held doc. Two rows about one entity reads as a bug."""
    server = _server(tmp_path)
    cfg = _cfg(tmp_path, permissions=[], capabilities={"actions": ["write_recipe"]})
    ids = [n["id"] for n in routine_surface(server, cfg)["nodes"]]
    assert ids.count("action:write_recipe") == 1


# --- the schedule join: does the file say when this routine runs? ---------------------------


def test_a_member_cron_a_group_suppresses_is_reported(tmp_path):
    """D71: a group with a cron suppresses every member's own cron. A member that kept one has
    a routine.yaml naming a time it will never fire at — steward-hub-maintainer recorded 23:00
    while firing at 06:30 in its group's chain — and nothing said the two disagreed."""
    from rsched import groups as groups_mod

    server = _server(tmp_path)
    groups_mod.create(server.routines_home, name="Professional · Daily",
                      members=[{"slug": "r"}], cron="30 6 * * *", tz="Europe/Berlin")
    surface = routine_surface(server, _cfg(tmp_path, cron="0 23 * * *"))
    node = _by_id(surface, "schedule:cron")
    assert node and node["severity"] == NOTE
    assert "'0 23 * * *'" in node["effect"] and "'30 6 * * *'" in node["effect"]
    assert "Professional · Daily" in node["why"]
    # a NOTE never fails the command — nothing is broken, the file is just misleading
    assert surface["verdict"]["ready"] is True
    # and clearing the routine's own cron settles it
    assert _by_id(routine_surface(server, _cfg(tmp_path, cron="")), "schedule:cron") is None


def test_a_routine_nothing_ever_starts_is_reported(tmp_path):
    """The mirror case: no cron of its own, no group with a schedule. A perfectly good
    on-demand design, and indistinguishable from an oversight until it is said out loud."""
    server = _server(tmp_path)
    node = _by_id(routine_surface(server, _cfg(tmp_path, cron="")), "schedule:none")
    assert node and node["severity"] == NOTE
    assert "nothing on a clock starts this routine" in node["effect"]
    # a routine with triggers is started by events — the wording says so instead
    triggered = _by_id(routine_surface(server, _cfg(tmp_path, cron="", triggers=[
        {"id": "t-1", "type": "report", "cooldown_s": 900}])), "schedule:none")
    assert triggered and "a trigger" in triggered["effect"]
    # a DISABLED routine already says it does not run — no second row for the same fact
    assert _by_id(routine_surface(server, _cfg(tmp_path, cron="", enabled=False)),
                  "schedule:none") is None


def _with_phases(server, cfg, phase_json: str | None) -> None:
    """A routine dir whose recipe declares phases, optionally with a state/phase.json."""
    d = pathlib.Path(cfg.dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.md").write_text("## Run flow\ndo it\n\n## Phases\n- steady\n", encoding="utf-8")
    if phase_json is not None:
        (d / "state").mkdir(exist_ok=True)
        (d / "state" / "phase.json").write_text(phase_json, encoding="utf-8")


@pytest.mark.usefixtures("empty_store")
def test_phase_file_keyed_wrong_is_a_note(tmp_path):
    """The composer reads state/phase.json as .get("phase"), and that value scopes a stopping
    condition to a stage. Routines that invented their own key (funscript-trainer wrote
    `lifecycle`, self-audit `state`, routine-improver `{}`) wrote a file that looks right and
    matches nothing — the digest still shows it, so nothing ever complained."""
    server, cfg = _server(tmp_path), _cfg(tmp_path)
    _with_phases(server, cfg, '{"lifecycle": "steady"}')
    node = _by_id(routine_surface(server, cfg), "state:phase")
    assert node and node["severity"] == NOTE and "lifecycle" in node["effect"]


@pytest.mark.usefixtures("empty_store")
def test_phase_file_absent_is_a_note_once_the_routine_has_run(tmp_path):
    server, cfg = _server(tmp_path), _cfg(tmp_path)
    _with_phases(server, cfg, None)
    # no COMPLETED run yet → nothing has had its chance to record one, so nothing to say
    run = pathlib.Path(cfg.dir) / "runs" / "20260101-000000"
    run.mkdir(parents=True)
    assert _by_id(routine_surface(server, cfg), "state:phase") is None
    (run / "result.md").write_text("done", encoding="utf-8")
    node = _by_id(routine_surface(server, cfg), "state:phase")
    assert node and node["severity"] == NOTE


@pytest.mark.usefixtures("empty_store")
def test_phase_file_correct_says_nothing(tmp_path):
    server, cfg = _server(tmp_path), _cfg(tmp_path)
    _with_phases(server, cfg, '{"phase": "steady", "note": "n"}')
    assert _by_id(routine_surface(server, cfg), "state:phase") is None


@pytest.mark.usefixtures("empty_store")
def test_recipe_without_phases_is_not_missing_one(tmp_path):
    """A routine whose recipe declares no phases has nothing to record — silence, not a note."""
    server, cfg = _server(tmp_path), _cfg(tmp_path)
    d = pathlib.Path(cfg.dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.md").write_text("## Run flow\ndo it\n", encoding="utf-8")
    assert _by_id(routine_surface(server, cfg), "state:phase") is None


def test_the_boot_note_carries_only_what_the_run_can_act_on():
    """The engine's boot note explains FAIL and WARN and nothing else, because it exists to
    save a run from discovering a gap at turn nine. A NOTE is addressed to the OPERATOR — a
    cron the group suppresses, a phase file keyed wrong — and putting one in front of every
    run buys prompt noise it cannot act on. `rsched validate` still prints all three."""
    surface = {"nodes": [
        {"id": "a", "severity": BLOCKS, "why": "w", "effect": "e"},
        {"id": "b", "severity": INTERRUPTS, "why": "w", "effect": ""},
        {"id": "c", "severity": NOTE, "why": "w", "effect": ""},
    ]}
    assert len(surface_lines(surface)) == 3                     # validate: everything
    boot = surface_lines(surface, BOOT_SEVERITIES)
    assert [ln.split()[1] for ln in boot] == ["a:", "b:"]       # boot: no NOTE
