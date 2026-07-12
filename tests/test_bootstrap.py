"""One-time adoption of new default fragments into existing routines (bootstrap.py)."""

import json

import yaml

from rsched.bootstrap import _ADOPTED_MARKER, adopt_fragments

FRAG = "---\ntags: [a, b, c]\n---\n# fragment: memory — test notes\nbody\n"


def _mk_library(tmp_path):
    frags = tmp_path / "libraries" / "fragments"
    frags.mkdir(parents=True)
    (frags / "memory.md").write_text(FRAG, encoding="utf-8")
    return frags


def _set_fragments(routine_dir, slugs):
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["fragments"] = slugs
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_adopt_appends_slug_and_local_copy_once(make_routine, tmp_path):
    d = make_routine(slug="r1")
    _set_fragments(d, ["ask-policy"])
    frags = _mk_library(tmp_path)
    home = tmp_path / "routines"

    assert adopt_fragments(home, frags) == 1
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["fragments"] == ["ask-policy", "memory"]
    assert (d / "fragments" / "memory.md").read_text(encoding="utf-8") == FRAG
    assert json.loads((home / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["memory"]

    # The user deactivates it later (list edit + local copy removed, as the web layer does):
    # adoption is marker-gated, so the next boot must NOT re-add it.
    _set_fragments(d, ["ask-policy"])
    (d / "fragments" / "memory.md").unlink()
    assert adopt_fragments(home, frags) == 0
    assert yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["fragments"] == ["ask-policy"]


def test_adopt_leaves_implicit_default_lists_alone(make_routine, tmp_path):
    # No `fragments:` key = the routine follows DEFAULT_FRAGMENTS (which now includes the
    # slug). Writing an explicit list would SHRINK its active set — only the editable local
    # copy may be added.
    d = make_routine(slug="r2")
    frags = _mk_library(tmp_path)
    assert adopt_fragments(tmp_path / "routines", frags) == 1
    assert "fragments" not in yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert (d / "fragments" / "memory.md").exists()


def test_adopt_skips_dot_dirs_and_already_active(make_routine, tmp_path):
    d = make_routine(slug="r3")
    _set_fragments(d, ["memory"])
    wizard = tmp_path / "routines" / ".wizard-20260712-000000"
    wizard.mkdir(parents=True)
    (wizard / "routine.yaml").write_text("fragments: [ask-policy]\n", encoding="utf-8")
    frags = _mk_library(tmp_path)

    assert adopt_fragments(tmp_path / "routines", frags) == 0
    assert "memory" not in (wizard / "routine.yaml").read_text(encoding="utf-8")
    # already-adopted slugs are still marked done so the next boot skips the scan
    assert json.loads((tmp_path / "routines" / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["memory"]


def test_adopt_waits_for_a_library(make_routine, tmp_path):
    make_routine(slug="r4")
    missing = tmp_path / "libraries" / "fragments"     # never created → no library yet
    assert adopt_fragments(tmp_path / "routines", missing) == 0
    assert not (tmp_path / "routines" / _ADOPTED_MARKER).exists()   # retried next boot


def test_adopt_seeds_missing_library_copy_from_repo_seed(make_routine, tmp_path):
    # An existing library repo predates the fragment: the repo seed is copied in (never
    # overwriting) so the library copy exists as the grants/copy authority.
    d = make_routine(slug="r5")
    _set_fragments(d, [])
    frags = tmp_path / "libraries" / "fragments"
    frags.mkdir(parents=True)

    assert adopt_fragments(tmp_path / "routines", frags) == 1
    assert (frags / "memory.md").exists()
    assert "# fragment: memory" in (d / "fragments" / "memory.md").read_text(encoding="utf-8")
