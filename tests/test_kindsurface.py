"""The action-schema projection: what a run is SHOWN must match what it may DO.

The load-bearing property is completeness — a projected schema may never omit a field an
allowed kind needs, or a legal action becomes unrepresentable. It is checked against
KIND_EXAMPLES (one minimal valid action per kind) rather than a second hand-written map,
so a new kind is covered the moment it gets an example.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from rsched.engine.actions import ALWAYS_KINDS, KIND_EXAMPLES, KIND_FIELDS, KINDS
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.kindsurface import effective_kinds, schema_for_kinds


@pytest.mark.parametrize("kind", KINDS)
def test_projection_still_accepts_that_kinds_own_example(kind):
    """Narrowing to one kind keeps every field that kind uses."""
    schema = schema_for_kinds({kind})
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(KIND_EXAMPLES[kind])


@pytest.mark.parametrize("kind", KINDS)
def test_projection_keeps_every_declared_field_of_an_allowed_kind(kind):
    required, optional = KIND_FIELDS[kind]
    props = schema_for_kinds({kind})["properties"]
    missing = [f for f in (*required, *optional) if f not in props]
    assert not missing, f"{kind}: projection dropped {missing}"
    for universal in ("say", "note", "kind"):
        assert universal in props


def test_always_kinds_survive_every_projection():
    """finish and report are available regardless of the workflow's allowlist, so the
    schema must keep them emittable even when the run allows nothing else."""
    schema = schema_for_kinds({"read_file"})
    assert set(ALWAYS_KINDS) <= set(schema["properties"]["kind"]["enum"])
    jsonschema.Draft202012Validator(schema).validate(KIND_EXAMPLES["finish"])


def test_full_and_none_return_the_schema_unchanged():
    """A run with everything enabled must see byte-identical bytes — the prompt-caching
    contract depends on the composed prefix being stable."""
    assert schema_for_kinds(None) is ACTION_SCHEMA
    assert schema_for_kinds(set(KINDS)) is ACTION_SCHEMA


def test_projection_drops_other_kinds_fields_and_prose():
    schema = schema_for_kinds({"read_file", "util"})
    props = schema["properties"]
    # schedule_run / ask_user / memory_write fields have no business here
    for gone in ("fire_at", "cancel", "question", "mode", "about", "delete",
                 "workflow", "turns", "response_schema"):
        assert gone not in props, f"{gone!r} survived a projection that excludes its kind"
    # `target` DOES survive: `report` is an ALWAYS_KIND and owns it, so every projection
    # carries it — but only with report's clause, never schedule_run's
    assert "target" in props
    assert "schedule_run" not in props["target"]["description"]
    assert "report" in props["target"]["description"]
    assert props["kind"]["enum"] == ["util", "read_file", "list_models", "report", "finish"]
    # the shared `name` description sheds its memory_read / read_rule clauses
    assert "memory_read" not in props["name"]["description"]
    assert "read_rule" not in props["name"]["description"]
    assert "util" in props["name"]["description"]


def test_projection_is_materially_smaller():
    """The point of the exercise: a restricted workflow stops paying for 21 kinds. The
    clarify-instruction allowlist is the real worst case in the library."""
    full = len(json.dumps(ACTION_SCHEMA, indent=1))
    clarify = len(json.dumps(schema_for_kinds(
        {"ask_user", "read_file", "write_file", "finish"}), indent=1))
    # ~46% off at the time of writing; the floor guards the mechanism, not the exact ratio.
    assert clarify < full * 0.6, f"projection saved too little: {clarify} vs {full}"


def test_projection_never_mutates_the_global_schema():
    """Regression: schema_for_kinds filtered the ORIGINAL schema's property specs into
    its deepcopy shell, so the description-trimming loop mutated the shared global —
    every projection permanently trimmed ACTION_SCHEMA for the whole process (cross-run
    contamination in the daemon). The full schema must be byte-identical after any
    projection."""
    before = json.dumps(ACTION_SCHEMA, sort_keys=True)
    schema_for_kinds({"finish"})
    schema_for_kinds({"ask_user", "read_file", "write_file", "finish"})
    assert json.dumps(ACTION_SCHEMA, sort_keys=True) == before


def test_effective_kinds_intersects_allowlist_and_grants():
    class Grants:
        def allows_kind(self, kind):
            return kind != "write_util"

    assert effective_kinds(None, None) == list(KINDS)
    # ALWAYS_KINDS ride along even when the workflow allowlist omits them (list_models
    # joined 0.212.0 — read-only discovery for the per-call model override)
    assert effective_kinds({"read_file"}, None) == ["read_file", "list_models",
                                                   "report", "finish"]
    # a capability-denied kind is dropped even when the workflow permits it
    assert "write_util" not in effective_kinds({"read_file", "write_util"}, Grants())


def test_module_docstring_kind_count_tracks_kinds():
    """The kindsurface docstring cites how many kinds exist ('permit 8 of the N kinds …
    all N in the schema'); that N must equal len(KINDS) or the prose lies about the
    contract (F272: it read 21 while KINDS held 23). Self-updating — no hard-coded count."""
    import re

    import rsched.engine.kindsurface as ks

    doc = ks.__doc__ or ""
    cited = [int(n) for n in re.findall(r"of the (\d+) kinds|all (\d+) in the schema",
                                        doc) for n in n if n]
    assert cited, "kindsurface docstring no longer cites a kind count — update this guard"
    assert all(n == len(KINDS) for n in cited), (
        f"kindsurface docstring cites {cited} kinds but KINDS has {len(KINDS)}")
