"""The run-history depth is a REAL boundary: the jail refuses what the action gate refuses (D207).

The measurement that opened this: `read_file` refused an older `runs/<ts>/gate.json` with
"previous runs are readable at the default 'last' depth only", while `gu json get <the same
absolute path>` returned it. The cap lived in the action layer, and a util is handed the path
and opens it with ordinary access inside a jail that mounted the whole routine dir readable —
its own `runs/` tree included. The operator decided it is a real boundary and that it belongs
where the jail is composed.

So the test that matters here is the one that runs a real jailed command at a real old run
directory and asserts the KERNEL refuses it. The rest of this file is the arithmetic around it:
which run dirs a depth admits, that both doors into `sandbox.wrap` carve, and that the run is
TOLD what was withheld instead of meeting a bare permission error.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import run_context, server_config
from rsched import ownseal, rundepth, sandbox
from rsched.engine.executor import dispatch
from rsched.engine.jailnotes import jail_notes
from rsched.grantpolicy import GrantPolicy

OLD, MID, NEW = "20260101-000000", "20260102-000000", "20260103-000000"
CURRENT = "20260104-000000"


def _tree(tmp_path: Path, *runs: str) -> Path:
    own = tmp_path / "a-routine"
    for name in ("state", ".memory"):
        (own / name).mkdir(parents=True, exist_ok=True)
    for ts in runs:
        (own / "runs" / ts).mkdir(parents=True)
        (own / "runs" / ts / "status.json").write_text(f'{{"run": "{ts}"}}', encoding="utf-8")
    return own


def _scope(own: Path, depth: str, current: str = CURRENT) -> rundepth.Scope:
    return rundepth.Scope(root=own.resolve(), depth=depth, current=current)


# ---- which run dirs a depth admits -------------------------------------------------------


def test_last_admits_the_newest_prior_run_and_nothing_older(tmp_path):
    own = _tree(tmp_path, OLD, MID, NEW, CURRENT)
    scope = _scope(own, "last")
    assert [p.name for p in rundepth.readable(scope)] == [NEW]
    assert rundepth.refused(scope) == [OLD, MID]
    assert rundepth.current_dir(scope) == own / "runs" / CURRENT


def test_all_admits_every_prior_run_and_none_refuses_them_all(tmp_path):
    own = _tree(tmp_path, OLD, MID, NEW, CURRENT)
    assert [p.name for p in rundepth.readable(_scope(own, "all"))] == [OLD, MID, NEW]
    assert rundepth.refused(_scope(own, "all")) == []
    assert rundepth.readable(_scope(own, "none")) == []
    assert rundepth.refused(_scope(own, "none")) == [OLD, MID, NEW]
    # …but the current run's own tree is readable at EVERY depth: the engine points the model
    # there after compaction, and a child's workspace lives under runs/<ts>/sub/<n>/.
    assert rundepth.current_dir(_scope(own, "none")) == own / "runs" / CURRENT


def test_the_last_depth_is_computed_by_one_function_for_both_enforcers(tmp_path):
    """`last_prior` is what the action gate and the jail both read. A second implementation
    kept in step by hand is how one path refuses what the other serves — which is the defect
    D207 exists to end, so it is asserted rather than assumed.
    """
    own = _tree(tmp_path, OLD, MID, CURRENT)
    runs = own / "runs"
    assert rundepth.last_prior(runs, CURRENT) == MID
    # whichever run is CURRENT is excluded, and the newest of the rest is "last" — run dir
    # names are timestamps, so sorted order IS chronological order
    assert rundepth.last_prior(runs, MID) == CURRENT
    assert rundepth.last_prior(runs / "nope", CURRENT) is None   # total about a missing tree
    assert rundepth.last_prior(_tree(tmp_path / "fresh") / "runs", CURRENT) is None


def test_a_first_run_refuses_nothing_and_says_nothing(tmp_path):
    """No previous runs is the common case on a fresh routine: the boundary withholds nothing,
    so the observation must carry no field about it (no noise where there is no cost)."""
    own = _tree(tmp_path, CURRENT)
    scope = _scope(own, "last")
    assert rundepth.readable(scope) == [] and rundepth.refused(scope) == []
    assert rundepth.note(scope) == {}


# ---- the jail mount -----------------------------------------------------------------------


def _mounts(own: Path, scope) -> tuple[set[str], set[str], set[str]]:
    ro: list[str] = []
    rw: list[str] = []
    ls: list[str] = []
    ownseal.mount_own_dir(sandbox.SandboxPolicy(own_dir=own, run_depth=scope), ro, rw, ls)
    return set(ro), set(rw), set(ls)


def _granted(rules: set[str], path: Path) -> bool:
    """Is `path` covered by any rule in `rules` — at it, or beneath it?

    Landlock rights are inherited DOWN the hierarchy, so literal set membership is the wrong
    question and asserting it is how a boundary test passes against unenforced code: mounting
    the routine dir `ro` grants every run dir beneath it without ever naming one. (The same
    trap `test_own_dir_seal._writable` documents for the write seal.)
    """
    return any(path == Path(r) or path.is_relative_to(Path(r)) for r in rules)


def test_the_jail_mounts_only_the_run_dirs_the_depth_allows(tmp_path):
    own = _tree(tmp_path, OLD, MID, NEW, CURRENT)
    ro, rw, ls = _mounts(own, _scope(own, "last"))
    runs = own / "runs"
    # the own dir is LIST-ONLY: a command still cds into it and lists it…
    assert str(own) in ls
    # …but it is NOT readable wholesale, which is the hole D207 closed
    assert not _granted(ro, own / "runs" / OLD / "status.json")
    assert not _granted(ro, own / "runs" / MID / "status.json")
    assert not _granted(rw, own / "runs" / OLD / "status.json")
    # the permitted prior run and the current run ARE readable
    assert _granted(ro, runs / NEW / "status.json")
    assert _granted(ro, runs / CURRENT / "status.json")
    # and nothing else about reading the routine dir changed
    assert _granted(ro, own / "state") and _granted(ro, own / ".memory")


def test_depth_all_mounts_every_prior_run(tmp_path):
    own = _tree(tmp_path, OLD, MID, CURRENT)
    ro, _rw, ls = _mounts(own, _scope(own, "all"))
    assert str(own) in ls
    for ts in (OLD, MID, CURRENT):
        assert _granted(ro, own / "runs" / ts / "status.json"), ts


def test_depth_none_mounts_no_prior_run_but_keeps_the_current_one(tmp_path):
    """A sub-workflow child runs without run history: its context is its brief, not the
    archive. Its own workspace lives under runs/<ts>/sub/<n>/ and must stay reachable."""
    own = _tree(tmp_path, OLD, CURRENT)
    ro, _rw, _ls = _mounts(own, _scope(own, "none"))
    assert not _granted(ro, own / "runs" / OLD / "status.json")
    assert _granted(ro, own / "runs" / CURRENT / "status.json")


def test_the_second_door_carves_too_without_a_run_behind_it(tmp_path):
    """BOTH doors into `sandbox.wrap`. The daemon's admission jails build their own
    `SandboxPolicy` (daemon/gate_prepare.py) before a run exists, so they carry no depth —
    and a change covering only the util path would leave that door open, which is exactly the
    shape of half-fix the obvious test sweep misses. A policy with a routine dir and no run
    falls to the CLOSED `last` floor, never to the old mount-everything shape.
    """
    own = _tree(tmp_path, OLD, MID, NEW)
    ro, _rw, ls = _mounts(own, None)
    assert str(own) in ls
    assert _granted(ro, own / "runs" / NEW / "status.json")       # the last run only
    assert not _granted(ro, own / "runs" / OLD / "status.json")
    assert not _granted(ro, own / "runs" / MID / "status.json")


def test_wrap_drops_the_routine_dir_from_the_wholesale_roots_mount(tmp_path):
    """A granted root EQUAL to the routine dir would re-grant read beneath it and undo the
    carve — and every child run holds exactly that root (F185/childrun.py). Rights are
    additive downward, so the carve only holds if that root is not also mounted `ro`.
    """
    import json

    from rsched import landlock

    own = _tree(tmp_path, OLD, NEW, CURRENT)
    (tmp_path / "lib" / "utils").mkdir(parents=True)
    policy = sandbox.SandboxPolicy(mode="permissive", read_roots=(own,), own_dir=own,
                                   run_depth=_scope(own, "last"))
    monkey = landlock._abi
    try:
        landlock._abi = 4
        spec = json.loads(sandbox.wrap(["/bin/true"], policy=policy,
                                       libraries_home=tmp_path / "lib", net=True,
                                       fs_roots=True, fs_paths=())[2])
    finally:
        landlock._abi = monkey
    assert str(own) in spec["ls"]
    assert str(own) not in spec["ro"]
    assert not _granted(set(spec["ro"]), own / "runs" / OLD / "status.json")
    assert _granted(set(spec["ro"]), own / "runs" / NEW / "status.json")


# ---- what the run is TOLD ------------------------------------------------------------------


def test_the_refusal_teaches_what_the_cap_is_and_that_it_is_configurable(tmp_path):
    """A refusal that teaches nothing is a defect in this project, and a jailed command meets
    a bare kernel permission error — which says nothing about why. So the engine names the
    depth, what IS readable, and that the depth is the operator's setting.
    """
    own = _tree(tmp_path, OLD, MID, NEW, CURRENT)
    policy = sandbox.SandboxPolicy(own_dir=own, run_depth=_scope(own, "last"))
    note = jail_notes(policy)["run_depth"]
    assert note["depth"] == "last"
    assert note["refused_runs"] == 2
    assert note["readable_runs"] == sorted([NEW, CURRENT])
    text = note["note"]
    assert "last" in text and "capabilities.runs" in text
    assert "none | last | all" in text          # what the operator can set it to
    assert "ask_user" in text                   # and the route to a wider depth
    assert "always readable" in text            # the current run's own tree


def test_a_root_above_the_routine_dir_is_reported_not_quietly_untrue(tmp_path):
    """A granted root that CONTAINS the routine dir (`~/routines`: the meta routines hold it)
    is mounted as one hierarchy, so the depth cannot bind inside it. The operator asked for a
    boundary with its cost named — so the run is told where it does NOT bind, rather than
    being left to believe in a boundary it does not have.
    """
    own = _tree(tmp_path, OLD, NEW, CURRENT)
    scope = _scope(own, "last")
    assert rundepth.uncarvable(scope, (tmp_path,)) == [str(tmp_path.resolve())]
    assert rundepth.uncarvable(scope, (own / "runs",)) == []       # the tree itself: not above
    assert rundepth.uncarvable(scope, (tmp_path / "elsewhere",)) == []
    policy = sandbox.SandboxPolicy(own_dir=own, read_roots=(tmp_path,), run_depth=scope)
    note = jail_notes(policy)["run_depth"]
    assert note["not_enforced_under"] == [str(tmp_path.resolve())]
    assert "does NOT bind" in note["note"]


def test_no_depth_scope_adds_no_field(tmp_path):
    """A policy with no run behind it (a `base_policy`: write_util selftests, the notify
    channel) mounts no routine dir, so there is nothing to say about run history."""
    assert jail_notes(sandbox.base_policy(SimpleNamespace(sandbox="permissive"))) == {}


# ---- the kernel, which is the only proof that counts ---------------------------------------


def test_a_jailed_command_handed_an_older_runs_path_is_refused_by_the_kernel(
        make_routine, tmp_path, monkeypatch):
    """THE test for this decision: the exact bypass the measurement recorded, run for real.

    A util or shell command is handed an ABSOLUTE path to an older run's file and opens it with
    ordinary access — that is what made the action-layer cap prompt economy rather than a
    boundary. Proving the refusal outside pytest, or against a spec dict, would prove it in the
    wrong environment: a spec with the right strings in it says nothing about what the kernel
    does.

    The toolchain's tmp rules are withheld for the duration, and here they must be withheld
    ENTIRELY rather than moved to read-only as `test_own_dir_seal` does: pytest's `tmp_path`
    lives under /tmp, `_toolchain()` mounts /tmp and /var/tmp read-WRITE for every jailed
    command, and a READ refusal cannot be proven at a path another rule grants read on. (On the
    live instance routine dirs are under the routines home, so nothing is weakened there — this
    is a test-location artefact.)
    """
    import tempfile

    if not sandbox.available():
        pytest.skip("Landlock unavailable on this kernel — the jail cannot engage")
    real_toolchain = sandbox._toolchain
    scratch = Path(tempfile.gettempdir()).resolve()

    def _is_scratch(root: str) -> bool:
        p = Path(root).resolve()
        return p == scratch or scratch.is_relative_to(p)

    def _no_tmp():
        ro, rw = real_toolchain()
        return [r for r in ro if not _is_scratch(r)], [r for r in rw if not _is_scratch(r)]

    monkeypatch.setattr(sandbox, "_toolchain", _no_tmp)
    server = server_config(libraries_home=tmp_path / "libraries", sandbox="permissive")
    (server.libraries_home / "utils").mkdir(parents=True)
    ctx = run_context(make_routine(slug="capped"), CURRENT, server=server,
                      grants=GrantPolicy(actions=frozenset({"shell"}), run_history="last"))
    own = ctx.routine.dir
    for ts in (OLD, MID, NEW):
        (own / "runs" / ts).mkdir(parents=True, exist_ok=True)
        (own / "runs" / ts / "status.json").write_text(f'{{"run": "{ts}"}}', encoding="utf-8")
    (own / "runs" / CURRENT / "status.json").write_text('{"run": "current"}', encoding="utf-8")

    # refused: every run older than the depth allows, by ABSOLUTE path — the measured bypass
    for ts in (OLD, MID):
        obs = dispatch({"kind": "shell",
                        "command": f"cat {own / 'runs' / ts / 'status.json'}"}, ctx)
        assert obs["exit"] != 0, f"the jail served runs/{ts}: {obs}"
        assert ts not in obs.get("stdout", "")

    # allowed: the last prior run (the depth's own promise) and this run's own tree
    for ts in (NEW, CURRENT):
        obs = dispatch({"kind": "shell",
                        "command": f"cat {own / 'runs' / ts / 'status.json'}"}, ctx)
        assert obs["exit"] == 0, obs
        assert "run" in obs["stdout"]

    # and the run ids stay ENUMERABLE: a list-only mount is inherited downward, so `ls` works
    # at any depth while the contents stay shut. A glob that enumerates then opens therefore
    # fails loudly at the open, instead of silently seeing nothing.
    obs = dispatch({"kind": "shell", "command": f"ls {own / 'runs'}"}, ctx)
    assert obs["exit"] == 0, obs
    for ts in (OLD, MID, NEW, CURRENT):
        assert ts in obs["stdout"]


def test_the_action_gate_and_the_jail_refuse_the_same_path(make_routine, tmp_path):
    """One depth, two enforcers, no gap: `read_file` and the jail must disagree about nothing.
    This is the pairing the decision rests on — the action gate alone was the defect.
    """
    server = server_config(libraries_home=tmp_path / "libraries", sandbox="permissive")
    ctx = run_context(make_routine(slug="paired"), CURRENT, server=server,
                      grants=GrantPolicy(run_history="last"))
    own = ctx.routine.dir
    for ts in (OLD, NEW):
        (own / "runs" / ts).mkdir(parents=True, exist_ok=True)
        (own / "runs" / ts / "status.json").write_text("{}", encoding="utf-8")
    scope = rundepth.scope_for_ctx(ctx)
    assert scope is not None and scope.depth == "last" and scope.current == CURRENT

    refused_by_gate = rundepth.read_gate(ctx, (own / "runs" / OLD / "status.json").resolve())
    assert refused_by_gate and "last" in refused_by_gate
    assert rundepth.read_gate(ctx, (own / "runs" / NEW / "status.json").resolve()) is None
    # the jail says the same, directory for directory
    mounted = {p.name for p in rundepth.readable(scope)}
    assert NEW in mounted and OLD not in mounted
