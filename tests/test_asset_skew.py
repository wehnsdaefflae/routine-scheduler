"""F696 — ASSET/PROCESS SKEW: `/static` is served live from disk while every Python object in
this process stays at the build it booted with, so a release landing mid-day puts NEW javascript
in front of an OLD process and nothing says so.

The specimen is the operator's R2546: 'i set up everything on the conversation creation page and
yet once the conversation was started, most of the settings were gone'. Every layer was correct
at HEAD — the composer sent all 17 fields, `ComposerForm` declared all 17 — but the daemon
serving the console had booted at 07:34:53, the two halves landed together at 08:47, and the
conversation was created at 13:42:08 inside that window. The browser's new fields were
accepted-and-discarded.

Why the comparison lives on the SERVER. Nothing in an asset says which build it came from, so
the console cannot name the build it is running; and the process never restarted during the F696
window, so `/api/status`'s `build` never changed either — a console comparing successive readings
of it would have seen nothing all day. The server is the only party that knows BOTH the build it
booted with and the build the checkout serving /static is at NOW, so it compares them and reports
the verdict.

Sibling: `rsched.engine.buildskew` (F677) covers the same release landing mid-RUN against held
Python objects. That half is lazy imports; this half is static assets, which were not versioned
against the process at all.
"""

from __future__ import annotations

import pytest

from conftest import authed_client, make_test_server
from rsched.web import assetskew

BOOTED = "fab9f46 2026-10-19"      # what the process started with
MOVED = "392379e 2026-10-19"       # what the checkout is at once the release lands


@pytest.fixture
def status_of(tmp_path, monkeypatch):
    """`/api/status` from an app that booted at the first stamp and now reads the second.

    The stamp is patched BEFORE the app is built, because the boot half really is read at boot —
    which is the whole asymmetry under test.
    """
    def _status(*stamps: str) -> dict:
        reads = list(stamps)
        monkeypatch.setattr(assetskew, "read_stamp",
                            lambda _repo: reads.pop(0) if len(reads) > 1 else reads[0])
        assetskew.forget_disk_stamp()
        with authed_client(make_test_server(tmp_path)) as c:
            return c.get("/api/status").json()

    yield _status
    assetskew.forget_disk_stamp()


def test_status_reports_the_live_checkout_stamp_beside_the_booted_one(status_of):
    s = status_of(BOOTED, BOOTED)
    assert s["build"] == BOOTED           # the build this process IS
    assert s["disk_build"] == BOOTED      # the build /static is served from NOW
    assert s["build_skew"] is False


def test_status_flags_the_skew_once_the_checkout_moves_under_the_process(status_of):
    """The F696 window itself — and the console shows a banner off this one boolean."""
    s = status_of(BOOTED, MOVED)
    assert s["build"] == BOOTED
    assert s["disk_build"] == MOVED
    assert s["build_skew"] is True


def test_an_unknown_stamp_is_never_reported_as_skew(status_of):
    """A deployment that is not a git checkout has no stamp to compare, and a banner nobody can
    ever clear is worse than no banner."""
    s = status_of(BOOTED, "")
    assert s["disk_build"] == ""
    assert s["build_skew"] is False


def test_the_verdict_is_a_pure_comparison_of_the_two_stamps():
    assert assetskew.stamps_skewed(BOOTED, MOVED) is True
    assert assetskew.stamps_skewed(BOOTED, BOOTED) is False
    assert assetskew.stamps_skewed("", MOVED) is False
    assert assetskew.stamps_skewed(BOOTED, "") is False


def test_the_live_stamp_is_read_once_per_ttl_not_once_per_request(tmp_path, monkeypatch):
    """/api/status is polled by every open tab every 30 s and by Settings' restart watch every
    2 s. One `git log` per poll is a cost this check must not add."""
    reads: list[object] = []

    def counted(repo):
        reads.append(repo)
        return MOVED

    monkeypatch.setattr(assetskew, "read_stamp", counted)
    assetskew.forget_disk_stamp()
    with authed_client(make_test_server(tmp_path)) as c:
        boot_reads = len(reads)                      # the boot stamp is one read, at boot
        for _ in range(5):
            assert c.get("/api/status").json()["disk_build"] == MOVED
        assert len(reads) == boot_reads + 1, f"5 polls cost {len(reads) - boot_reads} git reads"

        assetskew.forget_disk_stamp()                # the TTL elapsing, deterministically
        assert c.get("/api/status").json()["disk_build"] == MOVED
        assert len(reads) == boot_reads + 2
    assetskew.forget_disk_stamp()


def test_read_stamp_answers_empty_without_a_repo():
    """The one shape every caller must survive: no source repo configured at all."""
    assert assetskew.read_stamp(None) == ""


# --- half B: the field the running process does not know ------------------------------------


def test_a_field_this_build_does_not_know_is_a_422_not_a_silent_discard(tmp_path):
    """The other half of F696, and the one that actually lost the operator's work. Pydantic's
    default DROPS an unknown multipart field without a 422 or a log, so a composer newer than
    its server had its new fields accepted-and-discarded. `extra="forbid"` turns that into an
    error naming the field, which the composer shows (`toastError`).
    """
    with authed_client(make_test_server(tmp_path)) as c:
        r = c.post("/api/conversations",
                   data={"text": "hello", "a_field_from_a_newer_build": "x"})

    assert r.status_code == 422, r.text
    assert "a_field_from_a_newer_build" in r.text


def test_every_field_the_composer_sends_is_still_accepted(tmp_path):
    """The guard must refuse only what this build does not know. Every field
    `static/views/conversations-new.js` appends is named here, so forbidding extras can never
    start rejecting the console's own form — which would be a worse failure than the one fixed.
    """
    from rsched.web.api_conversation_create import ComposerForm

    sent = {"text", "playbook", "model", "models", "efforts", "workdir", "fs_read_roots",
            "fs_write_roots", "max_turns", "max_total_turns", "max_wall_clock_min",
            "max_total_tokens", "deliberation", "permissions", "rules", "connections", "files"}

    assert sent <= set(ComposerForm.model_fields), sent - set(ComposerForm.model_fields)
    assert ComposerForm.model_config.get("extra") == "forbid"
