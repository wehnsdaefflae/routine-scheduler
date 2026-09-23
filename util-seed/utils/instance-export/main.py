"""instance-export — mirror this instance's routines + sanitized config into the library repo tree.

usage: gu instance-export DEST [--routines-home PATH] [--config PATH] [--json]
calls: (none)
secrets: (none)
tags: sync, backup, meta
net: outbound
fs: roots

Everything the instance acquires syncs to ONE repo — this util stages the instance-owned part
into that repo's working tree (DEST, normally ~/.local/share/routine-scheduler-libraries, which
already holds workflows/, traits/, permissions/, playbooks/, utils/): (a) every routine under
--routines-home (default ~/routines) into DEST/routines/<slug>/, minus transient run state (runs/,
.git/, inbox/, questions/, status.json — routine.yaml, main.md, stages/, traits/, state/,
LEDGER.md all stay); (b) the server config (default ~/.config/routine-scheduler/config.yaml)
into DEST/config/config.yaml with every `token` and `api_key` value replaced by REDACTED —
parsed as YAML, never regexed. Idempotent and rsync-like: files gone from the source are deleted
from DEST. An unreadable file or directory (permission-denied mounts etc.) is SKIPPED and
recorded in `errors` rather than aborting the whole export. Two guards keep the mirror
pushable and private: a routine that is a git repo is enumerated through ITS OWN git view
(`git ls-files --cached --others --exclude-standard`), so whatever the routine's .gitignore
keeps out of its repo — a .secrets/ folder, mnt/, .venv — never reaches the mirror either;
and any file at or over MAX_FILE_BYTES (10 MiB) is never copied and is pruned from DEST if a
previous run copied it, listed in `oversize` (GitHub refuses a 100 MB blob outright and a
223 MB state inventory once blocked every push for days; the routine's own repo is the
place for such a file, not the off-box mirror). Run it right before git-sync on DEST.
--selftest builds a fake instance in a temp dir (including a permission-denied subdirectory,
a git-ignored secret and an oversize file) and asserts exclusions, redaction, deletion of
vanished files, and that a permission error is recorded without crashing — fully offline."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

EXCLUDE = {"runs", ".git", "inbox", "questions", "status.json",
           ".venv", ".util_outputs", "mnt"}   # transient run state / generated caches / external mount points
REDACT_KEYS = {"token", "api_key"}
MAX_FILE_BYTES = 10 * 1024 * 1024   # a mirror carries files people read; nothing they read is 10 MiB


def _git_listed(routine_dir: Path) -> list[Path] | None:
    """The files the routine's OWN repo tracks or would track (tracked + untracked-not-ignored),
    or None when the routine is not a git repo or git cannot answer. This is the routine's own
    statement of what belongs to it: its .gitignore already keeps runs/, mnt/, .venv/ and any
    .secrets/ out, and the mirror must not know better than the routine does."""
    if not (routine_dir / ".git").is_dir():
        return None
    try:
        r = subprocess.run(["git", "-C", str(routine_dir), "ls-files", "-z", "--cached",
                            "--others", "--exclude-standard"],
                           capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return [routine_dir / rel for rel in r.stdout.split("\0") if rel]


def _wanted_files(routine_dir: Path) -> tuple[list[Path], list[str]]:
    """Every file under routine_dir except the transient-state names, at any depth — taken
    from the routine's own git view when it has one (see _git_listed), from a plain walk
    otherwise. Returns (files, errors): a directory os.walk cannot list (permission denied, a
    broken mount, ...) is skipped and its error recorded here instead of raising and aborting
    the whole export — the fault of one routine's stray mount must never sink every routine's
    sync."""
    out: list[Path] = []
    errors: list[str] = []
    listed = _git_listed(routine_dir)
    if listed is not None:
        for p in listed:
            rel_parts = p.relative_to(routine_dir).parts
            if any(part in EXCLUDE for part in rel_parts):
                continue
            out.append(p)
        return sorted(out), errors

    def onerror(exc: OSError) -> None:
        errors.append(str(exc))

    for dirpath, dirnames, filenames in os.walk(routine_dir, onerror=onerror):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE]
        for fname in filenames:
            if fname in EXCLUDE:
                continue
            out.append(Path(dirpath) / fname)
    return sorted(out), errors


def export_routines(routines_home: Path, dest_routines: Path) -> dict:
    """Mirror each routine's persistent tree into dest_routines/<slug>/ (copy + prune)."""
    exported, skipped = [], []
    desired: set[Path] = set()                        # rel-to-dest_routines paths that should exist
    slugs: set[str] = set()
    all_errors: list[str] = []
    oversize: list[dict] = []
    if routines_home.is_dir():
        for rdir in sorted(p for p in routines_home.iterdir() if p.is_dir()):
            if rdir.name.startswith("."):
                skipped.append(rdir.name)
                continue
            slugs.add(rdir.name)
            copied = unchanged = 0
            files, walk_errors = _wanted_files(rdir)
            all_errors.extend(walk_errors)
            for src in files:
                rel = Path(rdir.name) / src.relative_to(rdir)
                try:
                    if not src.is_file() or src.is_symlink():
                        continue
                    size = src.stat().st_size
                except OSError as exc:
                    all_errors.append(f"{src}: {exc}")
                    desired.add(rel)                   # keep on a transient fault — never prune blind
                    continue
                if size >= MAX_FILE_BYTES:
                    # never mirrored, and NOT in `desired`, so a copy a previous run made is pruned
                    oversize.append({"path": str(rel), "bytes": size})
                    continue
                desired.add(rel)
                dst = dest_routines / rel
                try:
                    data = src.read_bytes()
                except OSError as exc:
                    all_errors.append(f"{src}: {exc}")
                    continue
                if dst.is_file() and dst.read_bytes() == data:
                    unchanged += 1
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
                copied += 1
            exported.append({"slug": rdir.name, "copied": copied, "unchanged": unchanged})
    removed = 0
    if dest_routines.is_dir():                        # prune what vanished from the source
        for p in sorted(dest_routines.rglob("*"), reverse=True):
            rel = p.relative_to(dest_routines)
            if p.is_file() and rel not in desired:
                p.unlink()
                removed += 1
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
    return {"exported": exported, "skipped": skipped, "removed": removed,
            "slugs": sorted(slugs), "errors": all_errors, "oversize": oversize}


def redact(obj):
    """Recursively blank secret values: any `token`/`api_key` mapping entry with a non-empty
    value becomes REDACTED (empty stays empty — it honestly says 'nothing was set')."""
    hits = 0
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in REDACT_KEYS and isinstance(val, (str, int, float)) and str(val).strip():
                obj[key] = "REDACTED"
                hits += 1
            else:
                hits += redact(val)
    elif isinstance(obj, list):
        for item in obj:
            hits += redact(item)
    return hits


def export_config(config_path: Path, dest_dir: Path) -> dict:
    if not config_path.is_file():
        return {"exported": False, "reason": f"{config_path} not found"}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"exported": False, "reason": f"{config_path} is not a YAML mapping"}
    redacted = redact(data)
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "config.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"exported": True, "redacted_values": redacted}


def run(dest: str, routines_home: str, config: str) -> dict:
    dest_path = Path(dest).expanduser()
    if not dest_path.is_dir():
        raise ValueError(f"DEST {dest_path} is not a directory (clone/create the library repo first)")
    routines = export_routines(Path(routines_home).expanduser(), dest_path / "routines")
    cfg = export_config(Path(config).expanduser(), dest_path / "config")
    return {"dest": str(dest_path), "routines": routines, "config": cfg}


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "routines"
        keep = ["routine.yaml", "main.md", "LEDGER.md",
                "stages/one.md", "traits/t.md", "state/phase.json"]
        drop = ["status.json", "runs/2026-01-01T00-00-00/transcript.jsonl",
                "inbox/msg.json", "questions/pending/q.json", ".git/HEAD"]
        for rel in keep + drop:
            p = home / "demo" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content of {rel}\n")
        # a second routine that IS a git repo: its own .gitignore decides what the mirror
        # sees (a .secrets/ folder stays home), and an oversize state file is never mirrored
        repo = home / "gitty"
        (repo / "state").mkdir(parents=True)
        (repo / ".secrets").mkdir()
        (repo / "routine.yaml").write_text("slug: gitty\n")
        (repo / "state" / "ok.json").write_text("{}\n")
        (repo / "state" / "huge.jsonl").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        (repo / ".secrets" / "key.pem").write_text("PRIVATE\n")
        (repo / ".gitignore").write_text(".secrets/\n")
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        (repo / "state" / "untracked.json").write_text("{}\n")   # not yet committed: still exported
        (home / ".control").mkdir(parents=True)
        (home / ".control" / "restart.request").write_text("{}")
        # a permission-denied subdirectory (a stray mount, an unreadable cache) must not abort the export
        restricted = home / "demo" / "restricted"
        restricted.mkdir()
        (restricted / "secret.txt").write_text("nope\n")
        restricted.chmod(0o000)
        try:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("bind: 127.0.0.1\ntoken: \"super-secret\"\n"
                           "endpoints:\n  or:\n    kind: openai\n    api_key: sk-live-123\n"
                           "  local:\n    kind: openai\n    api_key: \"\"\n")
            dest = Path(tmp) / "library"
            dest.mkdir()
            result = run(str(dest), str(home), str(cfg))
            for rel in keep:                                              # persistent files exported
                assert (dest / "routines" / "demo" / rel).is_file(), rel
            for rel in drop:                                              # transient state excluded
                assert not (dest / "routines" / "demo" / rel).exists(), rel
            assert not (dest / "routines" / ".control").exists()          # dot-dirs are not routines
            assert result["routines"]["skipped"] == [".control"], result["routines"]
            assert result["routines"]["errors"], "expected the permission-denied dir to be recorded"
            gd = dest / "routines" / "gitty"
            assert (gd / "state" / "ok.json").is_file() and (gd / "state" / "untracked.json").is_file()
            assert not (gd / ".secrets").exists(), "a git-ignored folder must never reach the mirror"
            assert not (gd / "state" / "huge.jsonl").exists(), "an oversize file must never be mirrored"
            assert result["routines"]["oversize"] == [
                {"path": "gitty/state/huge.jsonl", "bytes": MAX_FILE_BYTES + 1}], result["routines"]
            # a copy a PREVIOUS run made of a now-oversize file is pruned, not kept
            (gd / "state" / "stale-big.jsonl").write_bytes(b"old copy")
            (repo / "state" / "stale-big.jsonl").write_bytes(b"y" * (MAX_FILE_BYTES + 1))
            out_cfg = yaml.safe_load((dest / "config" / "config.yaml").read_text())
            assert out_cfg["token"] == "REDACTED"
            assert out_cfg["endpoints"]["or"]["api_key"] == "REDACTED"
            assert out_cfg["endpoints"]["local"]["api_key"] == ""         # empty stays empty
            assert out_cfg["bind"] == "127.0.0.1" and result["config"]["redacted_values"] == 2
            # idempotence + rsync-like pruning: delete at the source → gone from the mirror
            (home / "demo" / "stages" / "one.md").unlink()
            second = run(str(dest), str(home), str(cfg))
            assert not (dest / "routines" / "demo" / "stages").exists()
            assert not (gd / "state" / "stale-big.jsonl").exists()
            demo = second["routines"]["exported"][0]
            assert demo["copied"] == 0 and second["routines"]["removed"] == 2, second["routines"]
        finally:
            restricted.chmod(0o700)                    # restore so TemporaryDirectory cleanup can proceed
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu instance-export",
                                description="Stage routines + sanitized config into the library repo tree.")
    p.add_argument("dest", nargs="?", help="library repo working tree (the export target)")
    p.add_argument("--routines-home", default="~/routines", help="routines home (default ~/routines)")
    p.add_argument("--config", default="~/.config/routine-scheduler/config.yaml",
                   help="server config to sanitize + export")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.dest:
        p.error("provide DEST (the library repo working tree)")
    try:
        result = run(args.dest, args.routines_home, args.config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
    else:
        r, c = result["routines"], result["config"]
        cfg_note = "config sanitized" if c.get("exported") else f"config skipped ({c.get('reason')})"
        err_note = f"; {len(r['errors'])} errors" if r.get("errors") else ""
        big_note = (f"; {len(r['oversize'])} oversize file(s) NOT mirrored: "
                    + ", ".join(o["path"] for o in r["oversize"])) if r.get("oversize") else ""
        print(f"exported {len(r['exported'])} routines ({r['removed']} stale files pruned); {cfg_note}{err_note}{big_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
