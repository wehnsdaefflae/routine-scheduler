"""Canonical path helpers and atomic file IO.

Every cross-process file (status.json, inbox messages, answers, control.json) goes through
atomic_write so a reader never sees a partial file.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml


def expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser()


def config_file() -> Path:
    env = os.environ.get("RSCHED_CONFIG")
    if env:
        return expand(env)
    return expand("~/.config/routine-scheduler/config.yaml")


def repo_root() -> Path:
    """The source checkout's root — where deploy/, library-seed/ and util-seed/ live."""
    return Path(__file__).resolve().parents[2]


def atomic_write(path: str | Path, data: str | bytes, *, mode: int | None = None) -> Path:
    """Write via tmp file + rename in the target directory (same filesystem). A concurrent
    reader sees the old file or the new one, never a partial write. `mode` (permission bits,
    e.g. from a prior `stat().st_mode`) is applied to the new file before the rename — pass it
    when overwriting so the temp file's default 0600 doesn't drop an existing file's bits
    (notably +x); omit it for new files.

    The tmp file's DATA is fsynced before the rename, so a reader that sees the new name
    never sees unflushed bytes. The containing DIRECTORY is deliberately not fsynced: the
    guarantee is concurrent-reader atomicity (old file or new, never a torn write), not
    power-loss durability of the rename itself — a crash that loses the directory entry
    leaves the prior valid file in place, and every consumer here is a cache, telemetry, or
    state a rebooted box re-derives.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmode, encoding = ("wb", None) if isinstance(data, bytes) else ("w", "utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, fmode, encoding=encoding) as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except BaseException:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path: str | Path, obj: object) -> Path:
    return atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | Path, default: object = None) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def append_jsonl(path: str | Path, *rows: object) -> None:
    """Append `rows` to the JSON-lines stream at `path` — the ONE writer of the system's
    append-only streams (the report ledger, the health and usage streams, the admin audit,
    the UI traces, a reaped run's closing transcript event).

    The rows go out in ONE `write(2)` on an O_APPEND descriptor, the unit Linux appends whole:
    every stream has several processes appending at once (the daemon and each engine), and a
    buffered text-mode handle hands a line longer than its buffer to the kernel in pieces that
    another writer's line can land between. Non-ASCII stays as written; every reader splits
    on the newline byte alone (`jsonl_records`), never `str.splitlines`, which also breaks on
    U+2028.
    Raises OSError like any write: best-effort callers catch it where the stream is optional.
    """
    data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    if not data:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        view = memoryview(data)
        while view:                      # a regular file takes it whole; never assume it
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def jsonl_records(text: str) -> list[dict]:
    """The JSON objects of a JSON-lines stream, in order — the ONE reader `append_jsonl` pairs
    with. A blank line, a torn tail, a line that does not parse and a line that parses to
    something other than an object are all skipped: these streams are hand-trimmed, restored
    and appended to by processes that can die mid-line, and one bad row must never hide the
    rest. Splits on the newline byte only, for the reason `append_jsonl` gives.
    """
    out: list[dict] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def read_jsonl(path: str | Path) -> list[dict]:
    """`jsonl_records` of the file at `path`; a missing or unreadable file reads as empty.
    Decoded leniently: a writer that died inside a multi-byte character leaves bytes that
    spoil only its own line, never the whole stream.
    """
    try:
        return jsonl_records(Path(path).read_bytes().decode("utf-8", errors="replace"))
    except OSError:
        return []


def atomic_write_yaml(path: str | Path, obj: object) -> Path:
    """Serialize `obj` as YAML and atomic_write it — the ONE writer for every YAML file this
    system owns (routine.yaml, tuning.yaml, config.yaml).

    The two dump options ARE the reason this exists rather than being spelled at each call
    site. `sort_keys=False` keeps the key order a human wrote: routine.yaml is read and
    hand-edited far more often than it is written, and an alphabetised rewrite would reorder
    the whole file under the user on every web save. `allow_unicode=True` keeps a name with an
    umlaut readable instead of escaped code points. Open-coded at twenty call sites they were one
    forgotten kwarg away from a routine's config silently changing shape depending on which
    module last wrote it — which is not a diff anyone reviews, because nobody wrote it.
    """
    return atomic_write(path, yaml.safe_dump(obj, sort_keys=False, allow_unicode=True))


def read_yaml(path: str | Path, default: object = None) -> Any:
    """Parse a YAML file, reading an EMPTY document as `default` — `yaml.safe_load` returns
    None for an empty file, which is why every call site spelled `or {}` after it.

    Unlike `read_json`, errors PROPAGATE: a missing file raises OSError and a malformed one
    raises yaml.YAMLError. The asymmetry is deliberate. Nearly every YAML read here is the
    first half of a read-modify-write of `routine.yaml`, and a default handed back for an
    unparseable file would rewrite the user's hand-broken config FROM that default, dropping
    every key that failed to parse. That path is reachable, not theoretical: `registry.scan`
    catalogs an unloadable routine.yaml as a disabled routine rather than hiding it, so the
    routine page still offers every editor that writes the file back. The readers that must
    turn a broken file into a PROBLEM STRING rather than an exception (the config loaders)
    catch OSError/yaml.YAMLError around this call and say which one they got.

    Returns `Any` where `read_json` returns `object`: read_json's callers validate an
    untrusted record before touching it, while these callers index a mapping they then hand
    to pydantic, so `object` would buy nothing but an isinstance narrowing at every site.

    CSafeLoader, not `safe_load`: the C parser is the same grammar an order of magnitude
    faster (measured on the deployment — the 35 live routine.yaml files, 87 KB: 583 ms pure
    Python, 59 ms libyaml), and this is on the request path for every config read-modify-write
    as well as the cold catalog scan. No getattr fallback: libyaml is present in both live
    environments, and one that lacks it should fail loudly rather than quietly serve a daemon
    ten times slower on the surface whose slow-request log is already GIL-bound.
    """
    return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=yaml.CSafeLoader) or default


def within(root: Path, candidate: Path) -> bool:
    """True if candidate (resolved) lies inside root (resolved)."""
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def directly_under(path: Path | str, home: Path | str | None) -> bool:
    """True when `path` is a directory DIRECTLY inside `home`, both resolved.

    How run KIND is told everywhere: a conversation, a detached background task and a
    scheduled routine are told apart by the HOME their directory sits in, never by the yaml's
    `kind:` — a detached task's routine.yaml carries none, and the home is where the run
    actually lives. A missing home, or a path that cannot be resolved, is under nothing.
    """
    if home is None:
        return False
    try:
        return Path(path).resolve().parent == Path(home).resolve()
    except (OSError, RuntimeError):     # RuntimeError: a symlink loop
        return False


def resolve_rel(base: Path, rel: str, extra_roots: Sequence[Path] = ()) -> Path:
    """Resolve a path from an action: relative → under base; absolute → must fall inside
    base or one of extra_roots. Raises PermissionError otherwise.
    """
    p = expand(rel)
    candidate = p if p.is_absolute() else (base / p)
    roots = [base, *extra_roots]
    for root in roots:
        if within(root, candidate):
            return candidate.resolve()
    allowed = ", ".join(str(r) for r in roots)
    raise PermissionError(f"path {rel!r} is outside the allowed roots ({allowed})")


def repo_lock_path(home: Path) -> Path:
    """The per-repo commit-lock file for the git repo containing `home`, placed inside the
    repository's git dir so every writer of the SAME work tree — no matter which subdir it
    passes as `home` — agrees on one lock, and the lock is never a file in that tree. A linked
    worktree or a submodule has a `.git` FILE naming its git dir (`gitdir: <path>`); the lock
    goes there, because a dotfile beside it was swept into the tree's next `add -A`. A broken
    `.git` file (naming nothing) or a tree with no repo at all gets a dotfile at its root.
    The `git` util's runner keeps a copy of this rule (util-seed/utils/git, `_repo_lock`).
    """
    cur = Path(home).resolve()
    for d in (cur, *cur.parents):
        g = d / ".git"
        if g.is_dir():
            return g / "rsched-commit.lock"
        if g.is_file():
            named = _named_git_dir(g)
            return named / "rsched-commit.lock" if named else d / ".rsched-commit.lock"
    return cur / ".rsched-commit.lock"


def _named_git_dir(dot_git_file: Path) -> Path | None:
    """The git dir a `.git` FILE names (`gitdir: <path>`, relative to the file's own dir), or
    None when the file names none that exists.
    """
    try:
        text = dot_git_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    head, sep, target = text.strip().partition("gitdir:")
    if not sep or head.strip():
        return None
    named = (dot_git_file.parent / target.strip()).resolve()
    return named if named.is_dir() else None


@contextmanager
def file_lock(lock_path: str | Path, *, timeout: float = 30.0,
              poll: float = 0.05) -> Iterator[bool]:
    """Advisory exclusive lock across processes via `fcntl.flock`. Yields True once held,
    or False if `timeout` elapsed without acquiring — in which case the caller proceeds
    BEST-EFFORT (a stale/hung holder must never deadlock a commit; libgit's own subprocess
    timeout bounds the wait regardless) — and `libgit.writing` then removes no git index lock,
    since the holder may be mid-commit. The lock file itself is never written or committed.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= end:
                    break
                time.sleep(poll)
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
