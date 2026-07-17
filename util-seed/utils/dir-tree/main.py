# /// script
# dependencies = []
# ///
"""dir-tree — list a directory tree to a bounded depth (routines have no shell).

usage: gu dir-tree ROOT [--depth N] [--max N] [--all] [--json]
calls: (none)
secrets: (none)
tags: files, listing, meta
net: none

The routine-safe replacement for `ls`/`find`: prints each entry as an indented name
(directories with a trailing /), sorted, depth-first, bounded by --depth (default 2) and
--max entries (default 500, so a huge tree can't flood a transcript). Dot-entries are
skipped unless --all. With --json emits [{path, dir, size}] relative to ROOT. --selftest
builds a small tree in a temp dir and asserts listing, depth bound and dot-skipping —
fully offline."""

import argparse
import json
import sys
import tempfile
from pathlib import Path


def walk(root: Path, depth: int, max_entries: int, show_all: bool) -> list[dict]:
    """Depth-first bounded listing: [{path (rel, posix), dir, size}], sorted per level."""
    out: list[dict] = []

    def rec(d: Path, level: int) -> None:
        if level > depth or len(out) >= max_entries:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except OSError:
            return
        for p in entries:
            if len(out) >= max_entries:
                return
            if not show_all and p.name.startswith("."):
                continue
            is_dir = p.is_dir()
            out.append({"path": p.relative_to(root).as_posix(), "dir": is_dir,
                        "size": 0 if is_dir else p.stat().st_size})
            if is_dir:
                rec(p, level + 1)

    rec(root, 1)
    return out


def render(items: list[dict], truncated: bool) -> str:
    lines = []
    for it in items:
        indent = "  " * it["path"].count("/")
        name = it["path"].rsplit("/", 1)[-1]
        lines.append(f"{indent}{name}/" if it["dir"] else f"{indent}{name} ({it['size']}B)")
    if truncated:
        lines.append("… (truncated — raise --max or lower --depth)")
    return "\n".join(lines) if lines else "(empty)"


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "b-dir" / "deep" / "deeper").mkdir(parents=True)
        (root / "b-dir" / "deep" / "deeper" / "too-deep.txt").write_text("x")
        (root / "b-dir" / "f.txt").write_text("hello")
        (root / "a.txt").write_text("hi")
        (root / ".hidden").write_text("secret")
        items = walk(root, depth=2, max_entries=500, show_all=False)
        paths = [i["path"] for i in items]
        assert paths == ["b-dir", "b-dir/deep", "b-dir/f.txt", "a.txt"], paths  # dirs first, sorted
        assert not any(".hidden" in p for p in paths)                    # dot-entries skipped
        assert not any("deeper" in p for p in paths)                     # depth bound holds
        all_items = walk(root, depth=1, max_entries=500, show_all=True)
        assert ".hidden" in [i["path"] for i in all_items]
        capped = walk(root, depth=3, max_entries=2, show_all=False)
        assert len(capped) == 2                                          # entry cap holds
        assert "f.txt (5B)" in render(items, False)
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu dir-tree",
                                description="List a directory tree to a bounded depth.")
    p.add_argument("root", nargs="?", help="directory to list")
    p.add_argument("--depth", type=int, default=2, help="max depth (default 2)")
    p.add_argument("--max", type=int, default=500, dest="max_entries",
                   help="max entries (default 500)")
    p.add_argument("--all", action="store_true", help="include dot-entries")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.root:
        p.error("provide ROOT (the directory to list)")
    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1
    items = walk(root, args.depth, args.max_entries, args.all)
    truncated = len(items) >= args.max_entries
    print(json.dumps({"root": str(root), "entries": items, "truncated": truncated})
          if args.json else render(items, truncated))
    return 0


if __name__ == "__main__":
    sys.exit(main())
