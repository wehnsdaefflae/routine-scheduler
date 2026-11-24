"""R3204: the nightly snapshot goes to the LOCAL disk first, the off-box share second."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_backup_snapshots import SHM, _backup, _ok
from test_deploy_state import DEPLOY, _env, _home


@pytest.fixture
def home(tmp_path) -> Path:
    return _home(tmp_path)


def _tiers(tmp_path: Path):
    """Two roots on the /dev/shm filesystem: the local tier and the off-box one."""
    if not SHM.is_dir() or not os.access(SHM, os.W_OK) \
            or SHM.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("needs a writable filesystem apart from tmp_path for the backup roots")
    base = Path(tempfile.mkdtemp(prefix="rsched-tiers-test-", dir=SHM))
    return base, base / "local" / "rsched-backup", base / "offbox" / "rsched-backup"


def test_the_local_tier_is_the_default_and_the_offbox_one_is_second():
    """The defaults carry the decision: a reader of the script sees which tier is which."""
    text = (DEPLOY / "backup.sh").read_text(encoding="utf-8")
    assert "DEFAULT_ROOT=/mnt/usb500/rsched-backup" in text
    assert "DEFAULT_MIRROR_ROOT=/mnt/sshd_volume1/rsched-backup" in text


def test_the_unit_passes_the_local_tier_and_hands_the_offbox_one_through_the_environment():
    """The unit's wiring is the whole order, and getting it backwards would make the NAS the
    nightly tier again — silently, since both paths are valid roots.
    """
    unit = (DEPLOY / "rsched-backup.service").read_text(encoding="utf-8")
    assert "Environment=RSCHED_BACKUP_LOCAL=/mnt/usb500/rsched-backup" in unit
    assert "Environment=RSCHED_MIRROR=/mnt/sshd_volume1/rsched-backup" in unit
    exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    assert exec_line.endswith("${RSCHED_BACKUP_LOCAL}"), exec_line
    assert "bash -c" not in exec_line, exec_line
    # And the script reads the off-box tier from that same variable, so the unit needs no wrapper.
    assert "RSCHED_MIRROR" in (DEPLOY / "backup.sh").read_text(encoding="utf-8")


def test_an_explicit_empty_mirror_root_means_local_only_and_is_not_overridden(home, tmp_path):
    """`MIRROR_ROOT=` is an instruction, not an absence: a caller that says "local tier only"
    must not have the unit's RSCHED_MIRROR filled in behind it."""
    base, local, _ = _tiers(tmp_path)
    try:
        proc = _backup(home, local, "2026-11-05",
                       overrides={"MIRROR_ROOT": "", "RSCHED_MIRROR": "/mnt/somewhere-else"})
        _ok(proc)
        assert "off-box mirror: not configured" in proc.stdout, proc.stdout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_both_tiers_complete_and_the_offbox_copy_matches_the_local_snapshot(home, tmp_path):
    base, local, offbox = _tiers(tmp_path)
    try:
        proc = _backup(home, local, "2026-11-05", overrides={"MIRROR_ROOT": str(offbox)})
        _ok(proc)
        day = max(p.name for p in (local / "snapshots").iterdir() if p.name[0].isdigit())
        assert (local / ".rsched-backup-completed").is_file()
        assert (offbox / ".rsched-backup-completed").is_file()
        assert (offbox / "snapshots" / day).is_dir(), "the off-box tier holds the same dated snapshot"
        assert (offbox / "latest").resolve() == (offbox / "snapshots" / day).resolve()
        local_files = {p.relative_to(local / "snapshots" / day)
                       for p in (local / "snapshots" / day).rglob("*") if p.is_file()}
        off_files = {p.relative_to(offbox / "snapshots" / day)
                     for p in (offbox / "snapshots" / day).rglob("*") if p.is_file()}
        assert local_files and local_files == off_files
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_an_offbox_failure_leaves_the_local_snapshot_complete_and_stamped(home, tmp_path):
    """THE PROPERTY THE WHOLE DECISION RESTS ON. The off-box step must be unable to fail or
    delay the local one — so an off-box target that is REFUSED (not a mounted disk of its own)
    must still leave a complete, stamped local snapshot and a zero exit.
    """
    base, local, _ = _tiers(tmp_path)
    try:
        # An off-box root on the HOME's own filesystem: refused by the mount guard.
        doomed = tmp_path / "not-a-disk" / "rsched-backup"
        proc = _backup(home, local, "2026-11-05", overrides={"MIRROR_ROOT": str(doomed)})
        assert proc.returncode == 0, f"an off-box refusal must not fail the night:\n{proc.stderr}"
        day = max(p.name for p in (local / "snapshots").iterdir() if p.name[0].isdigit())
        assert (local / ".rsched-backup-completed").is_file()
        assert (local / "latest").resolve() == (local / "snapshots" / day).resolve()
        assert "REFUSING (off-box tier)" in proc.stderr
        assert not (doomed / "snapshots").exists(), "nothing was written to the refused target"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_target_not_on_a_mounted_disk_of_its_own_is_refused(home, tmp_path):
    """Step 3, for the LOCAL tier: an unplugged drive leaves an ordinary directory behind, and
    a snapshot landing there would fill the root disk and report success.
    """
    doomed = tmp_path / "unplugged" / "rsched-backup"
    proc = _backup(home, doomed, "2026-11-05")
    assert proc.returncode == 1
    assert "NOT ON A MOUNTED DISK OF ITS OWN" in proc.stderr
    assert not (doomed / "snapshots").exists()


def test_the_offbox_step_can_be_skipped_without_touching_the_local_tier(home, tmp_path):
    base, local, offbox = _tiers(tmp_path)
    try:
        proc = _backup(home, local, "2026-11-05",
                        overrides={"MIRROR_ROOT": str(offbox), "SKIP_OFFBOX": "1"})
        _ok(proc)
        assert (local / ".rsched-backup-completed").is_file()
        assert not offbox.exists(), "skipped means not even created"
        assert "off-box mirror: skipped" in proc.stdout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_the_offbox_tier_leaves_out_the_caches_the_local_one_keeps(home, tmp_path):
    """The operator's (b), answering the measurement that `runs/` was not the bulk: `.libreoffice`
    and routine `.secrets` caches are ~20% of the off-box round trips and regenerate, so they go
    off-box and STAY on the local disk. His answer to the two options was "both (a) and (b)".
    """
    base, local, offbox = _tiers(tmp_path)
    (home / "routines/r1/.libreoffice/cache").mkdir(parents=True)
    (home / "routines/r1/.libreoffice/cache/big").write_text("regenerable", encoding="utf-8")
    (home / "routines/r1/.secrets").mkdir(parents=True, exist_ok=True)
    (home / "routines/r1/.secrets/restic-files.txt").write_text("a list", encoding="utf-8")
    (home / "routines/r1/state").mkdir(parents=True, exist_ok=True)
    (home / "routines/r1/state/plan.md").write_text("kept everywhere", encoding="utf-8")
    try:
        proc = _backup(home, local, "2026-11-05", overrides={"MIRROR_ROOT": str(offbox)})
        _ok(proc)
        day = max(p.name for p in (local / "snapshots").iterdir() if p.name[0].isdigit())
        for tier, name in ((local, "local"), (offbox, "off-box")):
            snap = tier / "snapshots" / day
            kept = (snap / "routines/r1/state/plan.md").is_file()
            assert kept, f"{name} tier dropped state/, which both tiers carry"
        assert (local / "snapshots" / day / "routines/r1/.libreoffice/cache/big").is_file()
        assert (local / "snapshots" / day / "routines/r1/.secrets/restic-files.txt").is_file()
        assert not (offbox / "snapshots" / day / "routines/r1/.libreoffice").exists()
        assert not (offbox / "snapshots" / day / "routines/r1/.secrets").exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_loose_git_objects_are_packed_before_the_snapshot(home, tmp_path):
    """The operator's (a). A loose object is ONE FILE and so one sshfs round trip, and 69,251 of
    them were 38% of the whole transferred set. `git gc` packs them and loses nothing — the
    history is packed, not pruned — so the snapshot copies packfiles instead.
    """
    base, local, _ = _tiers(tmp_path)
    repo = home / "routines/r1"
    repo.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git is None:
        pytest.skip("needs git")
    subprocess.run([git, "init", "-q", str(repo)], check=True, capture_output=True)
    for n in range(12):
        (repo / f"f{n}.txt").write_text(f"{n}", encoding="utf-8")
        subprocess.run([git, "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run([git, "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", f"c{n}"], check=True, capture_output=True)
    loose_before = len([p for p in (repo / ".git/objects").rglob("*")
                        if p.is_file() and len(p.parent.name) == 2])
    assert loose_before > 0, "the fixture repo has no loose objects to pack"
    try:
        # The floor is the test's, because the fixture repo is small on purpose: the PROPERTY is
        # "a repo over the floor ends with fewer loose objects than it began with", and it has to
        # be asserted on the COUNT. Asserting only that the line was printed is what let a
        # nightly no-op ship — see the next test.
        proc = _backup(home, local, "2026-11-05",
                       overrides={"SKIP_OFFBOX": "1", "GC_LOOSE_FLOOR": "1"})
        _ok(proc)
        assert "git objects:" in proc.stdout, proc.stdout
        assert f"{loose_before} loose before" in proc.stdout, proc.stdout
        loose_after = len([p for p in (repo / ".git/objects").rglob("*")
                           if p.is_file() and len(p.parent.name) == 2])
        assert loose_after < loose_before, (
            f"packing left {loose_after} of {loose_before} loose objects — each one is a file and "
            f"so one sshfs round trip, which is the whole reason this step exists")
        assert "1 repo(s) packed" in proc.stdout, proc.stdout
        # Packed, not PRUNED: the history is still all there.
        log = subprocess.run([git, "-C", str(repo), "rev-list", "--count", "HEAD"],
                             check=True, capture_output=True, text=True)
        assert log.stdout.strip() == "12", "packing lost commits"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_the_packing_floor_is_ours_because_gits_own_threshold_never_fires_here(home, tmp_path):
    """THE DEFECT THIS GUARDS. Shipped with git's own `gc.auto` default — 6,700 loose objects in
    ONE repository — this step packed NOTHING on this host for as long as it existed: the objects
    are spread over ~30 small repos, the largest holding 4,344. Measured 2026-11-24 from the
    unit's own journal, "44687 loose before, 44687 after (0 repo(s) packed)", against 45,277 loose
    objects over 30 repos, while the step's purpose was to remove 38% of the off-box tier's round
    trips. So the threshold has to be the BACKUP's question (this repo has enough files to be
    worth packing) and not git's (this repo is big enough to be worth maintaining).
    """
    base, local, _ = _tiers(tmp_path)
    repo = home / "routines/r1"
    repo.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git is None:
        pytest.skip("needs git")
    subprocess.run([git, "init", "-q", str(repo)], check=True, capture_output=True)
    for n in range(12):
        (repo / f"f{n}.txt").write_text(f"{n}", encoding="utf-8")
        subprocess.run([git, "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run([git, "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", f"c{n}"], check=True, capture_output=True)
    loose = len([p for p in (repo / ".git/objects").rglob("*")
                 if p.is_file() and len(p.parent.name) == 2])
    assert 0 < loose < 6700, "the fixture must sit below git's own default, as every repo here does"
    try:
        # A floor ABOVE the repo's count leaves it alone, and the report says so honestly — this
        # is the behaviour every night had, and it must be reachable only by choosing it.
        under = _backup(home, local, "2026-11-05",
                        overrides={"SKIP_OFFBOX": "1", "GC_LOOSE_FLOOR": str(loose + 1)})
        _ok(under)
        assert f"{loose} loose before, {loose} after (0 repo(s) packed)" in under.stdout, under.stdout
        still = len([p for p in (repo / ".git/objects").rglob("*")
                     if p.is_file() and len(p.parent.name) == 2])
        assert still == loose
        # The DEFAULT floor must be low enough to fire on the repos this host actually has: the
        # largest holds 4,344 loose objects and the median around 1,650.
        source = (DEPLOY / "backup.sh").read_text(encoding="utf-8")
        default = int(source.split('GC_LOOSE_FLOOR="${GC_LOOSE_FLOOR:-')[1].split("}")[0])
        assert 0 < default <= 1000, (
            f"a default floor of {default} would not pack this host's repos, which is the no-op "
            f"this test exists to prevent")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_the_packing_never_detaches_never_prunes_and_never_estimates():
    """Three measured hazards, all in the invocation itself. `gc.autoDetach` defaults to TRUE, so
    a gc that finds work FORKS and returns — leaving it rewriting the object directories while
    rsync walks them, which is worse than not packing at all. A backup is the last thing that
    should decide an unreferenced object a run just wrote is garbage, so `--no-prune`: measured
    2026-11-24, it still empties the loose directories (4,344 → 0) while deleting nothing. And
    `--auto` must be GONE: it does not count loose objects but samples `objects/17` and
    multiplies by 256, so a repo with 36 loose objects estimates 0 and is never packed at any
    threshold — half of why this step was a nightly no-op. The count is ours; the gc after it is
    unconditional.
    """
    source = (DEPLOY / "backup.sh").read_text(encoding="utf-8")
    invocation = source[source.index("if ! git -c "):source.index("could not be packed")]
    assert "gc.autoDetach=false" in invocation, (
        "a detached gc races the snapshot it is supposed to precede")
    assert "--no-prune" in invocation, "a backup may pack history, never prune it"
    assert "--auto" not in invocation, (
        "`gc --auto` is an estimator over one sampled fanout directory, not a count: it packs "
        "nothing on a repo whose objects/17 is empty, at any threshold")


def test_packing_can_be_switched_off_and_a_failure_never_fails_the_night(home, tmp_path):
    """`git gc` is an optimisation. A repo that cannot be packed is snapshotted as it is — every
    night before this change was — so neither a broken repo nor a host without git may cost the
    backup, and the switch exists so the unit can turn it off without editing the script.
    """
    base, local, _ = _tiers(tmp_path)
    broken = home / "routines/r1/.git"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "objects").mkdir(exist_ok=True)       # a .git that is not a repository
    try:
        off = _backup(home, local, "2026-11-05",
                      overrides={"SKIP_OFFBOX": "1", "GC_REPOS": "0"})
        _ok(off)
        assert "GC_REPOS=0 — not packing" in off.stdout, off.stdout
        on = _backup(home, local, "2026-11-05", overrides={"SKIP_OFFBOX": "1"})
        _ok(on)                                     # the broken repo did not fail the night
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_no_heredoc_in_deploy_substitutes_a_command_when_it_is_printed():
    """A hazard that fired for real on 2026-11-05, not a hypothetical."""
    offenders = []
    for script in sorted(DEPLOY.glob("*.sh")):
        lines = script.read_text(encoding="utf-8").splitlines()
        inside = None
        for number, line in enumerate(lines, 1):
            if inside is None:
                stripped = line.strip()
                if stripped.startswith("cat <<") and "'" not in stripped and '"' not in stripped:
                    inside = stripped.split("<<", 1)[1].strip().lstrip("-")
                continue
            if line.strip() == inside:
                inside = None
                continue
            if "`" in line or "$(" in line:
                offenders.append(f"{script.name}:{number}: {line.strip()[:70]}")
    assert not offenders, (
        "an unquoted heredoc command-substitutes its text, so printing help would RUN these:\n"
        + "\n".join(offenders))


def test_help_has_no_side_effects(home, tmp_path):
    """The same hazard, from the outside: --help writes nothing and exits 0."""
    env = _env(home)
    env["MIRROR_ROOT"] = ""
    before = set(Path(env["HOME"]).rglob("*"))
    proc = subprocess.run(["bash", str(DEPLOY / "backup.sh"), "--help"], env=env,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "TWO TIERS" in proc.stdout
    assert set(Path(env["HOME"]).rglob("*")) == before
