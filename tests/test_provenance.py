"""Seed ↔ steps provenance: stamping a baseline, then detecting each direction of drift —
the seed edited without recompiling, and the step modules changed under a fixed seed."""

from pathlib import Path

import frontmatter

from rsched.workflows import provenance
from rsched.workflows.adapt import dump_markdown


def _routine(tmp_path: Path, instruction: str, main_body: str, steps: dict[str, str]) -> Path:
    d = tmp_path / "r"
    (d / "steps").mkdir(parents=True)
    (d / "instruction.md").write_text(instruction, encoding="utf-8")
    for name, body in steps.items():
        (d / "steps" / f"{name}.md").write_text(body, encoding="utf-8")
    meta = provenance.stamp({"name": "r"}, routine_dir=d, main_body=main_body, instruction=instruction)
    (d / "main.md").write_text(dump_markdown(meta, main_body), encoding="utf-8")
    return d


def test_fresh_stamp_reads_as_no_drift(tmp_path):
    d = _routine(tmp_path, "do the thing", "entry\nroute to steps", {"a": "step a", "b": "step b"})
    assert provenance.drift(d, "do the thing") == {"tracked": True, "instruction": False, "steps": False}


def test_editing_the_seed_flags_instruction_drift_only(tmp_path):
    d = _routine(tmp_path, "do the thing", "entry", {"a": "step a"})
    (d / "instruction.md").write_text("do a DIFFERENT thing", encoding="utf-8")
    dr = provenance.drift(d, "do a DIFFERENT thing")
    assert dr == {"tracked": True, "instruction": True, "steps": False}


def test_editing_a_step_flags_steps_drift_only(tmp_path):
    d = _routine(tmp_path, "do the thing", "entry", {"a": "step a", "b": "step b"})
    (d / "steps" / "a.md").write_text("step a — HAND EDITED", encoding="utf-8")
    dr = provenance.drift(d, "do the thing")
    assert dr == {"tracked": True, "instruction": False, "steps": True}


def test_adding_or_removing_a_step_flags_steps_drift(tmp_path):
    d = _routine(tmp_path, "do the thing", "entry", {"a": "step a"})
    (d / "steps" / "c.md").write_text("a brand new module", encoding="utf-8")
    assert provenance.drift(d, "do the thing")["steps"] is True
    (d / "steps" / "c.md").unlink()
    (d / "steps" / "a.md").unlink()
    assert provenance.drift(d, "do the thing")["steps"] is True


def test_whitespace_only_change_is_not_drift(tmp_path):
    # compiled_hash strips every part, so a trailing-newline difference never reads as drift
    d = _routine(tmp_path, "do the thing", "entry", {"a": "step a"})
    (d / "steps" / "a.md").write_text("step a\n\n", encoding="utf-8")
    assert provenance.drift(d, "do the thing\n")["instruction"] is False
    assert provenance.drift(d, "do the thing")["steps"] is False


def test_frontmatter_change_is_not_steps_drift(tmp_path):
    # compiled_hash is over the BODY + steps, never the frontmatter (which carries the hash itself)
    d = _routine(tmp_path, "do the thing", "entry", {"a": "step a"})
    meta, body = frontmatter.parse((d / "main.md").read_text(encoding="utf-8"))
    meta["tags"] = ["added-later"]
    (d / "main.md").write_text(dump_markdown(meta, body), encoding="utf-8")
    assert provenance.drift(d, "do the thing")["steps"] is False


def test_untracked_without_a_baseline(tmp_path):
    d = tmp_path / "r"
    (d / "steps").mkdir(parents=True)
    (d / "main.md").write_text("---\nname: r\n---\n\nno hashes here\n", encoding="utf-8")
    assert provenance.drift(d, "anything") == {"tracked": False, "instruction": False, "steps": False}


def test_no_main_md_is_untracked(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    assert provenance.drift(d, "anything")["tracked"] is False
