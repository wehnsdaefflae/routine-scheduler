"""MIGRATION(expires=2026-11-30): the one-shot that gives every LIVE rule its `read_when:` and
eight their first assists. The seed sync only adds missing rules, and the live library had
moved on from the seed — so this inserts frontmatter keys and touches no prose."""

from pathlib import Path
from types import SimpleNamespace

from rsched import migrate_rule_read_when as mig
from rsched.library_docs import parse_lenient
from rsched.paths import read_json
from rsched.workflows.lint import lint_rule_text

SEED = Path(__file__).resolve().parents[1] / "library-seed" / "rules"


def _strip(text: str, *keys: str) -> str:
    """`text` without the named top-level frontmatter keys — the live copy before 0.404.0."""
    out, skipping = [], False
    head, sep, body = text[4:].partition("\n---\n")
    for line in head.splitlines():
        if line and not line.startswith((" ", "\t")):
            skipping = line.split(":", 1)[0] in keys
        if not skipping:
            out.append(line)
    return "---\n" + "\n".join(out) + sep + body


def _server(tmp_path):
    lib = tmp_path / "lib"
    (lib / "rules").mkdir(parents=True)
    (tmp_path / "routines").mkdir()
    return SimpleNamespace(routines_home=tmp_path / "routines", libraries_home=lib)


def test_a_rule_gains_its_read_when_and_its_assists_and_keeps_its_prose(tmp_path):
    server = _server(tmp_path)
    seed = (SEED / "web-research.md").read_text(encoding="utf-8")
    live = _strip(seed, "read_when", "assists").replace("Your training is stale",
                                                         "Your training — revised — is stale")
    (server.libraries_home / "rules" / "web-research.md").write_text(live, encoding="utf-8")
    record = mig.run_migration(server)
    assert record["changed"] == {"web-research": ["read_when", "assists"]}
    text = (server.libraries_home / "rules" / "web-research.md").read_text(encoding="utf-8")
    meta = parse_lenient(text)[0]
    assert meta["read_when"] == parse_lenient(seed)[0]["read_when"]
    assert [a["id"] for a in meta["assists"]] == ["on-the-first-search"]
    assert "Your training — revised — is stale" in text       # a revised body is kept as is
    assert lint_rule_text(text, filename="web-research.md") == []
    # once: the record stops a second boot from doing anything at all
    assert mig.run_migration(server) == {}
    assert read_json(server.routines_home / mig.RECORD)["changed"] == record["changed"]


def test_a_live_only_rule_gets_its_line_from_the_migration(tmp_path):
    server = _server(tmp_path)
    (server.libraries_home / "rules" / "resource-economy.md").write_text(
        "---\ntags: [a, b, c]\neffect:\n  with: measures on a slice before the whole job\n"
        "  without: launches full-size passes on hope alone\n"
        "  when: Hold it for routines whose steps process bulk data\n---\n\n"
        "# rule: resource-economy — spend the cheapest thing\n\nbody one\nbody two\n",
        encoding="utf-8")
    record = mig.run_migration(server)
    assert record["changed"] == {"resource-economy": ["read_when"]}
    meta = parse_lenient((server.libraries_home / "rules" / "resource-economy.md")
                         .read_text(encoding="utf-8"))[0]
    assert meta["read_when"] == mig.LIVE_ONLY["resource-economy"]


def test_what_it_cannot_settle_it_names_and_leaves(tmp_path):
    server = _server(tmp_path)
    rules = server.libraries_home / "rules"
    # an operator already gave this one an assists block: theirs outranks the seed's
    seed = (SEED / "correspondence.md").read_text(encoding="utf-8")
    theirs = _strip(seed, "read_when", "assists").replace(
        "\ntags:", "\nassists:\n  - id: mine\n    moment: observation\n"
        "    predicate: repeated-failure\n    payload: remind\n    line: my own line\ntags:")
    (rules / "correspondence.md").write_text(theirs, encoding="utf-8")
    # a rule this release knows nothing about
    (rules / "brand-new.md").write_text("---\ntags: [a, b, c]\n---\n# rule: brand-new — x\n",
                                        encoding="utf-8")
    # a rule that already says when to read it
    kept = (SEED / "fix-the-cause.md").read_text(encoding="utf-8")
    (rules / "fix-the-cause.md").write_text(kept, encoding="utf-8")
    record = mig.run_migration(server)
    assert record["changed"] == {"correspondence": ["read_when"]}
    assert "add once-it-is-sent from the seed by hand" in record["left"]["correspondence"]
    assert "no read_when line" in record["left"]["brand-new"]
    assert "fix-the-cause" not in record["left"]
    assert (rules / "fix-the-cause.md").read_text(encoding="utf-8") == kept
    assert "my own line" in (rules / "correspondence.md").read_text(encoding="utf-8")


# --- the sending permissions (migrate_sending_permissions, same release) ------------------

def test_a_sending_permission_is_replaced_only_while_it_is_the_superseded_seed(tmp_path,
                                                                             monkeypatch):
    import hashlib

    from rsched import migrate_superseded_docs as perms

    def sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    old = "---\ntags: [communication, email, outbound]\n---\n# permission: outbound-mail — x\n"
    monkeypatch.setattr(perms, "SUPERSEDED", {"permissions/outbound-mail.md": sha(old),
                                              "permissions/usenet.md": sha(old),
                                              "permissions/messaging-zulip.md": sha(old)})
    server = _server(tmp_path)
    live = server.libraries_home / "permissions"
    live.mkdir()
    seed = SEED.parent / "permissions"
    (live / "outbound-mail.md").write_text(old, encoding="utf-8")
    (live / "usenet.md").write_text("an operator's own edit\n", encoding="utf-8")
    record = perms.run_migration(server)
    assert record["replaced"] == ["permissions/outbound-mail.md"]
    assert (live / "outbound-mail.md").read_text(encoding="utf-8") == \
        (seed / "outbound-mail.md").read_text(encoding="utf-8")
    assert "edited on this instance" in record["left"]["permissions/usenet.md"]
    assert (live / "usenet.md").read_text(encoding="utf-8") == "an operator's own edit\n"
    assert "unreadable" in record["left"]["permissions/messaging-zulip.md"]   # absent here
    assert perms.run_migration(server) == {}


def test_the_superseded_hashes_are_the_seed_before_this_release():
    """Each hash must match the doc as it shipped in 0.403.0 — which is the seed minus the three
    things this release added (the tag, the outbox block, the conduct line) — or the migration
    would leave every live doc alone."""
    import hashlib

    from rsched import migrate_superseded_docs as perms

    seed = SEED.parent
    for rel, digest in perms.SUPERSEDED.items():
        if not rel.startswith("permissions/"):
            continue        # a rewritten rule body cannot be reconstructed; git holds that one
        slug = rel
        text = (seed / rel).read_text(encoding="utf-8").replace(", as-operator]", "]")
        head, sep, body = text[4:].partition("\n---\n")
        text = "---\n" + head[:head.index("\noutbox:")] + sep + body
        lines = text.split("\n")
        start = next(n for n, ln in enumerate(lines) if ln.startswith("Before EVERY "))
        end = next(n for n in range(start, len(lines)) if lines[n].endswith("again."))
        before = "\n".join(lines[:start] + lines[end + 1:])
        assert hashlib.sha256(before.encode("utf-8")).hexdigest() == digest, slug
