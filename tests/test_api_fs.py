"""Server-side directory browser (/api/fs/list): listing + sort, parent, defaults, errors."""


def test_lists_dirs_first_then_files(api_client):
    c, tmp = api_client
    base = tmp / "browse"
    (base / "beta").mkdir(parents=True)
    (base / "alpha").mkdir()
    (base / "afile.txt").write_text("x", encoding="utf-8")
    (base / "zfile.md").write_text("y", encoding="utf-8")
    data = c.get(f"/api/fs/list?path={base}").json()
    assert data["path"] == str(base.resolve())
    assert data["parent"] == str(base.parent)
    assert not data["truncated"]
    # directories first (case-insensitively sorted), then files
    assert [e["name"] for e in data["entries"]] == ["alpha", "beta", "afile.txt", "zfile.md"]
    assert [e["is_dir"] for e in data["entries"]] == [True, True, False, False]


def test_default_and_tilde_resolve_to_home(api_client, monkeypatch):
    c, tmp = api_client
    home = tmp / "fakehome"
    (home / "sub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # no path -> "~" -> $HOME; an explicit "~" resolves the same
    assert c.get("/api/fs/list").json()["path"] == str(home.resolve())
    assert c.get("/api/fs/list?path=~").json()["path"] == str(home.resolve())


def test_root_has_no_parent(api_client):
    from pathlib import Path

    import pytest
    try:
        next(Path("/").iterdir(), None)
    except PermissionError:   # sandboxed test env (landlock): the endpoint 403s on /
        pytest.skip("this environment cannot list the filesystem root")
    c, _ = api_client
    assert c.get("/api/fs/list?path=/").json()["parent"] is None


def test_missing_is_404_and_file_is_400(api_client):
    c, tmp = api_client
    assert c.get(f"/api/fs/list?path={tmp / 'nope'}").status_code == 404
    f = tmp / "afile"
    f.write_text("x", encoding="utf-8")
    assert c.get(f"/api/fs/list?path={f}").status_code == 400


def test_an_unresolvable_path_is_a_bad_path_not_a_crash(api_client):
    """`Path.resolve()` raises RuntimeError on a symlink loop (Python 3.12) and ValueError on
    an embedded NUL; the guard around it caught OSError alone, so both were a 500."""
    c, tmp = api_client
    loop = tmp / "loop"
    loop.symlink_to(loop)
    for path in (str(loop), f"{tmp}/a\x00b"):
        r = c.get("/api/fs/list", params={"path": path})
        assert r.status_code == 400, (path, r.text)
        assert r.json()["detail"].startswith("bad path")


def test_descending_into_a_dead_mount_is_an_explicit_error(api_client, monkeypatch):
    """F190 marks an entry the daemon cannot stat as an `unreadable` DIRECTORY, so the picker
    descends into it and gets an explicit error. A dead network mount raises ENOTCONN from
    every stat — `Path.exists()` re-raises that errno — so descending was a 500 instead."""
    import errno
    from pathlib import Path

    c, tmp = api_client
    dead = tmp / "mnt" / "share"
    dead.mkdir(parents=True)
    real_stat, real_iterdir = Path.stat, Path.iterdir

    def gone(path):
        raise OSError(errno.ENOTCONN, "Transport endpoint is not connected", str(path))

    monkeypatch.setattr(Path, "stat", lambda self, *a, **k: gone(self) if self == dead
                        else real_stat(self, *a, **k))
    monkeypatch.setattr(Path, "iterdir", lambda self: gone(self) if self == dead
                        else real_iterdir(self))
    r = c.get("/api/fs/list", params={"path": str(dead)})
    assert r.status_code == 502, r.text
    assert "not connected" in r.json()["detail"]


def test_credential_stores_are_not_browsable(api_client, monkeypatch):
    """The picker must not hand credential-store layouts (secrets.env, key files, .mounts)
    to a bearer holder — the sandbox works to keep exactly these invisible to runs."""
    client, tmp = api_client
    cfg_dir = tmp / "config-home"
    (cfg_dir / ".mounts").mkdir(parents=True)
    (cfg_dir / "secrets.env").write_text("K=v", encoding="utf-8")
    monkeypatch.setattr("rsched.paths.config_file", lambda: cfg_dir / "config.yaml")
    r = client.get("/api/fs/list", params={"path": str(cfg_dir)})
    assert r.status_code == 403 and "credentials" in r.json()["detail"]
    # an ORDINARY 403 — an authorized caller refused a specific resource. It must NOT carry
    # the tier marker, or the console would drop a perfectly good token and re-gate on it
    assert "www-authenticate" not in r.headers
    assert client.get("/api/fs/list",
                      params={"path": str(cfg_dir / ".mounts")}).status_code == 403


def test_fs_list_requires_bearer(api_client):
    c, _ = api_client
    r = c.get("/api/fs/list", headers={"Authorization": ""})
    assert r.status_code == 401


def test_fs_list_truncates_at_max_entries(api_client, monkeypatch):
    from rsched.web import api_fs

    c, tmp = api_client
    base = tmp / "many"
    base.mkdir()
    for n in range(5):
        (base / f"f{n}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(api_fs, "MAX_ENTRIES", 3)
    data = c.get(f"/api/fs/list?path={base}").json()
    assert data["truncated"] is True
    assert len(data["entries"]) == 3


def test_unstatable_entry_is_marked_not_hidden(api_client, monkeypatch):
    """F190: an entry the daemon cannot stat (dead/permission-restricted mount under e.g.
    /mnt) must appear as a MARKED directory — never silently demoted to a file row or
    dropped, which read as 'the directory is empty' in the picker."""
    from pathlib import Path


    c, tmp = api_client
    base = tmp / "mounts"
    (base / "ok").mkdir(parents=True)
    (base / "cifs-share").mkdir()

    real = Path.is_dir

    def fake_is_dir(self):
        if self.name == "cifs-share":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    data = c.get(f"/api/fs/list?path={base}").json()
    by_name = {e["name"]: e for e in data["entries"]}
    assert by_name["cifs-share"]["is_dir"] is True          # still descendable
    assert by_name["cifs-share"].get("unreadable") is True  # and visibly marked
    assert by_name["ok"]["is_dir"] is True and "unreadable" not in by_name["ok"]
