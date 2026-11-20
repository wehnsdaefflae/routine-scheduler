"""`GET /api/credential-screen` — WHICH PATHS may be screened at all (D296, option a).

The other half of this interface's tests (`test_credential_screen.py`) asks what a screen
REPORTS about an admitted path. This module asks the prior question, and every answer here is a
refusal: a screener reaching outside the instance's own trees, following a link, or blocking on
a pipe is a hazard regardless of how well it matches content.

Every assertion reads the refusal's stable CODE, never its prose. The prose interpolates the
path, and pytest derives `tmp_path` from the test's own name — so `assert "symlink" in reason`
passes against ANY refusal of a path under a test called "...symlink...". That false pass hid a
real finding on 2026-11-20: the symlink case was being refused by root containment, and
O_NOFOLLOW was never exercised by any test.
"""

from __future__ import annotations

import os

import pytest

from credscreenkit import (
    CENTRAL_VALUE,
    inside_root,
    screen_paths,
    seed_stores,
    seeded_client,
)


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


def test_a_symlink_inside_the_roots_is_refused_rather_than_followed(client, inside):
    """O_NOFOLLOW on the final component. The target is INSIDE the roots on purpose: a link
    pointing OUT is already refused by containment, so only this case reaches the open flags —
    and a mirror would otherwise publish the link's target under the link's name.

    Asserted on the refusal CODE, never on its prose: the prose interpolates the path, and
    pytest derives `tmp_path` from the test's own name, so a substring assertion naming the
    reason passes against any refusal of a path under a test called "...symlink...". That
    false pass hid this very finding on 2026-11-20.
    """
    (inside / "target.txt").write_text(CENTRAL_VALUE, encoding="utf-8")
    link = inside / "link.txt"
    link.symlink_to(inside / "target.txt")
    body = screen_paths(client, link).json()
    assert body["screened"] == []
    assert [r["code"] for r in body["refused"]] == ["symlink"], body


def test_a_symlink_out_of_the_roots_is_refused_by_containment(client, inside, tmp_path):
    """Containment is judged on the RESOLVED path, so a link cannot smuggle an outside target
    in — and the refusal says which bound stopped it, not merely that one did."""
    (tmp_path / "outside.txt").write_text(CENTRAL_VALUE, encoding="utf-8")
    link = inside / "escape.txt"
    link.symlink_to(tmp_path / "outside.txt")
    body = screen_paths(client, link).json()
    assert body["screened"] == []
    assert [r["code"] for r in body["refused"]] == ["outside-roots"], body


def test_a_path_outside_every_authorized_root_is_refused(client, tmp_path):
    """A screener with no business reading /etc has none reading anywhere else either."""
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x" * 20, encoding="utf-8")
    body = screen_paths(client, outside).json()
    assert body["screened"] == []
    assert [r["code"] for r in body["refused"]] == ["outside-roots"], body


def test_a_dotdot_path_cannot_climb_out_of_a_root(client, inside, tmp_path):
    """The same bound against the other spelling: `..` is resolved before containment is
    judged, so a path that merely STARTS inside a root does not pass."""
    (tmp_path / "climbed.txt").write_text("x" * 20, encoding="utf-8")
    body = screen_paths(client, inside / ".." / ".." / ".." / "climbed.txt").json()
    assert [r["code"] for r in body["refused"]] == ["outside-roots"], body


def test_a_relative_path_is_refused(client):
    body = screen_paths(client, "state/notes.md").json()
    assert [r["code"] for r in body["refused"]] == ["not-absolute"], body


def test_a_fifo_is_refused_instead_of_blocking_the_daemon(client, inside):
    """A screen that blocks forever on a pipe takes the daemon's thread with it. The type check
    is on the open descriptor, not on a prior stat."""
    fifo = inside / "pipe"
    os.mkfifo(fifo)
    body = screen_paths(client, fifo).json()
    assert body["screened"] == []
    assert [r["code"] for r in body["refused"]] == ["not-a-regular-file"], body


