"""F460 — the PARSE GATE: an edit or overwrite may not leave a structured file unparseable.

The live specimen: a degraded fallback model emitted a schema-VALID edit_file whose
replacement was half plausible JSON and half an echoed observation, and the engine applied it
byte-faithfully; a 59 KB state/shared-state.json stopped parsing and the routine worked from
rubble until a human diagnosed it (R1342/R1344, miz-grant-steward:20260907-054543 turn 75).
"""

from types import SimpleNamespace

from rsched.engine.fileformat import check_after
from rsched.engine.fileops import do_edit_file, do_write_file


def _ctx(tmp_path):
    """The narrowest RunContext stand-in do_edit_file / do_write_file actually touch."""
    return SimpleNamespace(routine=SimpleNamespace(dir=tmp_path, slug="fmt"),
                           write_roots=lambda: [tmp_path], grants=None,
                           run_dir=tmp_path / "runs" / "20260101-000000",
                           root_run_dir=tmp_path / "runs" / "20260101-000000",
                           seen_paths=set())


def test_check_after_only_refuses_a_file_it_breaks(tmp_path):
    good, bad = '{"a": 1}', '{"a": 1'
    assert check_after(tmp_path / "x.json", good, '{"a": 2}') is None
    assert check_after(tmp_path / "x.json", good, bad) is not None
    # a file that was ALREADY broken may be repaired — or made no worse — by an edit
    assert check_after(tmp_path / "x.json", bad, bad) is None
    # only whole-document formats; a .jsonl line or a .md heading is not one
    assert check_after(tmp_path / "x.jsonl", good, bad) is None
    assert check_after(tmp_path / "x.md", good, bad) is None
    assert check_after(tmp_path / "x.yaml", "a: 1\n", "a: 1\n b: [\n") is not None
    assert check_after(tmp_path / "x.toml", "a = 1\n", "a = \n") is not None


def test_edit_file_refuses_an_edit_that_breaks_json_and_leaves_the_file_alone(tmp_path):
    ctx = _ctx(tmp_path)
    path = tmp_path / "state"
    path.mkdir()
    target = path / "shared-state.json"
    original = '{\n  "context_hint": "keep",\n  "n": 1\n}\n'
    target.write_text(original, encoding="utf-8")

    obs = do_edit_file({"kind": "edit_file", "path": "state/shared-state.json",
                        "anchor": '"context_hint": "keep",',
                        "replacement": '"context_hint": ,'}, ctx)

    assert "not be valid JSON" in obs["error"]
    assert "UNCHANGED on disk" in obs["error"]
    assert target.read_text(encoding="utf-8") == original


def test_edit_file_still_applies_a_valid_edit_and_any_edit_to_prose(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "state").mkdir()
    target = tmp_path / "state" / "s.json"
    target.write_text('{"n": 1}\n', encoding="utf-8")
    assert "error" not in do_edit_file(
        {"kind": "edit_file", "path": "state/s.json",
         "anchor": '"n": 1', "replacement": '"n": 2'}, ctx)
    assert target.read_text(encoding="utf-8") == '{"n": 2}\n'

    notes = tmp_path / "state" / "notes.md"
    notes.write_text("# notes\nline\n", encoding="utf-8")
    assert "error" not in do_edit_file(
        {"kind": "edit_file", "path": "state/notes.md",
         "anchor": "line", "replacement": "{{{ not json at all"}, ctx)


def test_write_file_refuses_a_string_overwrite_that_breaks_yaml_but_not_an_append(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "state").mkdir()
    target = tmp_path / "state" / "cfg.yaml"
    target.write_text("a: 1\n", encoding="utf-8")

    obs = do_write_file({"kind": "write_file", "path": "state/cfg.yaml",
                         "content": "a: [1,\n"}, ctx)
    assert "not be valid YAML" in obs["error"]
    assert target.read_text(encoding="utf-8") == "a: 1\n"

    # appends are exempt by construction: the appended text is not the whole document
    assert "error" not in do_write_file({"kind": "write_file", "path": "state/cfg.yaml",
                                         "content": "b: 2\n", "append": True}, ctx)
    # …and structured content is serialized by the engine, so it can never be malformed
    assert "error" not in do_write_file({"kind": "write_file", "path": "state/cfg.json",
                                         "content": {"a": 1}}, ctx)
