# /// script
# dependencies = ["pyyaml"]
# ///
"""instance-export — mirror this instance's routines + sanitized config into the library repo tree.

usage: gu instance-export DEST [--routines-home PATH] [--config PATH] [--json]
calls: (none)
secrets: (none)
tags: sync, backup, meta

Everything the instance acquires syncs to ONE repo — this util stages the instance-owned part
into that repo's working tree (DEST, normally ~/.local/share/routine-scheduler-libraries, which
already holds workflows/, fragments/, utils/): (a) every routine under --routines-home (default
~/routines) into DEST/routines/<slug>/, minus transient run state (runs/, .git/, inbox/,
questions/, status.json — routine.yaml, instruction.md, main.md, steps/, fragments/, state/,
LEDGER.md all stay); (b) the server config (default ~/.config/routine-scheduler/config.yaml)
into DEST/config/config.yaml with every `token` and `api_key` value replaced by REDACTED —
parsed as YAML, never regexed. Idempotent and rsync-like: files gone from the source are deleted
from DEST. Run it right before git-sync on DEST. --selftest builds a fake instance in a temp dir
and asserts exclusions, redaction, and deletion of vanished files — fully offline."""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

EXCLUDE = {"runs", ".git", "inbox", "questions", "status.json"}   # transient run state
REDACT_KEYS = {"token", "api_key"}


def _wanted_files(routine_dir: Path) -> list[Path]:
    """Every file under routine_dir except the transient-state names, at any depth."""
    out = []
    for p in sorted(routine_dir.rglob("*")):
        if any(part in EXCLUDE for part in p.relative_to(routine_dir).parts):
            continue
        if p.is_file():
            out.append(p)
    return out


def export_routines(routines_home: Path, dest_routines: Path) -> dict:
    """Mirror each routine's persistent tree into dest_routines/<slug>/ (copy + prune)."""
    exported, skipped = [], []
    desired: set[Path] = set()                        # rel-to-dest_routines paths that should exist
    slugs: set[str] = set()
    if routines_home.is_dir():
        for rdir in sorted(p for p in routines_home.iterdir() if p.is_dir()):
            if rdir.name.startswith("."):
                skipped.append(rdir.name)
                continue
            slugs.add(rdir.name)
            copied = unchanged = 0
            for src in _wanted_files(rdir):
                rel = Path(rdir.name) / src.relative_to(rdir)
                desired.add(rel)
                dst = dest_routines / rel
                data = src.read_bytes()
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
    return {"exported": exported, "skipped": skipped, "removed": removed, "slugs": sorted(slugs)}


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
        keep = ["routine.yaml", "instruction.md", "main.md", "LEDGER.md",
                "steps/one.md", "fragments/f.md", "state/phase.json"]
        drop = ["status.json", "runs/2026-01-01T00-00-00/transcript.jsonl",
                "inbox/msg.json", "questions/pending/q.json", ".git/HEAD"]
        for rel in keep + drop:
            p = home / "demo" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content of {rel}\n")
        (home / ".control").mkdir(parents=True)
        (home / ".control" / "restart.request").write_text("{}")
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
        out_cfg = yaml.safe_load((dest / "config" / "config.yaml").read_text())
        assert out_cfg["token"] == "REDACTED"
        assert out_cfg["endpoints"]["or"]["api_key"] == "REDACTED"
        assert out_cfg["endpoints"]["local"]["api_key"] == ""         # empty stays empty
        assert out_cfg["bind"] == "127.0.0.1" and result["config"]["redacted_values"] == 2
        # idempotence + rsync-like pruning: delete at the source → gone from the mirror
        (home / "demo" / "steps" / "one.md").unlink()
        second = run(str(dest), str(home), str(cfg))
        assert not (dest / "routines" / "demo" / "steps").exists()
        demo = second["routines"]["exported"][0]
        assert demo["copied"] == 0 and second["routines"]["removed"] == 1, second["routines"]
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
        print(f"exported {len(r['exported'])} routines ({r['removed']} stale files pruned); {cfg_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
