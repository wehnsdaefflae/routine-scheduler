"""`GET /api/credential-screen` — what a screen of an admitted path REPORTS: the credential
names it carries, how far the comparison reached, and the digest of the bytes it read (D296,
operator decision 2026-11-19, option a).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from credscreenkit import (
    ALL_VALUES,
    CENTRAL_VALUE,
    MULTILINE_VALUE,
    ROUTINE_TOKEN,
    inside_root,
    screen_one,
    screen_paths,
    seed_stores,
    seeded_client,
)
from rsched import credneedles, credscreen, secrets


@pytest.fixture
def stores(monkeypatch, tmp_path):
    return seed_stores(monkeypatch, tmp_path)


@pytest.fixture
def client(tmp_path, stores, make_routine):
    with seeded_client(tmp_path, make_routine) as c:
        yield c


@pytest.fixture
def inside(tmp_path):
    return inside_root(tmp_path)


# ---------------------------------------------------------------- the load-bearing property

def test_no_credential_value_ever_reaches_the_response(client, inside):
    """A file carrying every seeded credential is reported by NAME, and the body is free of
    all four values — asserted over the raw JSON, not over fields this test enumerates."""
    leaky = inside / "leaky.txt"
    leaky.write_text("\n".join(ALL_VALUES), encoding="utf-8")
    r = screen_paths(client, leaky)
    assert r.status_code == 200
    body = json.dumps(r.json())
    for value in ALL_VALUES:
        assert value not in body, f"a credential VALUE reached the response: {value}"


def test_the_exports_own_token_may_screen(client, inside):
    """The export runs inside a util under the routine token. A screen it cannot reach is no
    screen, so the tier is pinned here rather than left to the route's spelling."""
    clean = inside / "ordinary.txt"
    clean.write_text("nothing secret here at all", encoding="utf-8")
    assert screen_paths(client, clean, token=ROUTINE_TOKEN).status_code == 200


# ------------------------------------------------------------------------- synthetic-secret

def test_a_synthetic_secret_in_a_routine_file_is_found_and_named(client, inside):
    """The base case the whole interface exists for: a credential pasted into a routine's own
    state is reported as carried, under the name it is stored as."""
    leaky = inside / "config.yaml.proposed"
    leaky.write_text(f"token: {CENTRAL_VALUE}\n", encoding="utf-8")
    result = screen_one(client, leaky)
    assert result["disposition"] == "carries"
    assert any("CENTRAL_API_KEY" in name for name in result["carries"]), result


def test_a_file_without_any_stored_credential_is_clean_with_its_digest(client, inside):
    """`clean` is a real verdict, and it is bound to bytes: the digest is of exactly what was
    read, so a caller can prove it publishes the file that was screened."""
    ordinary = inside / "notes.md"
    ordinary.write_text("a note about nothing", encoding="utf-8")
    result = screen_one(client, ordinary)
    assert result["disposition"] == "clean", result
    assert result["carries"] == []
    assert result["digest"] == hashlib.sha256(ordinary.read_bytes()).hexdigest()


def test_a_short_store_value_is_never_a_needle(client, inside, stores):
    """The engine's redaction floor (8 chars) is the screen's too: masking every "true" or
    "8080" protects nothing a guess could not reach and would flag every file. It is also what
    keeps the screen from being a search oracle — no prefix of a value ever matches."""
    secrets.set_secret("TINY_KEY", "abc")
    f = inside / "mentions-abc.txt"
    f.write_text("abc appears here", encoding="utf-8")
    assert screen_one(client, f)["disposition"] == "clean"


# --------------------------------------------------------------------------- escaped-secret

def test_a_json_escaped_credential_is_found(client, inside):
    """A multi-line private key stored in a .json file is escaped, so a byte match over the
    verbatim spelling alone would never see it — and that file would be mirrored off-box."""
    leaky = inside / "cache.json"
    leaky.write_text(json.dumps({"pem": MULTILINE_VALUE}), encoding="utf-8")
    result = screen_one(client, leaky)
    assert result["disposition"] == "carries"
    assert any("MY_PRIVATE_KEY" in name for name in result["carries"]), result


# ---------------------------------------------------------------------------- boundary-size

def test_a_credential_straddling_a_chunk_boundary_is_found(client, inside, monkeypatch):
    """The failure a chunked reader has by default: a 40-byte token split across a read
    boundary is in neither chunk. The overlap is `longest needle - 1` for exactly this."""
    monkeypatch.setattr(credscreen, "CHUNK_BYTES", 64)
    leaky = inside / "straddle.bin"
    half = 64 - len(CENTRAL_VALUE) // 2
    leaky.write_bytes(b"x" * half + CENTRAL_VALUE.encode() + b"y" * 64)
    result = screen_one(client, leaky)
    assert result["disposition"] == "carries", result


def test_a_file_past_the_byte_bound_is_truncated_and_never_clean(client, inside, monkeypatch):
    """A screen must bound what it reads, and a bounded read has not seen the whole file — so
    the verdict is `unknown`, never `clean`. A mirror that published on a truncated `clean`
    would publish exactly the oversize files nothing has ever compared."""
    monkeypatch.setattr(credscreen, "MAX_SCREEN_BYTES", 128)
    big = inside / "big.bin"
    big.write_bytes(b"z" * 4096)
    result = screen_one(client, big)
    assert result["truncated"] is True
    assert result["disposition"] == "unknown"
    assert result["bytes_screened"] == 128


def test_the_bound_is_above_the_exports_own_exclusion():
    """The export skips files >= 10 MiB, so those are the ones never compared by value. A
    screen bounded BELOW that cutoff could not help with any of them."""
    assert credscreen.MAX_SCREEN_BYTES > 10 * 1024 * 1024


# ------------------------------------------------------------------------------- the race

def test_a_file_replaced_while_it_was_read_is_reported_changed(inside, stores, monkeypatch):
    """The TOCTOU the decision names: the verdict belongs to the bytes that were hashed. A file
    swapped under the read is `changed`, and a changed file is never clean — otherwise a
    caller publishes bytes nobody screened."""
    target = inside / "swapped.txt"
    target.write_text("original content here", encoding="utf-8")
    roots = (inside.parents[1].resolve(),)
    real_scan = credscreen._scan

    def swap_then_scan(fd, needles):
        out = real_scan(fd, needles)
        target.write_text(f"replaced with {CENTRAL_VALUE}", encoding="utf-8")
        return out

    monkeypatch.setattr(credscreen, "_scan", swap_then_scan)
    result = credscreen.screen(target, roots)
    assert result.changed is True
    assert result.disposition == "unknown"


# ---------------------------------------------------------------------------- partial-source

def test_an_unreadable_store_makes_coverage_partial_and_forbids_clean(inside, stores,
                                                                      monkeypatch):
    """The defect that made D296 necessary, in the daemon's own terms: when a source cannot be
    read, an empty finding list means "nothing was compared", not "nothing is there". So
    coverage is `partial` and the disposition is `unknown` — a screen may never launder a
    denial into a clean bill."""
    def denied(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(secrets, "load_secrets", denied)
    ordinary = inside / "plain.txt"
    ordinary.write_text("nothing of note", encoding="utf-8")
    result = credscreen.screen(ordinary, (inside.parents[1].resolve(),))
    assert result.coverage == "partial"
    assert "central_store" in result.unreadable_sources
    assert result.disposition == "unknown"


def test_a_denied_store_directory_is_unreadable_not_empty(tmp_path, stores):
    """A store directory nobody can enter must count as UNREADABLE, never as empty."""
    scoped = stores / "secrets.d"
    assert scoped.is_dir()                       # seeded by the fixture, readable until now
    scoped.chmod(0o000)
    try:
        needles = credneedles.collect(config_path=tmp_path / "config.yaml")
        assert needles.read["scoped_stores"] == "unreadable", needles.read
        assert needles.coverage == "partial"
        assert "PermissionError" in needles.denied["scoped_stores"]
        # and the readable sources are still read: one denial must not sink the rest
        assert needles.read["central_store"] is True, needles.read
    finally:
        scoped.chmod(0o755)


def test_a_denied_parent_of_every_store_is_unreadable_too(tmp_path, stores):
    """The coarser denial — the whole config dir hidden, which is what the util's sandbox does
    to it — must also read as unreadable rather than as a clean empty instance."""
    stores.chmod(0o000)
    try:
        needles = credneedles.collect(config_path=tmp_path / "config.yaml")
        assert needles.coverage == "partial"
        assert set(needles.unreadable()) >= {"central_store", "scoped_stores",
                                             "oauth_connections"}, needles.read
    finally:
        stores.chmod(0o755)


def test_the_coverage_statement_names_sources_and_no_values(client, inside):
    """The caller needs to tell an empty finding list over FULL coverage from one over partial
    — that distinction is the honest contract D171 could not offer."""
    ordinary = inside / "plain.txt"
    ordinary.write_text("nothing of note", encoding="utf-8")
    sources = screen_paths(client, ordinary).json()["sources"]
    assert sources["coverage"] in ("full", "partial")
    assert set(sources) == {"coverage", "unreadable", "denied", "credentials_matched_against"}
    assert isinstance(sources["credentials_matched_against"], int)


# ----------------------------------------------------------------------------------- pruning

def test_a_missing_path_is_unknown_and_never_absent(client, inside):
    """A discovery failure read as confirmed absence is how a mirror prunes a file nobody
    screened. A path the daemon could not open is `unknown` with its reason — never a verdict
    that something is not there."""
    result = screen_one(client, inside / "not-here.txt")
    assert result["disposition"] == "unknown"
    assert result["error"]
    assert result["carries"] == []


def test_an_unreadable_file_is_unknown_with_its_reason(client, inside):
    """Same rule for a file that exists and cannot be read: the caller must be able to tell
    'screened and clean' from 'could not look'."""
    locked = inside / "locked.txt"
    locked.write_text("x" * 20, encoding="utf-8")
    locked.chmod(0o000)
    try:
        result = screen_one(client, locked)
    finally:
        locked.chmod(0o644)
    assert result["disposition"] == "unknown"
    assert "PermissionError" in result["error"]


def test_a_batch_reads_the_stores_once_and_keeps_going_past_a_refusal(client, inside,
                                                                      tmp_path):
    """A mirror screens a file list, and one bad path in it must not cost the screening of
    every other file — nor N re-reads of every credential store."""
    good = inside / "a.txt"
    good.write_text("fine", encoding="utf-8")
    body = screen_paths(client, good, tmp_path / "outside.txt", inside / "b.txt").json()
    assert [r["path"] for r in body["screened"]] == [str(good), str(inside / "b.txt")]
    assert len(body["refused"]) == 1


def test_naming_no_path_is_a_refusal_not_an_empty_clean_report(client):
    assert screen_paths(client).status_code == 400
