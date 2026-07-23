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
