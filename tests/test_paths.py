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
