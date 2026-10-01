"""Gate checks answered from the filesystem and the clock — no network. Jail side, stdlib only."""

from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import subprocess
from pathlib import Path

from kit_common import NET_TIMEOUT_S, UnknownError, baseline, dig, now, read_json_file, since


def _folder(ctx: dict, raw: str) -> Path:
    root = Path(str(raw)).expanduser()
    return root if root.is_absolute() else Path(ctx["routine_dir"]) / root


def files_changed(check: dict, ctx: dict) -> tuple[bool, str, str]:
    """A file is NEW or CHANGED when its inode changed since the last ok run started: its
    ctime as well as its mtime, because mv, `rsync -a`, an unpacked archive and every sync
    client keep the mtime a file had elsewhere — a photo synced in today with last week's
    mtime is new to this folder all the same. (Anything else that touches an inode, a chmod,
    only ever adds a run.)
    """
    pattern = str(check.get("glob") or "*")
    nonempty = bool(check.get("nonempty"))
    cutoff = None if nonempty else since(ctx).timestamp()
    for raw in check["paths"]:
        root = _folder(ctx, raw)
        if not root.is_dir():
            raise UnknownError(f"{root} is not a readable folder")
        try:
            for path in root.rglob(pattern):
                if not path.is_file():
                    continue
                if nonempty:
                    return True, f"{path.name} is waiting in {root}", ""
                st = path.stat()
                if cutoff is not None and max(st.st_mtime, st.st_ctime) > cutoff:
                    return True, f"{path} changed since the last ok run", ""
        except OSError as exc:
            raise UnknownError(f"could not walk {root}: {exc}") from exc
    what = "no matching file" if nonempty else "nothing changed since the last ok run"
    return False, f"{what} under {', '.join(map(str, check['paths']))}", ""


def unpaired_files(check: dict, ctx: dict) -> tuple[bool, str, str]:
    root = _folder(ctx, check["path"])
    if not root.is_dir():
        raise UnknownError(f"{root} is not a readable folder")
    excluded = [str(e) for e in check.get("exclude") or []]
    try:
        names = {p.name for p in root.iterdir() if p.is_file()}
    except OSError as exc:
        raise UnknownError(f"could not list {root}: {exc}") from exc

    def output_of(name: str) -> str:
        stem, dot, suffix = name.rpartition(".")
        stem, suffix = (stem, dot + suffix) if dot else (name, "")
        return str(check["output"]).format(stem=stem, suffix=suffix)

    candidates = {n for n in names if fnmatch.fnmatch(n, str(check["match"]))
                  and not any(fnmatch.fnmatch(n, e) for e in excluded)}
    # An output usually matches the source pattern too (`x [done].mp4` is an .mp4): a file that
    # IS some candidate's output is never itself a source, whatever the exclude list says.
    sources = candidates - {output_of(n) for n in candidates}
    pending = sorted(n for n in sources if output_of(n) not in names)
    if pending:
        return True, f"{len(pending)} source(s) without an output, e.g. {pending[0]}", ""
    return False, f"every source in {root} has its output", ""


def repo_changed(check: dict, ctx: dict) -> tuple[bool, str, str]:
    path = _folder(ctx, check["path"])
    ref = str(check.get("ref") or "HEAD")
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", ref],
                             capture_output=True, text=True, timeout=NET_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UnknownError(f"git could not run in {path}: {exc}") from exc
    head = out.stdout.strip()
    if out.returncode or not head:
        raise UnknownError(f"{path} has no {ref}: {out.stderr.strip()[:200]}")
    try:
        before = baseline(ctx, str(check["id"]))
    except UnknownError as exc:
        return True, str(exc), head
    if head != before:
        return True, f"{path} moved to {head[:10]} since the last ok run", head
    return False, f"{path} is still at {head[:10]}", head


def runs_since(check: dict, ctx: dict) -> tuple[bool, str, str]:
    cutoff = since(ctx)
    wanted = set(check.get("routines") or [])
    me = ctx["routine"]
    usage = Path(ctx["routines_home"]) / ".control" / "workflow-usage.jsonl"
    n = 0
    try:
        with usage.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                who = rec.get("routine")
                if who == me or (wanted and who not in wanted) or rec.get("depth"):
                    continue
                try:
                    when = _dt.datetime.fromisoformat(str(rec.get("ts")))
                except ValueError:
                    continue
                if when > cutoff:
                    n += 1
    except OSError as exc:
        raise UnknownError(f"could not read the fleet's run record: {exc}") from exc
    need = int(check.get("min_runs") or 1)
    if n >= need:
        return True, f"{n} run(s) of other routines since the last ok run", ""
    return False, f"{n} run(s) of other routines since the last ok run (needs {need})", ""


def state(check: dict, ctx: dict) -> tuple[bool, str, str]:
    rel = str(check["file"])
    key = str(check.get("key") or "")
    if not key and not check.get("missing_field") and not check.get("idle_values"):
        return _nonempty(ctx, rel)
    try:
        doc = read_json_file(ctx, rel)
    except FileNotFoundError:
        if check.get("idle_values"):
            raise UnknownError(f"{rel} does not exist, so its {key} is unknown") from None
        return False, f"{rel} does not exist", ""
    value = dig(doc, key)
    key = key or "the whole file"
    if check.get("idle_values"):
        idle = [str(v) for v in check["idle_values"]]
        if str(value) in idle:
            return False, f"{rel} {key} is {value!r}, a waiting value", ""
        return True, f"{rel} {key} is {value!r}, which is not a waiting value", ""
    if check.get("missing_field"):
        return _lacking(value, str(check["missing_field"]), f"{rel} {key}")
    if value:
        size = len(value) if isinstance(value, (list, dict, str)) else 1
        return True, f"{rel} {key} holds {size} pending item(s)", ""
    return False, f"{rel} {key} is empty", ""


def _nonempty(ctx: dict, rel: str) -> tuple[bool, str, str]:
    """No key: the file is work when it exists and holds anything."""
    path = Path(ctx["routine_dir"]) / rel
    if not path.exists():
        return False, f"{rel} does not exist", ""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UnknownError(f"could not read {rel}: {exc}") from exc
    if raw not in ("", "[]", "{}", "null"):
        return True, f"{rel} is not empty", ""
    return False, f"{rel} is empty", ""


def _lacking(value: object, field: str, where: str) -> tuple[bool, str, str]:
    """A list is work while any item lacks `field` (an untested candidate, an unsent draft)."""
    if not isinstance(value, list):
        raise UnknownError(f"{where} is not a list")
    lacking = [v for v in value
               if not (isinstance(v, dict) and v.get(field) not in (None, "", [], {}))]
    if lacking:
        return True, f"{len(lacking)} item(s) in {where} have no {field}", ""
    return False, f"every item in {where} has a {field}", ""


def _date(text: object) -> _dt.date:
    try:
        return _dt.date.fromisoformat(str(text)[:10])
    except ValueError as exc:
        raise UnknownError(f"{text!r} is not a YYYY-MM-DD date") from exc


def dates(check: dict, ctx: dict) -> tuple[bool, str, str]:
    today = now(ctx).date()
    if check.get("from"):
        start = _date(check["from"])
        end = _date(check["until"]) if check.get("until") else None
        if start <= today and (end is None or today <= end):
            return True, f"inside the duty window from {start}" + (
                f" to {end}" if end else ""), ""
    if check.get("file"):
        try:
            doc = read_json_file(ctx, str(check["file"]))
        except FileNotFoundError:
            raise UnknownError(f"{check['file']} does not exist") from None
        early = _dt.timedelta(days=int(check.get("within_days") or 0))
        due = sorted(d for d in (_date(v) for v in _open_dates(doc, check) if v)
                     if d - early <= today)
        if due:
            return True, f"a dated duty is due ({due[0]})", ""
    return False, "no dated duty is due", ""


def _open_dates(doc: object, check: dict) -> list:
    """The dates the file holds — without those of items already DONE, when the check names
    the field that says so (`done_key`) and its finished values (`done_values`). An item whose
    status is anything else, or missing, still counts: only a finished duty is not due.
    """
    key = str(check.get("key") or "")
    done_key = str(check.get("done_key") or "")
    if not done_key:
        value = dig(doc, key)
        return value if isinstance(value, list) else [value]
    items_path, star, field = key.rpartition("*.")
    if not star or not field or "." in field:
        raise UnknownError(f"done_key needs a key of the form <list>.*.<field>, not {key!r}")
    items = dig(doc, items_path.rstrip("."))
    if not isinstance(items, list):
        raise UnknownError(f"{items_path.rstrip('.') or 'the file'} is not a list")
    done = {str(v) for v in check.get("done_values") or []}
    return [item.get(field) for item in items
            if isinstance(item, dict) and str(item.get(done_key)) not in done]


def weekdays(check: dict, ctx: dict) -> tuple[bool, str, str]:
    current = now(ctx)
    if current.weekday() not in set(check["days"]):
        return False, "no standing duty is due today", ""
    try:
        last = since(ctx)
    except UnknownError:
        return True, "a standing duty is due today", ""
    if last.astimezone(current.tzinfo).date() < current.date():
        return True, "a standing duty is due today and no run has done it yet", ""
    return False, "today's standing duty was already done by an ok run", ""


def max_quiet(check: dict, ctx: dict) -> tuple[bool, str, str]:
    quiet = now(ctx) - since(ctx)
    if quiet >= _dt.timedelta(days=int(check["days"])):
        return True, f"no ok run for {quiet.days} day(s) — the backstop is due", ""
    return False, f"the last ok run was {quiet.days} day(s) ago (backstop: {check['days']})", ""
