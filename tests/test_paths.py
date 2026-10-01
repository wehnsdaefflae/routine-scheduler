"""`paths` is the cross-process file seam — the contracts here are about HOW it reads and
writes, not about any one caller."""

from __future__ import annotations

import pytest
import yaml

from rsched import paths


def test_read_yaml_parses_with_libyaml(tmp_path, monkeypatch):
    """The C parser is part of the contract, not an optimisation to drop quietly.

    Measured on the deployment over the 35 live routine.yaml files (87 KB): 583 ms with the
    pure-Python SafeLoader against 59 ms with CSafeLoader. Every config read-modify-write on
    the request path pays it, on a daemon whose slow-request log is already GIL-bound.
    """
    seen: dict[str, object] = {}
    real = yaml.load

    def spy(stream, Loader):  # noqa: N803 — PyYAML's own keyword name
        seen["loader"] = Loader
        return real(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", spy)
    source = tmp_path / "routine.yaml"
    source.write_text("slug: uir\nbudgets:\n  max_turns: 12\n", encoding="utf-8")

    assert paths.read_yaml(source) == {"slug": "uir", "budgets": {"max_turns": 12}}
    assert seen["loader"] is yaml.CSafeLoader


def test_read_yaml_reads_an_empty_document_as_the_default(tmp_path):
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    assert paths.read_yaml(tmp_path / "empty.yaml", {}) == {}


def test_read_yaml_lets_a_broken_file_raise(tmp_path):
    """The asymmetry with read_json: a default handed back for an unparseable file would
    rewrite the operator's hand-broken config FROM that default."""
    (tmp_path / "broken.yaml").write_text("a: [1, 2\nb: }\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        paths.read_yaml(tmp_path / "broken.yaml", {})


def test_append_jsonl_round_trips_through_jsonl_records(tmp_path):
    """The stream pair: one writer, one reader. A value holding U+2028 stays on its line —
    `str.splitlines` would cut it there, so the reader splits on the newline byte alone."""
    path = tmp_path / "deep" / "stream.jsonl"
    rows = [{"n": 1, "text": "a b\u0085c"}, {"n": 2, "text": "plain"}]
    paths.append_jsonl(path, *rows)
    paths.append_jsonl(path)                          # nothing to append writes nothing
    assert paths.read_jsonl(path) == rows
    assert path.read_text(encoding="utf-8").count("\n") == 2


def test_jsonl_records_keeps_every_good_row_around_a_bad_one(tmp_path):
    """Hand-trimmed, restored or torn by a writer that died mid-line: one bad row never hides
    the rest, and a missing file reads as empty."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(b'{"a": 1}\n\n[1, 2]\n"text"\nnot json\n{"b": 2}\n{"c": "\xe2\x80')
    assert paths.read_jsonl(path) == [{"a": 1}, {"b": 2}]
    assert paths.read_jsonl(tmp_path / "missing.jsonl") == []


def test_append_jsonl_hands_the_kernel_one_write(tmp_path, monkeypatch):
    """Several processes append to the same streams; a line reaches the file in ONE write(2),
    the unit an O_APPEND file takes whole, never in buffer-sized pieces another line can land
    between."""
    import os

    writes: list[int] = []
    real = os.write

    def spy(fd, data):
        writes.append(len(data))
        return real(fd, data)

    monkeypatch.setattr(os, "write", spy)
    paths.append_jsonl(tmp_path / "s.jsonl", {"big": "x" * 70_000}, {"next": 1})
    assert len(writes) == 1
