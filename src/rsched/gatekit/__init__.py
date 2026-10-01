"""GATE CHECKS — declarative, model-free answers to "is there work for this fire?".

A run gate (docs/run-gates.md) decides whether a scheduled fire becomes a run at all. Writing a
Python predicate per routine is what kept gates rare: six scripts in two months, two of them
enabled, one of them unable to read the mailbox it was written for. So a gate is a LIST of CHECKS
in `routine.yaml` — `run_gate.checks` — each one a known question with parameters; a custom
`scripts/admit.py` is one more check (kind `script`) rather than the only way to have a gate.

THE ONE RULE every check obeys: a check may answer "no work" only when it KNOWS there is none.
Anything it cannot establish — a mailbox that refuses the login, a feed that times out, a state
file that does not parse, no earlier run to compare against — is WORK, with the reason naming what
could not be checked. A wrong "run" costs one ordinary fire; a wrong "skip" silently loses work,
which is the one failure a gate must not have. The fire is skipped only when EVERY check answered
"no work".

This package has two halves that never import each other. This module is the VOCABULARY — what
each kind means, its parameters, validation and the jail it needs — imported by the daemon, the
config loader and the console. The checks themselves (`run.py` and the `kit_*` modules) run
INSIDE the gate's Landlock jail as plain scripts, standard library only: the jail mounts this
directory, not the rest of `rsched`.
"""

from __future__ import annotations

from pathlib import Path

#: The package's importable surface is this vocabulary alone. The jail-side scripts are not
#: members: they import their siblings by bare name, so they load only after `run.py` has put
#: this directory on the import path. Anything that lists a package's submodules by importing
#: them (pdoc's Help build does) would otherwise try, and warn, in whatever order it walks.
__all__ = ["DEFAULT_AUTH_SECRET", "ENTRY", "KINDS", "NET_KINDS", "SECRET_PARAMS", "needs_net",
           "paths_named", "secrets_named", "validate"]

#: The file the daemon executes inside the jail.
ENTRY = Path(__file__).resolve().parent / "run.py"

#: The check vocabulary: kind → (one-line meaning, parameter spec). A spec entry is
#: `name: (type, required, help)`. The console renders its forms from this table and
#: `validate()` checks a routine.yaml against it, so a kind cannot be half-added.
KINDS: dict[str, tuple[str, dict[str, tuple[str, bool, str]]]] = {
    "mail": (
        "mail waits in a mailbox — unread, or new since the last run that finished ok",
        {"host": ("str", True, "IMAP server, e.g. faumail.fau.de or imap.gmail.com"),
         "port": ("int", False, "IMAP port (default 993)"),
         "mode": ("str", False, ("`unseen` (default): any unread message; `new`: any message "
                                 "that arrived after the last ok run started")),
         "user_secret": ("secret", False, "secret holding the login name"),
         "password_secret": ("secret", False, "secret holding the password"),
         "accounts_secret": ("secret", False,
                             ("secret holding a JSON account map {name: {email, app_password}} "
                              "— used instead of user_secret/password_secret")),
         "account": ("str", False, "which account of accounts_secret (default: the first)"),
         "folders": ("list", False, ("folders to look in (default INBOX); a special-use "
                                     "flag such as \\All or \\Sent names the folder whatever "
                                     "the server calls it")),
         "senders_file": ("path", False,
                          ("a JSON file in the routine's own directory with `senders` and/or "
                           "`sender_domains` lists — mail FROM those counts")),
         "from_any": ("list", False, "mail whose From contains one of these counts"),
         "subject_any": ("list", False, ("mail whose Subject contains one of these counts — "
                                         "with the two above a WATCH LIST: a message counts "
                                         "when it meets any of them (none given: every "
                                         "message counts)")),
         "from_domains_not": ("list", False,
                              "never count mail from these domains (and their subdomains)")}),
    "hub_feedback": (
        ("feedback waits on the routine's Steward hub page that its last publish has not "
         "consumed"),
        {"project": ("str", True, "the hub project slug the routine publishes"),
         "source": ("str", False, ("which entry of the web-auth secret holds the hub login "
                                   "(default `steward`)")),
         "auth_secret": ("secret", False, ("the secret holding the web logins (default "
                                           "WEB_AUTH_SOURCES)"))}),
    "url_changed": (
        "a web page, feed or API answers differently than it did at the last ok run",
        {"url": ("str", True, "the address to fetch (GET)"),
         "select": ("str", False, ("what to compare: `body` (default), `feed` (the set of "
                                   "RSS/Atom item ids), or `json:<dotted.path>` where `*` maps "
                                   "over a list (e.g. json:data.*.id)")),
         "token_secret": ("secret", False, "secret holding a bearer token for the request"),
         "auth_source": ("str", False, "an entry of the web-auth secret to log in with (Basic)"),
         "auth_secret": ("secret", False, "the web-auth secret (default WEB_AUTH_SOURCES)")}),
    "files_changed": (
        "a file under a folder is new or changed since the last ok run",
        {"paths": ("list", True, ("folders (inside the routine's filesystem roots or its own "
                                  "directory) to look in")),
         "glob": ("str", False, "only files matching this pattern (default: every file)"),
         "nonempty": ("bool", False, ("report work whenever the folder holds ANY matching file "
                                      "(for an intake folder the routine empties)"))}),
    "unpaired_files": (
        "a source file still has no output beside it (a work queue defined by files)",
        {"path": ("path", True, "the folder holding sources and outputs"),
         "match": ("str", True, "which files are sources, e.g. *.mp4"),
         "output": ("str", True, ("the output name for a source, with {stem} and {suffix}, "
                                  "e.g. `{stem} [done]{suffix}`")),
         "exclude": ("list", False, ("patterns that are never sources, e.g. tmp*.mp4 (an "
                                     "output is recognised by its name without being listed)"))}),
    "repo_changed": (
        "a git repository has new commits since the last ok run",
        {"path": ("path", True, "the repository (inside the routine's roots)"),
         "ref": ("str", False, "branch or ref to watch (default HEAD)")}),
    "runs_since": (
        "other routines have run since the last ok run (for routines that review runs)",
        {"min_runs": ("int", False, "how many new runs count as work (default 1)"),
         "routines": ("list", False, ("only these routines (default: every routine but this "
                                      "one)"))}),
    "state": (
        "a file in the routine's own directory says work is waiting",
        {"file": ("path", True, "a path relative to the routine's directory"),
         "key": ("str", False, ("a dotted path inside the JSON file (without it: the file is "
                                "work when it exists and is not empty)")),
         "idle_values": ("list", False, ("the value at `key` means NO work only when it is one "
                                         "of these — any other value is work (e.g. a phase)")),
         "missing_field": ("str", False, ("the value at `key` (the whole file when no key) "
                                          "is a list: work when any item lacks this field "
                                          "(e.g. an untested candidate)"))}),
    "dates": (
        "a dated duty is due",
        {"from": ("str", False, "work every fire from this date on (YYYY-MM-DD)"),
         "until": ("str", False, "…until this date, inclusive (with `from`: a window)"),
         "file": ("path", False, "a JSON file in the routine's directory holding dates"),
         "key": ("str", False, ("a dotted path to a date or a list of dates in that file "
                                "(`*` maps over a list, e.g. obligations.*.due_date)")),
         "within_days": ("int", False, "a file date counts this many days early (default 0)"),
         "done_key": ("str", False, ("the field of each listed item saying it is finished "
                                     "(e.g. status) — needs a key <list>.*.<date field>")),
         "done_values": ("list", False, ("the values of done_key that mean finished — such "
                                         "an item's date is never due"))}),
    "weekdays": (
        "a standing duty is due on these weekdays",
        {"days": ("list", True, ("weekday numbers, 0 = Monday … 6 = Sunday — the first fire on "
                                 "such a day is work"))}),
    "max_quiet": (
        "the last ok run is older than this — a backstop every gate should carry",
        {"days": ("int", True, "at most this many days between ok runs")}),
    "script": (
        "the routine's own custom predicate, scripts/admit.py",
        {}),
}

#: Checks that need the network, plus the parameters whose values name secrets. The daemon
#: builds the jail from these, so a check can never reach further than its kind needs.
NET_KINDS = frozenset({"mail", "url_changed", "hub_feedback"})
SECRET_PARAMS = frozenset({"user_secret", "password_secret", "accounts_secret", "token_secret",
                           "auth_secret"})
DEFAULT_AUTH_SECRET = "WEB_AUTH_SOURCES"   # noqa: S105 — the secret's NAME, never its value


def validate(checks: object) -> list[str]:
    """Problems with a `run_gate.checks` list, as sentences — empty when it is sound.

    Structural only: whether a named secret is granted, or a path lies inside the routine's
    roots, is the daemon's to judge at preparation, because only it holds the grants.
    """
    if checks is None:
        return []
    if not isinstance(checks, list):
        return ["run_gate.checks must be a list"]
    problems: list[str] = []
    seen: set[str] = set()
    for i, check in enumerate(checks):
        where = f"run_gate.checks[{i}]"
        if not isinstance(check, dict):
            problems.append(f"{where} must be a mapping")
            continue
        kind = check.get("kind")
        if kind not in KINDS:
            problems.append(f"{where}: unknown kind {kind!r} (one of {', '.join(KINDS)})")
            continue
        cid = str(check.get("id") or f"c{i + 1}")
        if cid in seen:
            problems.append(f"{where}: duplicate id {cid!r}")
        seen.add(cid)
        spec = KINDS[kind][1]
        problems.extend(f"{where}: {kind} takes no parameter {name!r}"
                        for name in check if name not in ("kind", "id") and name not in spec)
        for name, (typ, required, _help) in spec.items():
            value = check.get(name)
            if value in (None, "", []):
                if required:
                    problems.append(f"{where}: {kind} needs {name!r}")
                continue
            problems.extend(_type_problem(where, name, typ, value))
        problems.extend(_kind_problems(where, kind, check))
    return problems


def _type_problem(where: str, name: str, typ: str, value: object) -> list[str]:
    ok = {"str": isinstance(value, str), "secret": isinstance(value, str),
          "path": isinstance(value, str), "int": isinstance(value, int)
          and not isinstance(value, bool), "bool": isinstance(value, bool),
          "list": isinstance(value, list)}[typ]
    return [] if ok else [f"{where}: {name} must be a {typ}"]


def _kind_problems(where: str, kind: str, check: dict) -> list[str]:
    out: list[str] = []
    if kind == "mail":
        pair = bool(check.get("user_secret")) and bool(check.get("password_secret"))
        if not pair and not check.get("accounts_secret"):
            out.append(f"{where}: mail needs user_secret + password_secret, or accounts_secret")
        if check.get("mode") not in (None, "unseen", "new"):
            out.append(f"{where}: mail.mode is unseen or new")
    if kind == "weekdays" and not all(isinstance(d, int) and 0 <= d <= 6
                                      for d in check.get("days") or []):
        out.append(f"{where}: weekdays.days are numbers 0 (Monday) to 6 (Sunday)")
    if kind == "max_quiet" and isinstance(check.get("days"), int) and check["days"] < 1:
        out.append(f"{where}: max_quiet.days must be at least 1")
    if kind == "url_changed":
        sel = str(check.get("select") or "body")
        if sel not in ("body", "feed") and not sel.startswith("json:"):
            out.append(f"{where}: url_changed.select is body, feed or json:<dotted.path>")
    if kind in ("state", "dates") and str(check.get("file") or "").startswith("/"):
        out.append(f"{where}: {kind}.file is relative to the routine's directory")
    if kind == "dates" and not (check.get("from") or check.get("file")):
        out.append(f"{where}: dates needs `from` (a window) or `file` (dates the run keeps)")
    if kind == "dates" and check.get("done_key") and "*." not in str(check.get("key") or ""):
        out.append(f"{where}: dates.done_key needs a key of the form <list>.*.<date field>")
    if kind == "unpaired_files" and "{stem}" not in str(check.get("output") or ""):
        out.append(f"{where}: unpaired_files.output must contain {{stem}}")
    return out


def secrets_named(checks: list[dict]) -> set[str]:
    """Every secret a check list names — what the daemon must be allowed to inject."""
    named = {str(c[p]) for c in checks for p in SECRET_PARAMS if c.get(p)}
    for c in checks:
        if (c.get("kind") == "hub_feedback" or c.get("auth_source")) and not c.get("auth_secret"):
            named.add(DEFAULT_AUTH_SECRET)
    return named


def needs_net(checks: list[dict]) -> bool:
    return any(c.get("kind") in NET_KINDS for c in checks)


def paths_named(checks: list[dict], routine_dir: Path) -> list[Path]:
    """Absolute paths outside the routine's own directory that checks read — each must lie
    inside a granted root, which the daemon verifies before it mounts anything.
    """
    out: list[Path] = []
    for c in checks:
        kind = c.get("kind")
        raws = ([*c.get("paths", [])] if kind == "files_changed" else
                [c.get("path")] if kind in ("repo_changed", "unpaired_files") else [])
        for raw in raws:
            if raw:
                p = Path(str(raw)).expanduser()
                out.append(p if p.is_absolute() else routine_dir / p)
    return out
