# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""instance-export — mirror this instance's routines + sanitized config into the library repo tree.

usage: gu instance-export DEST [--routines-home PATH] [--config PATH] [--json]
calls: (none)
secrets: (none)
tags: sync, backup, meta
net: outbound
fs: roots

Everything the instance acquires syncs to ONE repo — this util stages the instance-owned part
into that repo's working tree (DEST, normally ~/.local/share/routine-scheduler-libraries, which
already holds workflows/, rules/, permissions/, playbooks/, utils/): (a) every routine under
--routines-home (default ~/routines) into DEST/routines/<slug>/, minus transient run state (runs/,
.git/, inbox/, questions/, status.json — routine.yaml, main.md, stages/, scripts/, state/,
LEDGER.md all stay); (b) the server config (default ~/.config/routine-scheduler/config.yaml)
into DEST/config/config.yaml with every credential value replaced by REDACTED — any key that
is or ends in `token`, `api_key`, `secret` or `password` (so `routine_token` too), parsed as
YAML, never regexed, plus the password of any `scheme://user:password@host` URL in a value.
Idempotent and rsync-like: files gone from the source are deleted from DEST. An unreadable file
or directory (permission-denied mounts etc.) is SKIPPED and recorded in `errors` rather than
aborting the whole export. Two guards keep the mirror pushable: a routine that is a git repo is
enumerated through ITS OWN git view (`git ls-files --cached --others --exclude-standard`), so
whatever the routine's .gitignore keeps out of its repo — a .secrets/ folder, mnt/, .venv —
never reaches the mirror either; and any file at or over MAX_FILE_BYTES (10 MiB) is never
copied and is pruned from DEST if a previous run copied it, listed in `oversize` (GitHub
refuses a 100 MB blob outright and a 223 MB state inventory once blocked every push for days).

NOTHING IT WRITES CARRIES A CREDENTIAL THE INSTANCE HOLDS, wherever in a routine's tree it
sits. Before writing anything the export reads every credential stored beside --config: the
config's own credential values, the central Secrets store (`secrets.env`) and every routine's
own (`secrets.d/<slug>.env`), the OAuth connections (`connections.json`) and every routine's
webhook tokens. A routine's `routine.yaml` has its webhook trigger tokens REDACTED — the one
credential the scheduler writes into it (a run gate's `*_secret` fields NAME secrets and hold
none) — listed in `redacted`. Any other file containing one of those values —
verbatim or JSON-escaped — is WITHHELD: never written, pruned from DEST if an earlier run
copied it, and listed in `withheld` with the NAME of what it carries (never the value) and `was_mirrored`
when an earlier export had put it in DEST (that credential has left the machine). A
withheld file is a defect of the routine that keeps it (a full copy of the config in its
state, a token pasted into a script), so the mirror carries no doctored copy that would hide
it — the owner removes the credential and the next export mirrors the file again. The Secrets
store keeps plain settings beside credentials (addresses, hosts, user names), so a store value
counts as a credential when its name — or, inside a JSON-map value, its field — says so
(CREDENTIAL_NAME_RE), or when it spans lines (a private key) — unless it is a filesystem path,
which says where a credential lives rather than holding one (SFTP_SOURCES' `key`); a value
under 8 characters (the engine's own redaction floor) is too likely to be an ordinary word to
match on. The export
FAILS CLOSED: a config or store it cannot read or parse refuses the whole export, because it
cannot vouch for a mirror whose credentials it cannot see. Run it right before `git sync` on
DEST. --selftest builds a fake instance in a temp dir (a permission-denied subdirectory, a
git-ignored secret, an oversize file, a routine keeping a copy of the config, a webhook token
pasted into a script) and asserts exclusions, redaction, withholding, pruning and the refusals
— fully offline."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

EXCLUDE = {"runs", ".git", "inbox", "questions", "status.json",
           ".venv", ".util_outputs", "mnt"}   # transient run state / generated caches / external mount points
#: A config key whose value is a credential: `token`, `routine_token` (R94's second bearer tier,
#: which bootstrap writes into every config.yaml), an endpoint's `api_key` — and by the same
#: rule any `*_token`, `*_secret` or `*_password` a later config grows. A SUFFIX rule on purpose:
#: the exact-name set {token, api_key} let `routine_token` through into a mirror that is pushed.
#: Also the rule for connections.json (an OAuth grant's `access_token` / `refresh_token`), whose
#: keys the scheduler writes. NOT routine.yaml's: there a run gate's `password_secret` holds the
#: NAME of a secret, and the one credential is a webhook trigger's `token` (_trigger_tokens).
SECRET_KEY_RE = re.compile(r"(?:^|_)(?:token|api_key|secret|password)$")
#: A Secrets-store NAME, or a field inside a JSON-map value, that holds a credential. The store's
#: names are the operator's own and it keeps plain settings beside credentials (GMAIL_ADDRESS,
#: NNTP_USER, ZULIP_SITE — values that sit in hundreds of routine files by design), so matching
#: every store value would withhold a routine's whole working record; the name is what says
#: which value grants access. Broader than SECRET_KEY_RE because operators name credentials
#: `OPENROUTER_UTIL_KEY`, `NNTP_PASS`, `TELEGRAM_API_HASH` and `DEEPL_API`.
CREDENTIAL_NAME_RE = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PWD|HASH|API|AUTH|CREDENTIAL)S?$", re.IGNORECASE)
URL_PASSWORD_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)([^\s/@]+)(@)", re.IGNORECASE)
MIN_SECRET_CHARS = 8                  # the engine's REDACT_MIN_CHARS (rsched/captured_output.py)
SECRETS_FILE = "secrets.env"          # the central store (rsched/secrets.py)
SCOPED_DIR = "secrets.d"              # one <slug>.env per routine (rsched/secrets.py)
CONNECTIONS_FILE = "connections.json"  # the OAuth grants (rsched/oauth/store.py)
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


def _decode_store_value(raw: str) -> str:
    """rsched/secrets.py's reading of one value: a double-quoted value is JSON-decoded (the
    multi-line escape a pasted private key is stored in); anything else is stripped of
    whitespace and simple wrapping quotes."""
    s = raw.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        try:
            decoded = json.loads(s)
            if isinstance(decoded, str):
                return decoded
        except ValueError:
            pass
    return s.strip('"').strip("'")


def read_store(path: Path) -> dict[str, str]:
    """One Secrets-store file → {NAME: value}, parsed like the scheduler's own reader. A
    missing file is an empty store; an unreadable one raises (the export fails closed)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key, value = s.split("=", 1)
            key = key.strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                out[key] = _decode_store_value(value)
    return out


def _keyed_values(obj, name_re: re.Pattern, prefix: str = "") -> list[tuple[str, str]]:
    """(dotted key path, value) for every scalar under a key `name_re` names a credential, plus
    the password of every `scheme://user:password@host` URL in any string value."""
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if (isinstance(key, str) and name_re.search(key) and not isinstance(val, bool)
                    and isinstance(val, (str, int, float))):
                found.append((path, str(val)))
            else:
                found += _keyed_values(val, name_re, path)
    elif isinstance(obj, list):
        for item in obj:
            found += _keyed_values(item, name_re, prefix)
    elif isinstance(obj, str):
        found += [(f"{prefix} (password in a URL)", m.group(2))
                  for m in URL_PASSWORD_RE.finditer(obj)]
    return found


def _store_credentials(store: dict[str, str], owner: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name, value in store.items():
        if CREDENTIAL_NAME_RE.search(name) or "\n" in value.strip():
            found.append((f"{owner} {name}", value))
        if value.strip().startswith("{"):
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            found += [(f"{owner} {name}.{path}", leaf)
                      for path, leaf in _keyed_values(parsed, CREDENTIAL_NAME_RE)]
    # a path says where a credential lives (a key file); the file is the secret, not its name
    return [(label, value) for label, value in found if not value.startswith(("/", "~/"))]


def _trigger_tokens(routine: object) -> list[tuple[int, str]]:
    """(index, token) of every webhook trigger in a parsed routine.yaml — the URL token that is
    the hook's only auth (rsched/triggers.py `new_webhook_trigger`)."""
    triggers = routine.get("triggers") if isinstance(routine, dict) else None
    return [(i, str(t["token"])) for i, t in enumerate(triggers if isinstance(triggers, list) else [])
            if isinstance(t, dict) and str(t.get("token") or "").strip()]


def known_credentials(config_path: Path, routines_home: Path) -> dict[str, list[str]]:
    """value → the names it is stored under, for every credential this instance holds: the
    config's own, both Secrets-store scopes, the OAuth connections (all beside the config, as
    the scheduler keeps them) and every routine's webhook tokens. Raises when the config or a
    store cannot be read or parsed — a mirror whose credentials cannot be seen is not exported.
    """
    if not config_path.is_file():
        raise ValueError(f"{config_path} not found — the export reads the instance's credentials "
                         "from it and refuses to mirror anything it cannot check")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"{config_path} is not a YAML mapping")
    base = config_path.parent
    found = [(f"config {path}", value) for path, value in _keyed_values(config, SECRET_KEY_RE)]
    found += _store_credentials(read_store(base / SECRETS_FILE), "secret")
    scoped = base / SCOPED_DIR
    if scoped.is_dir():
        for store in sorted(scoped.glob("*.env")):
            found += _store_credentials(read_store(store), f"routine {store.stem}'s secret")
    connections = base / CONNECTIONS_FILE
    if connections.exists():
        grants = json.loads(connections.read_text(encoding="utf-8"))
        found += [(f"connection {path}", value)
                  for path, value in _keyed_values(grants, SECRET_KEY_RE)]
    if routines_home.is_dir():
        for routine_yaml in sorted(routines_home.glob("*/routine.yaml")):
            slug = routine_yaml.parent.name
            if slug.startswith("."):
                continue
            try:
                data = yaml.safe_load(routine_yaml.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                continue            # its own copy is withheld by export_routines
            found += [(f"routine {slug}'s webhook token", token)
                      for _, token in _trigger_tokens(data)]
    known: dict[str, list[str]] = {}
    for label, value in found:
        if len(value) >= MIN_SECRET_CHARS and value != "REDACTED":
            known.setdefault(value, []).append(label)
    return known


def _needles(known: dict[str, list[str]]) -> list[tuple[bytes, list[str]]]:
    """Each credential as the bytes a file would hold it in: verbatim, and JSON-escaped when
    that differs (a multi-line key inside a .json or .jsonl file)."""
    out = []
    for value, labels in known.items():
        forms = {value, json.dumps(value)[1:-1]}
        out += [(form.encode("utf-8"), labels) for form in forms]
    return out


def carried(data: bytes, needles: list[tuple[bytes, list[str]]]) -> list[str]:
    """The names of every known credential `data` contains — never the values."""
    return sorted({label for needle, labels in needles if needle in data for label in labels})


def export_routines(routines_home: Path, dest_routines: Path,
                    needles: list[tuple[bytes, list[str]]]) -> dict:
    """Mirror each routine's persistent tree into dest_routines/<slug>/ (copy + prune)."""
    exported, skipped = [], []
    desired: set[Path] = set()                        # rel-to-dest_routines paths that should exist
    slugs: set[str] = set()
    all_errors: list[str] = []
    oversize: list[dict] = []
    withheld: list[dict] = []
    redacted: list[dict] = []

    def keep_unread(rel: Path) -> None:
        """Keep DEST's copy on a transient read fault — never prune blind — unless that copy
        itself carries a credential."""
        dst = dest_routines / rel
        found = carried(dst.read_bytes(), needles) if dst.is_file() else []
        if found:
            withheld.append({"path": str(rel), "carries": found, "was_mirrored": True})
        else:
            desired.add(rel)

    def withhold(rel: Path, found: list[str]) -> None:
        """Never written and NOT in `desired`, so a copy an earlier run made is pruned — and
        `was_mirrored` says so: that credential has already left the machine."""
        withheld.append({"path": str(rel), "carries": found,
                         "was_mirrored": (dest_routines / rel).is_file()})

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
                    keep_unread(rel)
                    continue
                if size >= MAX_FILE_BYTES:
                    # never mirrored, and NOT in `desired`, so a copy a previous run made is pruned
                    oversize.append({"path": str(rel), "bytes": size})
                    continue
                try:
                    data = src.read_bytes()
                except OSError as exc:
                    all_errors.append(f"{src}: {exc}")
                    keep_unread(rel)
                    continue
                if src == rdir / "routine.yaml":
                    data, hits = _redact_routine_yaml(data)
                    if hits is None:
                        withhold(rel, ["an unparseable routine.yaml, whose credentials "
                                       "cannot be redacted"])
                        continue
                    if hits:
                        redacted.append({"path": str(rel), "values": hits})
                found = carried(data, needles)
                if found:
                    withhold(rel, found)
                    continue
                desired.add(rel)
                dst = dest_routines / rel
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
            "slugs": sorted(slugs), "errors": all_errors, "oversize": oversize,
            "withheld": withheld, "redacted": redacted}


def _redact_routine_yaml(data: bytes) -> tuple[bytes, int | None]:
    """A routine.yaml with its webhook trigger tokens REDACTED. Returns the bytes to mirror and
    the redaction count; the original bytes when there was nothing to redact, and a count of
    None when the file does not parse."""
    try:
        parsed = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return data, None
    tokens = _trigger_tokens(parsed)
    if not tokens:
        return data, 0
    for i, _ in tokens:
        parsed["triggers"][i]["token"] = "REDACTED"
    hits = len(tokens)
    return yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True).encode("utf-8"), hits


def _scrub_url(value: str) -> tuple[str, int]:
    return URL_PASSWORD_RE.subn(r"\1REDACTED\3", value)


def redact(obj):
    """Recursively blank secret values: any mapping entry whose key SECRET_KEY_RE names a
    credential and whose value is non-empty becomes REDACTED (empty stays empty — it honestly
    says 'nothing was set'), and so does the password of any URL in a string value. Returns the
    hit count and mutates in place."""
    hits = 0
    if isinstance(obj, dict):
        for key, val in obj.items():
            if (isinstance(key, str) and SECRET_KEY_RE.search(key) and not isinstance(val, bool)
                    and isinstance(val, (str, int, float)) and str(val).strip()):
                obj[key] = "REDACTED"
                hits += 1
            elif isinstance(val, str):
                obj[key], n = _scrub_url(val)
                hits += n
            else:
                hits += redact(val)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i], n = _scrub_url(item)
                hits += n
            else:
                hits += redact(item)
    return hits


def export_config(config_path: Path, dest_dir: Path,
                  needles: list[tuple[bytes, list[str]]]) -> dict:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))   # known_credentials parsed it
    redacted = redact(data)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    out = dest_dir / "config.yaml"
    found = carried(text.encode("utf-8"), needles)
    if found:                         # a credential under a key that does not say so
        out.unlink(missing_ok=True)
        return {"exported": False, "reason": "it still carries a credential after redaction",
                "carries": found}
    dest_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return {"exported": True, "redacted_values": redacted}


def run(dest: str, routines_home: str, config: str) -> dict:
    dest_path = Path(dest).expanduser()
    if not dest_path.is_dir():
        raise ValueError(f"DEST {dest_path} is not a directory (clone/create the library repo first)")
    config_path, home = Path(config).expanduser(), Path(routines_home).expanduser()
    needles = _needles(known_credentials(config_path, home))   # raises before anything is written
    routines = export_routines(home, dest_path / "routines", needles)
    cfg = export_config(config_path, dest_path / "config", needles)
    return {"dest": str(dest_path), "routines": routines, "config": cfg}


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "routines"
        keep = ["routine.yaml", "main.md", "LEDGER.md",
                "stages/one.md", "scripts/helper.py", "state/phase.json"]
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
                           "routine_token: \"rt-live-456\"\n"
                           "endpoints:\n  or:\n    kind: openai\n    api_key: sk-live-123\n"
                           "    key_var: OPENROUTER_API_KEY\n"
                           "  local:\n    kind: openai\n    api_key: \"\"\n"
                           "models:\n  big:\n    context_tokens: 200000\n")
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
            # the second bearer tier is a credential too — and it is in EVERY config.yaml
            assert out_cfg["routine_token"] == "REDACTED", "routine_token reached the mirror"
            assert out_cfg["endpoints"]["or"]["api_key"] == "REDACTED"
            assert out_cfg["endpoints"]["local"]["api_key"] == ""         # empty stays empty
            # a NAME of a secret and a token COUNT are not secrets
            assert out_cfg["endpoints"]["or"]["key_var"] == "OPENROUTER_API_KEY"
            assert out_cfg["models"]["big"]["context_tokens"] == 200000
            assert out_cfg["bind"] == "127.0.0.1" and result["config"]["redacted_values"] == 3
            # idempotence + rsync-like pruning: delete at the source → gone from the mirror
            (home / "demo" / "stages" / "one.md").unlink()
            second = run(str(dest), str(home), str(cfg))
            assert not (dest / "routines" / "demo" / "stages").exists()
            assert not (gd / "state" / "stale-big.jsonl").exists()
            demo = second["routines"]["exported"][0]
            assert demo["copied"] == 0 and second["routines"]["removed"] == 2, second["routines"]
        finally:
            restricted.chmod(0o700)                    # restore so TemporaryDirectory cleanup can proceed
        _selftest_credentials(Path(tmp) / "guard")
    print("selftest: ok", file=sys.stderr)
    return 0


def _selftest_credentials(tmp: Path) -> None:
    """No credential the instance holds reaches the mirror, wherever a routine keeps it."""
    conf_dir, home, dest = tmp / "config", tmp / "routines", tmp / "library"
    for d in (conf_dir / SCOPED_DIR, home / "demo" / "state", home / "hooked" / "scripts", dest):
        d.mkdir(parents=True)
    cfg = conf_dir / "config.yaml"
    config_text = ("token: console-token-1\nendpoints:\n  nano:\n    api_key: nano-key-123\n"
                   "library:\n  remote: https://bot:ghp_urltoken123@github.com/o/r.git\n")
    cfg.write_text(config_text)
    pem = "-----BEGIN KEY-----\nAAAAprivatekeybody\n-----END KEY-----\n"
    (conf_dir / SECRETS_FILE).write_text(
        "OPENROUTER_UTIL_KEY=sk-store-abcdef\nNNTP_USER=operator-handle\n"
        'FTP_SOURCES={"box": {"host": "ftp.example.org", "pass": "ftp-pass-9876"}}\n'
        'SFTP_SOURCES={"box": {"user": "u1", "key": "/home/op/.ssh/box_key"}}\n'
        "FAU_PASSWORD=fau-pass-4321\n"
        f"AGENT_KEY={json.dumps(pem)}\nSHORT_PASS=hunter2\n")
    (conf_dir / SCOPED_DIR / "demo.env").write_text("OWN_TOKEN=scoped-tok-12345\n")
    (conf_dir / CONNECTIONS_FILE).write_text(json.dumps(
        {"google:me": {"access_token": "ya29.access-xyz", "refresh_token": "1//refresh-xyz",
                       "label": "personal"}}))
    hook = "hook-token-abcdefgh"
    (home / "hooked" / "routine.yaml").write_text(
        "name: Hooked\ntriggers:\n- id: t-1\n  type: webhook\n  token: " + hook + "\n"
        "budgets:\n  max_total_tokens: -1\n"
        "run_gate:\n  checks:\n  - kind: mail\n    password_secret: FAU_PASSWORD\n")
    (home / "hooked" / "scripts" / "post.py").write_text(f"URL = '/api/hooks/hooked/{hook}'\n")
    leaks = {"state/config.yaml.proposed": config_text,          # the 2026-09-25 incident
             "state/ftp.json": json.dumps({"pass": "ftp-pass-9876"}),
             "state/keys.jsonl": json.dumps({"key": pem}) + "\n",   # JSON-escaped multi-line key
             "notes.md": "pulled with sk-store-abcdef\n",
             "LEDGER.md": "refreshed with 1//refresh-xyz\n",
             "state/scoped.txt": "scoped-tok-12345\n",
             "state/clone.sh": "git clone https://bot:ghp_urltoken123@github.com/o/r.git\n"}
    # a plain setting under a name that is no credential, a too-short value, a secret's NAME,
    # and the path of a key file (where a credential lives, not the credential)
    clean = {"state/identity.md": "signed as operator-handle at ftp.example.org\n",
             "state/short.txt": "hunter2\n", "main.md": "plain recipe\n",
             "state/gate.md": "the gate reads FAU_PASSWORD\n",
             "state/sftp.md": "keyed with /home/op/.ssh/box_key\n"}
    for rel, text in {**leaks, **clean}.items():
        (home / "demo" / rel).write_text(text)
    # what an export made BEFORE this guard: the leaked copy already sits in the mirror
    stale = dest / "routines" / "demo" / "state" / "config.yaml.proposed"
    stale.parent.mkdir(parents=True)
    stale.write_text(config_text)
    result = run(str(dest), str(home), str(cfg))
    r = result["routines"]
    held = {w["path"]: w["carries"] for w in r["withheld"]}
    assert [w["path"] for w in r["withheld"] if w["was_mirrored"]] == [
        "demo/state/config.yaml.proposed"], r["withheld"]
    assert set(held) == {f"demo/{rel}" for rel in leaks} | {"hooked/scripts/post.py"}, held
    assert held["demo/state/config.yaml.proposed"] == [
        "config endpoints.nano.api_key", "config library.remote (password in a URL)",
        "config token"], held
    assert held["demo/state/ftp.json"] == ["secret FTP_SOURCES.box.pass"], held
    assert held["demo/state/keys.jsonl"] == ["secret AGENT_KEY"], held
    assert held["demo/state/scoped.txt"] == ["routine demo's secret OWN_TOKEN"], held
    assert held["demo/LEDGER.md"] == ["connection google:me.refresh_token"], held
    assert held["hooked/scripts/post.py"] == ["routine hooked's webhook token"], held
    for rel in leaks:                                   # never written, and the stale copy pruned
        assert not (dest / "routines" / "demo" / rel).exists(), rel
    assert r["removed"] == 1, r
    for rel in clean:
        assert (dest / "routines" / "demo" / rel).is_file(), rel
    mirrored = yaml.safe_load((dest / "routines" / "hooked" / "routine.yaml").read_text())
    assert mirrored["triggers"][0]["token"] == "REDACTED", mirrored
    assert mirrored["budgets"]["max_total_tokens"] == -1                    # a COUNT, kept
    assert mirrored["run_gate"]["checks"][0]["password_secret"] == "FAU_PASSWORD"   # a NAME, kept
    assert r["redacted"] == [{"path": "hooked/routine.yaml", "values": 1}], r
    out_cfg = yaml.safe_load((dest / "config" / "config.yaml").read_text())
    assert out_cfg["library"]["remote"] == "https://bot:REDACTED@github.com/o/r.git", out_cfg
    assert result["config"]["redacted_values"] == 3, result["config"]
    everything = b"".join(p.read_bytes() for p in dest.rglob("*") if p.is_file())
    for secret in ("console-token-1", "nano-key-123", "ghp_urltoken123", "sk-store-abcdef",
                   "ftp-pass-9876", "privatekeybody", "scoped-tok-12345", "refresh-xyz", hook,
                   "fau-pass-4321"):
        assert secret.encode() not in everything, secret
    # a credential under a config key that does not say so: the config is withheld, not published
    cfg.write_text(config_text + "notify:\n  channel: sk-store-abcdef\n")
    again = run(str(dest), str(home), str(cfg))["config"]
    assert again == {"exported": False, "reason": "it still carries a credential after redaction",
                     "carries": ["secret OPENROUTER_UTIL_KEY"]}, again
    assert not (dest / "config" / "config.yaml").exists()
    # FAILS CLOSED: no config, or an unreadable store, and nothing at all is written
    marker = home / "demo" / "state" / "new.md"
    marker.write_text("new\n")
    for broken in ("missing", "unreadable"):
        if broken == "missing":
            cfg.rename(cfg.with_suffix(".gone"))
        else:
            cfg.with_suffix(".gone").rename(cfg)
            (conf_dir / SECRETS_FILE).chmod(0o000)
            if os.access(conf_dir / SECRETS_FILE, os.R_OK):   # root reads it anyway
                break
        try:
            run(str(dest), str(home), str(cfg))
        except (OSError, ValueError):
            pass
        else:
            raise AssertionError(f"an export with a {broken} credential source ran")
        assert not (dest / "routines" / "demo" / "state" / "new.md").exists(), broken
    (conf_dir / SECRETS_FILE).chmod(0o600)


def main() -> int:
    p = argparse.ArgumentParser(prog="gu instance-export",
                                description="Stage routines + sanitized config into the library repo tree.")
    p.add_argument("dest", nargs="?", help="library repo working tree (the export target)")
    p.add_argument("--routines-home", default="~/routines", help="routines home (default ~/routines)")
    p.add_argument("--config", default="~/.config/routine-scheduler/config.yaml",
                   help="server config to sanitize + export; its Secrets stores sit beside it")
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
        print(summary(result))
    return 0


def summary(result: dict) -> str:
    r, c = result["routines"], result["config"]
    cfg_note = ("config sanitized" if c.get("exported")
                else f"config NOT exported ({c.get('reason')}: {', '.join(c.get('carries', []))})")
    err_note = f"; {len(r['errors'])} errors" if r.get("errors") else ""
    big_note = (f"; {len(r['oversize'])} oversize file(s) NOT mirrored: "
                + ", ".join(o["path"] for o in r["oversize"])) if r.get("oversize") else ""
    held_note = (f"; {len(r['withheld'])} file(s) WITHHELD, each carrying a live credential: "
                 + ", ".join(f"{w['path']} ({', '.join(w['carries'])}"
                             + (" — ALREADY IN THE REPO: rotate it" if w["was_mirrored"] else "")
                             + ")" for w in r["withheld"])
                 ) if r.get("withheld") else ""
    red_note = ("; credentials redacted in " + ", ".join(x["path"] for x in r["redacted"])
                ) if r.get("redacted") else ""
    return (f"exported {len(r['exported'])} routines ({r['removed']} stale files pruned); "
            f"{cfg_note}{err_note}{big_note}{held_note}{red_note}")


if __name__ == "__main__":
    sys.exit(main())
