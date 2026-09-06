"""The setup surface — the forward reading of the dependency graph.

Every case here is one the console could not see before: a held permission whose capability is
off, a util whose private store no grant covers, a secret declined forever, a rule that presumes
a write root. The point of the read model is that these stop being discoverable only by a run
failing at 3am, so the tests are written as "what would the operator have been told".

Two modules answer that: `surface.py` builds the nodes, `remedies.py` says one node's `fix` in
words for the two callers with no panel to click. The bindings at the foot of this file cross
that seam on purpose — a kind emitted with no words renders a gap with no remedy on the CLI.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
from pathlib import Path
from string import Formatter
from types import SimpleNamespace

import pytest

from rsched.readmodels import remedies as remedies_mod
from rsched.readmodels import surface as surface_mod
from rsched.readmodels.remedies import REMEDIES, surface_lines
from rsched.readmodels.surface import (
    BLOCKS,
    BOOT_SEVERITIES,
    INTERRUPTS,
    NOTE,
    routine_surface,
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
            "domain": "", "inherited": {}, "inherited_from": "", "enabled": True,
            "cron": "0 7 * * 1", "triggers": []}
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
    """A `?`-marked secret is withheld silently by design; the machine vars come from a
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
    one of them is satisfied; reporting it as missing would be a false alarm."""
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
    """Advisory — and addressed to the RUN rather than to a person: without it a run spends
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
    routine's OWN mapping at save; a domain's block is not floored (a member may hold the doc);
    and enforcement is capabilities-only so prose can never widen anything. So a domain can hand
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
    # the routine's own mapping holds it, so its own page is where it is dropped
    assert node["fix"] == {"kind": "cover_or_drop", "entity": "util:discord",
                           "owner": "routine"}

    covered = _cfg(tmp_path, permissions=["messaging-discord"],
                   capabilities={"utils": ["discord"]})
    assert _by_id(routine_surface(server, covered), "util:discord") is None


def _domain(server, name: str, **caps) -> str:
    """A domain whose shared config hands its members `caps`. Returns its id."""
    from rsched import domains

    return domains.create(server.routines_home, name=name,
                          config={"capabilities": caps})["id"]


@pytest.mark.usefixtures("empty_store")
def test_an_orphan_capability_the_domain_supplies_is_dropped_at_the_domain(tmp_path):
    """Provenance is most of the value of this row in prose — 'you did not set this, your
    domain did' — and all of it in the payload. A routine's own save FLOORS its mapping, which
    is what makes a drop on its own page work at all. A domain's block is deliberately not
    floored: the member's list UNIONS with it at every load, so the same drop applied to a
    domain-supplied util is undone before the next run reads it. The fix says where the act
    belongs, because an offer landing on a control that cannot perform it is worse than none."""
    server = _server(tmp_path)
    _util(server, "discord")
    domain_id = _domain(server, "Morning Brief", utils=["discord"])
    cfg = _cfg(tmp_path, capabilities={"utils": ["discord"]}, domain=domain_id,
               inherited={"capabilities": "1 from the domain"}, inherited_from="Morning Brief")
    node = _by_id(routine_surface(server, cfg), "util:discord")
    assert "Morning Brief" in node["why"]
    # the NAME goes where both renderings put it — in a sentence; the id is provenance
    assert node["fix"] == {"kind": "cover_or_drop", "entity": "util:discord",
                           "owner": "domain", "domain": "Morning Brief"}
    assert node["source"] == {"domain": domain_id}


@pytest.mark.usefixtures("empty_store")
def test_a_capability_the_member_lists_too_still_belongs_to_the_domain(tmp_path):
    """The reading `cfg.inherited` cannot give, in the direction that hurts. That mapping
    counts what the merge CONTRIBUTED; a member naming the same util itself contributes
    nothing — so the row would read as the routine's own while the union quietly restores the
    domain's copy after every drop. One fact settles the site: does the domain's block name it."""
    server = _server(tmp_path)
    _util(server, "discord")
    domain_id = _domain(server, "Morning Brief", utils=["discord"])
    cfg = _cfg(tmp_path, capabilities={"utils": ["discord"]}, domain=domain_id)
    assert cfg.inherited == {}          # what the loader records here: the union added nothing
    assert _by_id(routine_surface(server, cfg), "util:discord")["fix"]["owner"] == "domain"

    # ...and a domain supplying some OTHER capability leaves this one the routine's own
    other = _cfg(tmp_path, capabilities={"utils": ["discord"]},
                 domain=_domain(server, "Evening", actions=["write_util"]),
                 inherited={"capabilities": "1 from the domain"}, inherited_from="Evening")
    node = _by_id(routine_surface(server, other), "util:discord")
    assert node["fix"]["owner"] == "routine" and "Evening" not in node["why"]


@pytest.mark.usefixtures("empty_store")
def test_the_words_for_a_drop_say_where_it_can_be_performed(tmp_path):
    """The terminal reader is handed the same distinction the console routes on. "Switch it
    off" addressed to somebody who cannot is the broken link written out in prose."""
    server = _server(tmp_path)
    _util(server, "discord")
    own = surface_lines(routine_surface(server, _cfg(tmp_path,
                                                     capabilities={"utils": ["discord"]})))
    assert ("fix: hold a conduct doc that requires it, or drop it from this routine's "
            "capabilities") in own[0]

    domain_id = _domain(server, "Morning Brief", utils=["discord"])
    shared = surface_lines(routine_surface(server, _cfg(
        tmp_path, capabilities={"utils": ["discord"]}, domain=domain_id)))
    assert "drop it from the Morning Brief domain that supplies it" in shared[0]


@pytest.mark.usefixtures("empty_store")
def test_a_missing_util_leads_with_the_half_that_can_be_performed(tmp_path):
    """A util is authored by a RUN through write_util. The Library page offers "+ new" for
    rules, permissions and templates and none for utils, so "write the ghost util" sent its
    reader to a page with no control for it. The drop is the half a person performs — on the
    routine's own page — so the remedy leads with that one."""
    server = _server(tmp_path)
    line = surface_lines(routine_surface(server, _cfg(tmp_path,
                                                      capabilities={"utils": ["ghost"]})))[0]
    assert "fix: drop ghost from this routine's capabilities" in line
    assert "only a run writes a util" in line


@pytest.mark.usefixtures("empty_store")
def test_an_absent_util_the_domain_supplies_is_dropped_at_the_domain(tmp_path):
    """The provenance every other capability row carries, on the one row that had none. Without
    it the offer lands on this routine's own drop control, whose save the domain's block unions
    straight back at the next load — an act that succeeds while changing nothing, which is the
    one outcome worse than no offer at all."""
    server = _server(tmp_path)
    domain_id = _domain(server, "Morning Brief", utils=["ghost"])
    cfg = _cfg(tmp_path, capabilities={"utils": ["ghost"]}, domain=domain_id)
    surface = routine_surface(server, cfg)
    node = _by_id(surface, "util:ghost")
    assert node["fix"] == {"kind": "install_util", "name": "ghost",
                           "owner": "domain", "domain": "Morning Brief"}
    assert node["source"] == {"domain": domain_id}
    assert "Morning Brief" in node["why"]
    assert ("fix: drop ghost from the Morning Brief domain that supplies it"
            in surface_lines(surface)[0])


@pytest.mark.usefixtures("empty_store")
def test_an_absent_util_a_held_doc_requires_names_the_doc_rather_than_a_drop(tmp_path):
    """The absent util NO drop settles. A save raises the mapping to cover every held doc before
    it floors it, so dropping a util `messaging-discord` requires is undone by the same save
    that performs it — the orphan card leaves the row out for exactly that reason, which left
    the offer aiming at a panel where nothing could be done about it. The performable act is to
    stop holding the doc, so the fix names the doc and carries no drop site at all."""
    server = _server(tmp_path)
    _doc(server, "permissions", "messaging-discord",
         "---\ntags: [a]\nrequires:\n  utils: [ghost]\n---\n# permission: x — y\n")
    cfg = _cfg(tmp_path, permissions=["messaging-discord"], capabilities={"utils": ["ghost"]})
    surface = routine_surface(server, cfg)
    node = _by_id(surface, "util:ghost")
    assert node["severity"] == BLOCKS
    assert node["fix"] == {"kind": "install_util", "name": "ghost", "doc": "messaging-discord"}
    assert node["source"] == {"doc": "messaging-discord"}
    # ...and the terminal reader is handed that same act rather than "drop it"
    line = surface_lines(surface)[0]
    assert "fix: stop holding messaging-discord" in line
    assert "drop ghost" not in line

    # the doc beats every drop site, because no site can outlive the raise the doc performs
    inherited = _cfg(tmp_path, permissions=["messaging-discord"],
                     capabilities={"utils": ["ghost"]},
                     domain=_domain(server, "Morning Brief", utils=["ghost"]))
    assert _by_id(routine_surface(server, inherited), "util:ghost")["fix"] == {
        "kind": "install_util", "name": "ghost", "doc": "messaging-discord"}


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
    surface = routine_surface(server, cfg)
    assert [n["id"] for n in surface["nodes"]].count("action:write_recipe") == 1
    # and it is the DELIBERATE reading that survives, not the offer to undo it
    assert _by_id(surface, "action:write_recipe")["state"] == "on"


def test_the_deliberate_reading_of_an_entity_wins_whichever_check_ran_first():
    """The precedence the "not unmet ⇒ no fix" rule actually rests on, pinned where it is
    decided instead of where it happens to hold.

    `action:write_recipe` is emitted twice for one routine: "on", a deliberate switch carrying
    no fix, then "uncovered", which carries one — both NOTE. Until the merge stated a
    tie-break, which survived was decided by which append came first in `routine_surface`, with
    nothing marking either line as the one that must not move: moving it would have had the
    panel offer to undo a routine set up exactly as intended, with every test still passing.

    Severity is still asked first — a row that BLOCKS is never suppressed by a milder reading
    of the same entity, whatever either offers."""
    deliberate = {"id": "action:write_recipe", "severity": NOTE, "state": "on", "fix": {}}
    uncovered = {"id": "action:write_recipe", "severity": NOTE, "state": "uncovered",
                 "fix": {"kind": "cover_or_drop", "entity": "action:write_recipe"}}
    for order in ([deliberate, uncovered], [uncovered, deliberate]):
        kept = surface_mod._one_row_per_entity(list(order))
        assert [n["state"] for n in kept] == ["on"]

    absent = {"id": "util:ghost", "severity": BLOCKS, "state": "absent",
              "fix": {"kind": "install_util", "name": "ghost"}}
    mild = {"id": "util:ghost", "severity": NOTE, "state": "uncovered", "fix": {}}
    for order in ([absent, mild], [mild, absent]):
        assert surface_mod._one_row_per_entity(list(order))[0]["state"] == "absent"


# --- the schedule join: does the file say when this routine runs? ---------------------------


def test_a_member_cron_a_lane_suppresses_is_reported(tmp_path):
    """D71: a lane with a cron suppresses every member's own cron. A member that kept one has
    a routine.yaml naming a time it will never fire at — steward-hub-maintainer recorded 23:00
    while firing at 06:30 in its lane's chain — and nothing said the two disagreed."""
    from rsched import lanes as lanes_mod

    server = _server(tmp_path)
    lanes_mod.create(server.routines_home, name="Professional · Daily",
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
    """The mirror case: no cron of its own, no lane with a schedule. A perfectly good
    on-demand design — and indistinguishable from an oversight until it is said out loud."""
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
    """The composer reads state/phase.json as .get("phase"); that value scopes a stopping
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
    cron the lane suppresses, a phase file keyed wrong — and putting one in front of every
    run buys prompt noise it cannot act on. `rsched validate` still prints all three."""
    surface = {"nodes": [
        {"id": "a", "severity": BLOCKS, "why": "w", "effect": "e"},
        {"id": "b", "severity": INTERRUPTS, "why": "w", "effect": ""},
        {"id": "c", "severity": NOTE, "why": "w", "effect": ""},
    ]}
    assert len(surface_lines(surface)) == 3                     # validate: everything
    boot = surface_lines(surface, BOOT_SEVERITIES)
    assert [ln.split()[1] for ln in boot] == ["a:", "b:"]       # boot: no NOTE


@pytest.mark.usefixtures("empty_store")
def test_a_held_doc_whose_dial_is_not_switched_on_blocks(tmp_path):
    """The guard that should have caught the reminders rollout — and could not.

    "held, but its requires: are not switched on" was written when `requires:` named actions
    and utils only. Every DIAL added since — `runs`, `workflows`, `util_tags`, `reminders` —
    fell straight through it, so a routine could hold a doc, have its dial at the default, and
    read as READY. It asks the one cascade now, so a dial added tomorrow lands here on its own.
    """
    server = _server(tmp_path)
    _doc(server, "permissions", "reminders",
         "---\ntags: [a, b, c]\nrequires:\n  reminders: local\n---\n"
         "# permission: reminders — t\nb\n")
    cfg = _cfg(tmp_path, permissions=["reminders"],
               capabilities={"actions": [], "utils": [], "util_tags": [],
                             "reminders": "none"})
    node = _by_id(routine_surface(server, cfg), "permission:reminders")
    assert node and node["severity"] == BLOCKS
    assert "reminders=local" in node["effect"]


@pytest.mark.usefixtures("empty_store")
def test_a_dial_above_what_the_doc_asks_for_is_satisfied(tmp_path):
    """The dials are RANKED, so "different" is not "short". A routine at `reminders: global`
    honours a doc that requires `local` — reporting that as unsatisfied would tell the operator
    to turn something DOWN to make a warning go away.
    """
    server = _server(tmp_path)
    _doc(server, "permissions", "reminders",
         "---\ntags: [a, b, c]\nrequires:\n  reminders: local\n---\n"
         "# permission: reminders — t\nb\n")
    cfg = _cfg(tmp_path, permissions=["reminders"],
               capabilities={"actions": [], "utils": [], "util_tags": [],
                             "reminders": "global"})
    assert _by_id(routine_surface(server, cfg), "permission:reminders") is None


# --- the remedy: the panel diagnoses and says what settles it -------------------------------


@pytest.mark.usefixtures("empty_store")
def test_an_unsatisfied_permission_names_the_switch_that_settles_it(tmp_path):
    """The operator's question at a failed row is "where do I fix this?". The check that raises
    this one already knows the answer to the letter — it computed the shortfall to say what the
    failure costs. The row hands that same list over as its remedy, so the offer can read
    "switch on runs=last" rather than "go and look"."""
    server = _server(tmp_path)
    _doc(server, "permissions", "run-history",
         "---\ntags: [a, b, c]\nrequires:\n  runs: last\n---\n"
         "# permission: run history — t\nb\n")
    cfg = _cfg(tmp_path, permissions=["run-history"],
               capabilities={"actions": [], "utils": [], "util_tags": [], "runs": "none"})
    surface = routine_surface(server, cfg)
    node = _by_id(surface, "permission:run-history")
    assert node["severity"] == BLOCKS
    assert node["fix"] == {"kind": "switch_on", "entity": "run-history",
                           "missing": ["runs=last"]}
    # ...and the CLI half answers it too: `rsched validate` has no panel to click
    assert "fix: switch on runs=last" in surface_lines(surface)[0]


@pytest.mark.usefixtures("empty_store")
def test_a_met_row_offers_no_remedy(tmp_path):
    """A fix affordance on a satisfied row invites clicking to check what is already true; the
    panel's whole value is that all of it can be read without touching anything."""
    server = _server(tmp_path)
    _util(server, "signal", fs="roots, rw /srv/signal-sessions")
    _doc(server, "permissions", "personal-messaging",
         "---\ntags: [a, b, c]\nrequires:\n  utils: [signal]\n---\n# permission: x — y\n")
    surface = routine_surface(server, _cfg(tmp_path, permissions=["personal-messaging"],
                                           capabilities={"utils": ["signal"]},
                                           fs_write_roots=["/srv/signal-sessions"]))
    assert [n["id"] for n in surface["nodes"] if n["fix"]] == []
    assert _by_id(surface, "fs-write:/srv/signal-sessions")["fix"] == {}


@pytest.mark.usefixtures("empty_store")
def test_a_row_that_is_not_unmet_offers_no_remedy(tmp_path):
    """What an absent fix MEANS, settled once so the next node type has a rule instead of a
    precedent to guess from. SEVERITY does not decide it — a NOTE about a cron the lane
    overrides is unmet and carries one. Being UNMET decides it — and neither of these is. A
    routine that may rewrite its own recipe is set up exactly as intended; a retired one has MET
    every goal condition it was given. An offer on either is an offer to undo it, which
    reads as a defect report on a routine that is right — the retired row worst of all, since
    what it would undo is a finished job."""
    from rsched.engine import stopping

    server = _server(tmp_path)
    deliberate = routine_surface(server, _cfg(tmp_path,
                                              capabilities={"actions": ["write_recipe"]}))
    assert _by_id(deliberate, "action:write_recipe")["fix"] == {}

    cfg = _cfg(tmp_path)
    stopping.save(cfg.dir, {"mode": "all", "groups": [], "conditions": [
        {"id": "s1", "text": "the application is submitted", "scope": "goal",
         "status": "met"}]}, now="2026-09-05T00:00:00+02:00")
    surface = routine_surface(server, cfg)
    retired = _by_id(surface, "schedule:goal")
    assert retired and retired["severity"] == NOTE and retired["fix"] == {}
    # ...and the prose half ends at the effect rather than trailing a remedy nobody needs
    assert [ln for ln in surface_lines(surface) if "fix:" in ln] == []


@pytest.mark.usefixtures("empty_store")
def test_each_kind_of_gap_names_what_would_settle_it(tmp_path):
    """One row per thing an operator can actually do: write the missing util, grant a root of
    the right MODE (a read-only declaration asks for a read root — telling the operator to hand
    out write access to satisfy it would be the panel widening what it reports on), bind the
    account a rule presumes."""
    server = _server(tmp_path)
    _util(server, "reader", fs="ro /srv/data")
    _doc(server, "rules", "publishes",
         "---\ntags: [a, b, c]\nexpects:\n  connection: [google]\n---\n# rule: x — y\n")
    surface = routine_surface(server, _cfg(tmp_path, rules=["publishes"],
                                           capabilities={"utils": ["reader", "ghost"]}))
    assert _by_id(surface, "util:ghost")["fix"] == {"kind": "install_util", "name": "ghost",
                                                    "owner": "routine"}
    assert _by_id(surface, "fs-read:/srv/data")["fix"] == {
        "kind": "add_root", "mode": "read", "path": "/srv/data"}
    assert _by_id(surface, "connection:google")["fix"] == {
        "kind": "bind_connection", "provider": "google"}


@pytest.mark.usefixtures("empty_store")
def test_a_gap_fixed_off_this_page_still_says_what_settles_it(tmp_path):
    """Not every remedy is a panel on the routine page — a lane's schedule is instance
    state, a phase key is the recipe's. The row still names the remedy, because a reader who has
    to guess which of those it is has been abandoned at the moment they wanted to act."""
    from rsched import lanes as lanes_mod

    server = _server(tmp_path)
    lanes_mod.create(server.routines_home, name="Professional · Daily",
                     members=[{"slug": "r"}], cron="30 6 * * *", tz="Europe/Berlin")
    cron = _by_id(routine_surface(server, _cfg(tmp_path, cron="0 23 * * *")), "schedule:cron")
    assert cron["fix"]["kind"] == "lane_schedule"
    assert cron["fix"]["name"] == "Professional · Daily"

    cfg = _cfg(tmp_path)
    _with_phases(server, cfg, '{"lifecycle": "steady"}')
    assert _by_id(routine_surface(server, cfg), "state:phase")["fix"] == {
        "kind": "fix_phase", "expected": "phase"}


def _node_calls() -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(surface_mod))
    return [c for c in ast.walk(tree)
            if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_node"]


def _fix_args(call: ast.Call) -> list[ast.expr]:
    """The `fix` argument of one `_node` call — sixth positionally, or by keyword."""
    return list(call.args[5:6]) + [k.value for k in call.keywords if k.arg == "fix"]


def test_every_row_that_blocks_or_interrupts_carries_a_remedy():
    """The binding — and the reason it is written over the SOURCE rather than over one fixture:
    `fix` sat on `_node` from the day it was written with three call sites filling it in and
    every other passing nothing, which is how a diagnosis that names its remedy shipped for
    secrets alone. A row that costs the run something says what settles it, or this fails — and
    a node type added tomorrow lands here on its own."""
    unfixed = []
    for call in _node_calls():
        severity = ast.unparse(call.args[2])
        if ("BLOCKS" in severity or "INTERRUPTS" in severity) and not _fix_args(call):
            unfixed.append(f"{ast.unparse(call.args[0])} (line {call.lineno})")
    assert unfixed == []


def test_every_remedy_can_be_said_in_words():
    """The two halves of one answer: the panel turns a `fix` into a link, `rsched validate`
    turns the same one into a sentence. A kind with no words renders a gap with no remedy on
    the CLI — the whole failure, one kind later — and a phrase nothing emits is a remedy for a
    gap that cannot happen.

    Written across the two modules deliberately. The vocabulary is the seam between them, so
    the binding that keeps them one answer has to be read from both sides at once.

    A `kind:variant` entry is a second WORDING of one kind, never a second kind — `:any` for a
    need no sentence can name, `:domain` for a need said to somebody who cannot act on it where
    they stand — so the two sides are compared on the kind each variant belongs to. Which
    payload selects which wording is behaviour, pinned by the tests that render both."""
    kinds = {value.value
             for call in _node_calls() for arg in _fix_args(call)
             for d in ast.walk(arg) if isinstance(d, ast.Dict)
             for key, value in zip(d.keys, d.values, strict=True)
             if getattr(key, "value", None) == "kind"}
    assert kinds == {k.split(":", 1)[0] for k in REMEDIES}


def test_every_placeholder_a_remedy_names_is_a_param_a_fix_can_carry():
    """The OTHER half of the vocabulary, bound the same way the kinds are.

    `_remedy` formats every template against ONE set of params; the two are read against each
    other in both directions: a placeholder outside the set renders an empty gap where a
    subject belongs, a param no template names is furniture. Unbound, this is a wording edit
    with the reach of a crash — `format_map` over a plain mapping raises KeyError on an unknown
    placeholder that neither `engine/boot.py` nor `cli.py` catches, so the boot note which
    REPORTS a gap becomes the thing that ends the run.

    `Formatter().parse` rather than a regex over `{…}`, because it also rejects the malformed
    template a scan reads straight past."""
    named = {field for template in REMEDIES.values()
             for _, field, _, _ in Formatter().parse(template) if field is not None}
    assert named == set(remedies_mod._PARAMS)


def test_a_remedy_says_less_rather_than_raising(monkeypatch):
    """The failure mode the function PROMISES, pinned instead of described. A remedy is prose
    landing with no type to check it, rendered inside `rsched validate` and inside the boot note
    in front of a run: whatever the template names and whatever the payload is missing, the
    worst it may cost is a thinner sentence."""
    monkeypatch.setitem(REMEDIES, "add_secret", "add {name} through {nobody_declared_this}")
    assert remedies_mod._remedy({"kind": "add_secret", "name": "FOO"}) == "add FOO through "
    # a half-filled fix says the vaguer of the two sentences its kind has
    assert remedies_mod._remedy({"kind": "add_root", "mode": "write"}) == (
        "grant this routine a write root")
    # ...and a kind with no words at all renders no remedy, which is a row ending at its effect
    assert remedies_mod._remedy({"kind": "no_such_kind"}) == ""
    assert remedies_mod._remedy({}) == ""
