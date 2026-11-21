"""What the RECOMMENDER is told and what it keeps — the creation draft of 2026-11-20."""


def test_the_recommender_never_proposes_the_routine_s_own_directory(tmp_path, monkeypatch):
    """feeld-webui-maintainer's creation draft (2026-11-20) proposed `fs_write_roots: ["work"]`
    — a path inside the routine's own directory, which every run already writes, spelled
    relative, which the accept refuses. The own-directory entries are dropped before the judge
    sees the list, so the model's real root survives; one the routine already holds is kept."""
    import json
    from types import SimpleNamespace

    from rsched.patterns import recommend
    from rsched.patterns.recommend_roots import without_own_dir

    own = tmp_path / "routines" / "feeld"
    assert without_own_dir(["work", f"{own}/state", str(own), "/srv/feeld", "../x"], own,
                           []) == ["/srv/feeld", "../x"]
    assert without_own_dir([str(own)], own, [str(own)]) == [str(own)]     # already held

    reply = {"changes": [
        {"field": "fs_write_roots", "value_json": json.dumps(["work", "/srv/feeld"]),
         "reason": "r"},
        {"field": "fs_read_roots", "value_json": json.dumps(["state"]), "reason": "r"}]}
    monkeypatch.setattr("rsched.workflows.suggest._ask_json", lambda *a, **k: (reply, ""))
    monkeypatch.setattr(recommend, "_prompt", lambda *a, **k: "")
    server = SimpleNamespace(routines_home=tmp_path / "routines")
    out = recommend.recommend(server, slug="feeld", saved={"fs_read_roots": [],
                                                           "fs_write_roots": []},
                              pattern=None, context="maintain the web UI")
    assert out == {"fs_write_roots": {"value": ["/srv/feeld"], "reason": "r"}}


def test_the_recommender_reads_each_permission_s_effect_before_proposing_it(tmp_path):
    """The same draft proposed `android-actuation` "for observe/navigate" — which that doc's own
    WITHOUT line says needs no permission. The prompt now lists every permission's WITH /
    WITHOUT / WHEN lines and marks the util verbs it reserves as a standing pre-approval."""
    from types import SimpleNamespace

    from rsched.patterns.recommend import _prompt

    perms = tmp_path / "p"
    perms.mkdir()
    (perms / "android-actuation.md").write_text(
        "---\neffect:\n  with: may tap and type on the device\n"
        "  without: observes and navigates the device; every tap is refused at the gate\n"
        "  when: a change must be verified by pressing a button\n"
        "requires:\n  utils: [android:act]\n---\n"
        "# permission: android-actuation — act on an attached Android device\n",
        encoding="utf-8")
    server = SimpleNamespace(permissions_home=perms, rules_home=tmp_path / "r",
                             routines_home=tmp_path / "routines")
    text = _prompt(server, "feeld", {}, None, "maintain the web UI", ["permissions"])
    assert "WITHOUT: observes and navigates the device" in text
    assert "WITH: may tap and type on the device" in text
    assert "RESERVES android:act" in text
    assert "STANDING pre-approval" in text and "its WITHOUT line does not already allow" in text
    assert f"own directory is {tmp_path / 'routines' / 'feeld'}" in text
    assert "ABSOLUTE paths" in text
