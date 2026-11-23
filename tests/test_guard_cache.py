"""The credential-store guard resolves the stores once per identity, not once per question —
F906's second half: the instrument shipped in 0.588.0 attributed the console's residual
multi-second waits to the `app` span, the self-samples named the frame (`registry.scan` →
`load_config` → `load_routine` → `entities.guarded_roots` → `never_grantable_fs` →
`_spellings` → `Path.resolve`), and one `/api/runs` request over 30 routines was measured
paying 872 `Path.resolve()` calls and about 1.6 s because the 12 credential stores were
re-resolved for every path the guard was asked about; these guards hold the fix to both halves
of its claim — the resolves must fall, AND a store created, replaced or re-pointed after the
first question must still be caught, since a cached guard that goes stale is a credential leak.
"""

from __future__ import annotations

import pathlib

from rsched import entities


def _count_resolves(monkeypatch, work) -> int:
    """How many `Path.resolve()` calls `work` makes. `resolve` is the realpath syscall chain
    the guard spends its time in, so it is the thing the fix is claimed to reduce.
    """
    calls = []
    original = pathlib.Path.resolve

    def counting(self, *args, **kwargs):
        calls.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", counting)
    work()
    monkeypatch.undo()
    return len(calls)


def test_the_stores_are_resolved_once_and_not_once_per_question(tmp_path, monkeypatch):
    """Asking the guard about many paths must not re-resolve the stores for each one."""
    store = tmp_path / "ssh"
    store.mkdir()
    monkeypatch.setattr(entities, "NEVER_GRANTABLE", (str(store),))
    candidates = [str(tmp_path / f"project-{n}") for n in range(20)]
    entities.never_grantable_fs(candidates[0])          # warm, as a live process is

    def ask() -> None:
        for candidate in candidates:
            entities.never_grantable_fs(candidate)

    resolves = _count_resolves(monkeypatch, ask)
    # One per candidate (its own written-vs-resolved comparison, which is the question itself)
    # and none for the stores. Before the cache this was candidates x (1 + stores).
    assert resolves == len(candidates), resolves


def test_a_patched_store_constant_still_reaches_the_guard(tmp_path, monkeypatch):
    """The cache is keyed on `never_grantable_stores()` ITSELF and not only on the stores'
    filesystem identity, so patching the constant — which is how every enforcement-door test
    points the guard at a temporary store — changes the answer rather than being served a stale
    set; both stores here are ABSENT, the one case the identity stamp cannot tell apart (a path
    that cannot be stat'ed stamps like any other), so only the store names in the key separate
    the two configurations, and a pair of existing directories would prove nothing because
    their differing inodes would carry the stamp by themselves.
    """
    first = tmp_path / "ghost-one"
    second = tmp_path / "ghost-two"
    monkeypatch.setattr(entities, "NEVER_GRANTABLE", (str(first),))
    assert entities.never_grantable_fs(first)
    assert not entities.never_grantable_fs(second)
    monkeypatch.setattr(entities, "NEVER_GRANTABLE", (str(second),))
    assert entities.never_grantable_fs(second)
    assert not entities.never_grantable_fs(first)


def test_a_store_repointed_at_another_directory_is_guarded_at_its_new_target(
        tmp_path, monkeypatch):
    """The store itself being a symlink is guarded at its TARGET, and re-pointing that link is
    exactly the state change a cached resolution would miss.
    """
    old = tmp_path / "old-vault"
    new = tmp_path / "new-vault"
    old.mkdir()
    new.mkdir()
    store = tmp_path / "credentials"
    store.symlink_to(old)
    monkeypatch.setattr(entities, "NEVER_GRANTABLE", (str(store),))
    assert entities.never_grantable_fs(old)
    assert not entities.never_grantable_fs(new)
    store.unlink()
    store.symlink_to(new)
    assert entities.never_grantable_fs(new)
    assert not entities.never_grantable_fs(old)


def test_a_directory_above_a_store_becoming_a_link_is_guarded(tmp_path, monkeypatch):
    """The identity stamp covers every component on the way to a store, not the store alone:
    swapping a PARENT directory for a link moves the store without touching its own name.
    """
    real = tmp_path / "home-a"
    (real / "secrets").mkdir(parents=True)
    other = tmp_path / "home-b"
    (other / "secrets").mkdir(parents=True)
    home = tmp_path / "home"
    home.symlink_to(real)
    monkeypatch.setattr(entities, "NEVER_GRANTABLE", (str(home / "secrets"),))
    assert entities.never_grantable_fs(real / "secrets")
    assert not entities.never_grantable_fs(other / "secrets")
    home.unlink()
    home.symlink_to(other)
    assert entities.never_grantable_fs(other / "secrets")
    assert not entities.never_grantable_fs(real / "secrets")
