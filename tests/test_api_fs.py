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
    c, _ = api_client
    assert c.get("/api/fs/list?path=/").json()["parent"] is None


def test_missing_is_404_and_file_is_400(api_client):
    c, tmp = api_client
    assert c.get(f"/api/fs/list?path={tmp / 'nope'}").status_code == 404
    f = tmp / "afile"
    f.write_text("x", encoding="utf-8")
    assert c.get(f"/api/fs/list?path={f}").status_code == 400
