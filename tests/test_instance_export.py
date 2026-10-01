"""The library export (`util-seed/utils/instance-export`, live as `rsched export`) never puts a
credential the instance holds into the mirror library-sync pushes, wherever a routine keeps it.

The util is a standalone script and cannot import the package, so it keeps its own copies of
what the scheduler defines — the Secrets stores' file names and format, the OAuth store's file,
the redaction floor, the key a webhook trigger's token sits under. Its selftest checks its
behaviour; this pins those copies to the scheduler's own, so a store the scheduler moves or a
format it changes fails here instead of turning the guard blind. And it replays the incident
that made the guard: config-optimizer kept a full copy of config.yaml in its `state/`; the export pushed the console token and an endpoint key with it (2026-09-25, found 2026-10-01).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import yaml

from rsched import captured_output, secrets, triggers
from rsched.oauth import store as oauth_store

SOURCE = Path(__file__).resolve().parents[1] / "util-seed/utils/instance-export/main.py"
CONFIG = ('token: "console-token-abcdef"\nroutine_token: "routine-token-abcdef"\n'
          "endpoints:\n  nano:\n    kind: openai\n    api_key: nano-key-abcdef\n"
          "    key_var: NANO_GPT_API_KEY\n")
PEM = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
       "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB\n"
       "-----END OPENSSH PRIVATE KEY-----\n")


@pytest.fixture(scope="module")
def export():
    spec = importlib.util.spec_from_file_location("instance_export", SOURCE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def instance(tmp_path, monkeypatch):
    """A config dir whose Secrets store is written by the scheduler's own writer, a routines
    home and an empty library tree."""
    conf = tmp_path / "config"
    (conf / secrets.SCOPED_DIR).mkdir(parents=True)
    (conf / "config.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(secrets, "secrets_path", lambda: conf / secrets.SECRETS_FILE)
    home, dest = tmp_path / "routines", tmp_path / "library"
    home.mkdir()
    dest.mkdir()
    return conf, home, dest


def test_it_reads_the_stores_where_and_how_the_scheduler_keeps_them(export, instance):
    conf, _, _ = instance
    assert export.SECRETS_FILE == secrets.SECRETS_FILE
    assert export.SCOPED_DIR == secrets.SCOPED_DIR
    assert export.CONNECTIONS_FILE == oauth_store.CONNECTIONS_FILE
    assert export.MIN_SECRET_CHARS == captured_output.REDACT_MIN_CHARS
    secrets.set_secret("AGENT_KEY", PEM)              # the multi-line (JSON-quoted) form
    secrets.set_secret("PLAIN_TOKEN", "plain-token-123")
    secrets.set_routine_secret("demo", "OWN_PASS", "own-pass-123")
    with (conf / secrets.SECRETS_FILE).open("a", encoding="utf-8") as fh:
        fh.write('# a comment\nLEGACY="quoted-legacy"\n')
    for path in (secrets.secrets_path(), secrets.scoped_path("demo")):
        assert export.read_store(path) == secrets._read(path)


def test_a_webhook_trigger_the_scheduler_made_never_reaches_the_mirror(export, instance):
    conf, home, dest = instance
    trigger = triggers.new_webhook_trigger()
    routine = home / "hooked"
    (routine / "scripts").mkdir(parents=True)
    (routine / "routine.yaml").write_text(yaml.safe_dump({"name": "Hooked",
                                                          "triggers": [trigger]}))
    (routine / "scripts" / "post.py").write_text(f"HOOK = '{triggers.hook_path('hooked', trigger)}'\n")
    result = export.run(str(dest), str(home), str(conf / "config.yaml"))["routines"]
    mirrored = yaml.safe_load((dest / "routines" / "hooked" / "routine.yaml").read_text())
    assert mirrored["triggers"][0]["token"] == "REDACTED"
    assert mirrored["triggers"][0]["id"] == trigger["id"]
    assert result["redacted"] == [{"path": "hooked/routine.yaml", "values": 1}]
    assert result["withheld"] == [{"path": "hooked/scripts/post.py", "was_mirrored": False,
                                   "carries": ["routine hooked's webhook token"]}]
    assert not (dest / "routines" / "hooked" / "scripts" / "post.py").exists()


def test_a_copy_of_the_config_in_a_routines_state_is_withheld_and_pruned(export, instance):
    """The incident: the copy an earlier export pushed is pruned from the tree, named with what
    it carries and flagged as already mirrored — and no credential byte is left anywhere."""
    conf, home, dest = instance
    secrets.set_secret("NANO_GPT_API_KEY", "nano-key-abcdef")
    secrets.set_secret("GMAIL_ADDRESS", "operator@example.org")  # a setting, not a credential
    state = home / "config-optimizer" / "state"
    state.mkdir(parents=True)
    (state / "config.yaml.proposed").write_text(CONFIG)
    (state / "notes.md").write_text("mail goes to operator@example.org\n")
    leaked = dest / "routines" / "config-optimizer" / "state" / "config.yaml.proposed"
    leaked.parent.mkdir(parents=True)
    leaked.write_text(CONFIG)
    result = export.run(str(dest), str(home), str(conf / "config.yaml"))
    assert result["routines"]["withheld"] == [{
        "path": "config-optimizer/state/config.yaml.proposed", "was_mirrored": True,
        "carries": ["config endpoints.nano.api_key", "config routine_token", "config token",
                    "secret NANO_GPT_API_KEY"]}]
    assert not leaked.exists()
    assert result["routines"]["removed"] == 1
    assert (dest / "routines" / "config-optimizer" / "state" / "notes.md").is_file()
    assert result["config"] == {"exported": True, "redacted_values": 3}
    mirror = b"".join(p.read_bytes() for p in dest.rglob("*") if p.is_file())
    for value in ("console-token-abcdef", "routine-token-abcdef", "nano-key-abcdef"):
        assert value.encode() not in mirror, value


@pytest.mark.parametrize(("name", "is_credential"), [
    *[(n, True) for n in ("NANO_GPT_API_KEY", "OPENROUTER_UTIL_KEY", "PREDATOR_AGENT_KEY",
                          "CLAUDE_CODE_OAUTH_TOKEN", "GOOGLE_OAUTH_CLIENT_SECRET",
                          "GMAIL_APP_PASSWORD", "NNTP_PASS", "TELEGRAM_API_HASH", "DEEPL_API",
                          "GOOGLE_APPLICATION_CREDENTIALS", "pass", "key", "app_password",
                          "bot_token")],
    *[(n, False) for n in ("GMAIL_ADDRESS", "NNTP_USER", "NNTP_PORT", "ZULIP_SITE", "NTFY_URL",
                           "TELEGRAM_API_ID", "WITHINGS_CLIENT_ID", "GRANTS_AUTH_SOURCE",
                           "host", "user", "address", "channel_id")],
])
def test_a_store_value_is_a_credential_when_its_name_says_so(export, name, is_credential):
    """The store keeps plain settings beside credentials; those settings sit in hundreds of
    routine files (an address in every letter a routine drafts) — matching every store value
    would withhold a routine's whole working record. The names are this instance's own."""
    assert bool(export.CREDENTIAL_NAME_RE.search(name)) is is_credential


def test_a_key_files_path_is_not_the_key(export, instance):
    conf, home, dest = instance
    secrets.set_secret("SFTP_SOURCES", '{"box": {"user": "u1", "key": "/home/op/.ssh/box_key"}}')
    (home / "demo").mkdir()
    (home / "demo" / "notes.md").write_text("keyed with /home/op/.ssh/box_key\n")
    export.run(str(dest), str(home), str(conf / "config.yaml"))
    assert (dest / "routines" / "demo" / "notes.md").is_file()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file anyway")
def test_a_store_it_cannot_read_refuses_the_whole_export(export, instance):
    conf, home, dest = instance
    secrets.set_secret("ANY_TOKEN", "any-token-123")
    (home / "demo").mkdir()
    (home / "demo" / "main.md").write_text("recipe\n")
    secrets.secrets_path().chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            export.run(str(dest), str(home), str(conf / "config.yaml"))
    finally:
        secrets.secrets_path().chmod(0o600)
    assert not any(dest.iterdir()), "nothing may be written when a store cannot be read"
