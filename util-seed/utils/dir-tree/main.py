# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""dir-tree — list a directory tree to a bounded depth (no shell needed).

usage: gu dir-tree <root> [--depth <n>] [--max <n>] [--json]
calls: (none)
tags: files, listing, meta
net: none
fs: roots

Walks <root> up to --depth levels (default 2), listing dirs and files with
sizes. Skips nothing by default. Caps entries at --max (default 400). Data on
stdout; diagnostics on stderr.

AN UNREADABLE DIRECTORY IS REPORTED, NEVER COUNTED AS EMPTY. A directory the walk
cannot list (permission denied, gone mid-walk, or outside this util's sandbox roots)
is named on stderr and in the result's `errors` list. If NOTHING could be listed the
exit is 1 — a root outside the run's granted fs roots used to read as `entries: 0`,
exit 0, which is indistinguishable from an empty directory and sent the caller looking
for files that were there all along."""
import argparse
import json
import os
import sys


def run(root, depth=2, max_n=400):
    root = os.path.abspath(root)
    entries = []
    errors = []

    def note(exc):
        """os.walk swallows every listing error unless it is handed one of these."""
        errors.append({"path": getattr(exc, "filename", None) or root,
                       "error": exc.strerror or str(exc)})

    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, onerror=note):
        cur_depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if cur_depth > depth:
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                sz = -1
            entries.append({"path": os.path.relpath(fp, root),
                            "type": "file", "size": sz})
            if len(entries) >= max_n:
                return {"root": root, "depth": depth, "truncated": True,
                        "count": len(entries), "entries": entries, "errors": errors}
        # record dirs at this level too
        dirnames.sort()
        for dn in dirnames:
            entries.append({"path": os.path.relpath(os.path.join(dirpath, dn), root),
                            "type": "dir", "size": 0})
            if len(entries) >= max_n:
                return {"root": root, "depth": depth, "truncated": True,
                        "count": len(entries), "entries": entries, "errors": errors}
    return {"root": root, "depth": depth, "truncated": False,
            "count": len(entries), "entries": entries, "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "a", "b"))
        with open(os.path.join(d, "a", "f.txt"), "w") as f:
            f.write("hi")
        res = run(d, depth=2)
        paths = {e["path"] for e in res["entries"]}
        assert "a" in paths, paths
        assert os.path.join("a", "f.txt") in paths, paths
        assert res["errors"] == [], res["errors"]
        # an unlistable root is an ERROR, not an empty directory (R1767)
        blind = run(os.path.join(d, "nope"))
        assert blind["count"] == 0 and blind["errors"], blind
        assert blind["errors"][0]["path"].endswith("nope"), blind["errors"]
        print("selftest: ok", file=sys.stderr)
        return

    if not args.root:
        print("error: root required", file=sys.stderr)
        sys.exit(2)
    try:
        res = run(args.root, depth=args.depth, max_n=args.max)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    for err in res["errors"]:
        print(f"warning: cannot list {err['path']}: {err['error']}", file=sys.stderr)
    if res["errors"] and res["count"] == 0:
        print("error: nothing could be listed under "
              f"{res['root']} — the directory is unreadable, not empty. If the path is "
              "right, it is outside the fs roots this run granted: ask for a read root "
              "covering it, or list a directory inside one.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"root: {res['root']}  entries: {res['count']}  "
              f"truncated: {res['truncated']}")
        for e in res["entries"]:
            tag = "d" if e["type"] == "dir" else "f"
            print(f"  {tag} {e['size']:>9} {e['path']}")


if __name__ == "__main__":
    main()
