"""The scheduler's A2UI catalog obeys the rules that make it a CATALOG rather than a JSON file —
D310 step 2, the operator's `a-rich-plus`: our components EXTEND the v1.0 Basic Catalog instead of
replacing it, so a renderer resolves a component through its `@catalogId` and a validator checks
our UI trees exactly as it checks basic's; these guards hold the four structural rules the v1.0
specification states for any catalog (UAX #31 entity names, the strict top-level/`$defs`
boundary, `ComponentId`/`ChildList` typing for every structural link, and the reserved `Surface`
name) plus the two this repo adds for itself — every component the catalog declares is reachable
through `anyComponent`, and nothing here duplicates a component the Basic Catalog already has,
because a second `Text` would make the two catalogs disagree about the same word.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CATALOG_PATH = Path(__file__).resolve().parents[1] / "static" / "a2ui" / "scheduler-catalog.json"

#: The spec's canonical entity-name expression, narrowed to ASCII: UAX #31 allows the whole
#: `XID_Start`/`XID_Continue` range, and `re` cannot express those classes without a dependency,
#: so this is stricter than the spec rather than looser — every name we write is ASCII.
ENTITY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: What the Basic Catalog carries, so ours can be checked for not re-declaring any of it. Read
#: from the spec's published catalog, kept beside this test's own subject rather than inferred.
BASIC_COMPONENTS = frozenset({
    "Text", "Image", "Icon", "Video", "AudioPlayer", "Row", "Column", "List", "Card", "Tabs",
    "Modal", "Divider", "Button", "TextField", "CheckBox", "ChoicePicker", "Slider",
    "DateTimeInput",
})

#: The only external schema a catalog may reference, and the definitions inside it that a
#: component property may point at (the spec lists exactly these).
COMMON = "common_types.json"
ALLOWED_COMMON = frozenset({
    "ComponentId", "Child", "ChildList", "DynamicString", "DynamicNumber", "DynamicBoolean",
    "DynamicStringList", "DynamicValue", "Action", "AccessibilityAttributes", "CheckRule",
    "Extensions", "Checkable", "Surface", "ValidationResult",
})


@pytest.fixture(scope="module")
def catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _refs(node: object):
    """Every `$ref` string anywhere under `node`."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def test_the_catalog_declares_the_identity_a_renderer_resolves_it_by(catalog):
    """A component reaches our components by naming this catalogId in its `@catalogId`, and the
    spec asks for `$id` and `catalogId` to be the same URI so JSON Schema tooling and A2UI
    catalog negotiation agree about which document they are holding."""
    assert catalog["catalogId"] == catalog["$id"]
    assert catalog["protocolVersion"] == "1.0"
    assert catalog["catalogId"].startswith("https://")


def test_every_entity_name_obeys_the_naming_rules(catalog):
    """Component, function and property names cross renderer SDKs, parsers and code generators,
    so the spec requires UAX #31 variable names: no hyphens, no spaces, no leading digit."""
    names = [*catalog["components"], *catalog.get("functions", {})]
    for component in catalog["components"].values():
        names.extend(component.get("properties", {}))
    for function in catalog.get("functions", {}).values():
        names.extend(function.get("properties", {}))
    bad = [name for name in names if not ENTITY_RE.match(name)]
    assert bad == [], f"names violating the entity rules: {bad}"


def test_the_reserved_surface_name_is_not_taken(catalog):
    """`Surface` is the canonical container `createSurface` instantiates; a catalog that declared
    a component of that name would shadow the mount point of every surface."""
    assert "Surface" not in catalog["components"]


def test_components_live_at_the_top_level_and_only_the_two_unions_are_in_defs(catalog):
    """The spec's strict boundary: component and function schemas are declared under the
    top-level `components`/`functions` keys, and `$defs` holds only what is referenced
    externally — `anyComponent` and `anyFunction` — plus catalog-scoped leaf types."""
    assert set(catalog["$defs"]) >= {"anyComponent", "anyFunction"}
    for name, schema in catalog["$defs"].items():
        if name in ("anyComponent", "anyFunction"):
            continue
        assert "properties" not in schema or "component" not in schema.get("properties", {}), (
            f"$defs/{name} looks like a component; components belong at the top level")


def test_every_declared_component_is_reachable_through_any_component(catalog):
    """`anyComponent` is what the envelope schema validates against, so a component missing from
    it is a component no agent may legally emit — declared, documented, and unusable."""
    reachable = {ref.removeprefix("#/components/") for ref in _refs(catalog["$defs"]["anyComponent"])}
    assert reachable == set(catalog["components"]), (
        f"declared {sorted(catalog['components'])}, reachable {sorted(reachable)}")
    functions = {ref.removeprefix("#/functions/") for ref in _refs(catalog["$defs"]["anyFunction"])}
    assert functions == set(catalog.get("functions", {}))


def test_a_structural_link_is_typed_so_a_validator_can_follow_it(catalog):
    """A property holding another component's id MUST `$ref` `ComponentId`, and a list of
    children MUST `$ref` `ChildList`: validators decide what is a structural link by looking for
    exactly those refs, so a raw `type: string` id is read as static text and the tree goes
    unchecked. Our components take their content from the data model rather than naming children,
    so this asserts the contrapositive — no property smuggles a component id as a bare string."""
    for name, component in catalog["components"].items():
        for prop, schema in component.get("properties", {}).items():
            if prop == "component":
                continue
            # The protocol decides what a property MEANS from the type it refs, so each of these
            # names carries a required ref. `action` is here because a bare `type: object` is
            # accepted by every JSON validator and then dispatches nothing: the renderer looks
            # for the Action ref to know a click has a destination at all.
            required_ref = {"child": ("Child", "ChildList"), "children": ("Child", "ChildList"),
                            "componentId": ("ComponentId",), "action": ("Action",)}.get(prop)
            if required_ref:
                target = schema.get("$ref", "")
                assert target.endswith(required_ref), (
                    f"{name}.{prop} must $ref {'/'.join(required_ref)} for a validator to follow "
                    f"it, but is typed {target!r}")


def test_every_external_ref_points_into_common_types(catalog):
    """A catalog may reference the standard types in `common_types.json` and nothing else — a
    `$ref` to any other document is unresolvable for a renderer that holds only the protocol."""
    for ref in _refs(catalog):
        if ref.startswith("#/"):
            continue
        schema, _, pointer = ref.partition("#")
        assert schema == COMMON, f"external $ref outside {COMMON}: {ref}"
        assert pointer.removeprefix("/$defs/") in ALLOWED_COMMON, f"unknown common type: {ref}"


def test_nothing_here_re_declares_a_basic_catalog_component(catalog):
    """The operator chose to EXTEND the Basic Catalog, not replace it (`a-rich-plus`), so a
    component of ours sharing a basic name would make the two catalogs disagree about the same
    word while both are mixed into one surface — and the renderer resolves per component, so the
    disagreement would be invisible until a tree rendered wrongly."""
    clash = BASIC_COMPONENTS & set(catalog["components"])
    assert clash == set(), f"re-declares Basic Catalog components: {sorted(clash)}"


def test_every_component_says_what_it_is_for(catalog):
    """The catalog is read by a model composing a surface, and `instructions` plus the
    descriptions are the whole of what it is told; an undescribed component is one it will
    either misuse or ignore."""
    assert catalog["instructions"].strip()
    for name, component in catalog["components"].items():
        assert component.get("description", "").strip(), f"{name} has no description"
        assert "component" in component.get("required", []), f"{name} does not require `component`"
        for prop, schema in component["properties"].items():
            if prop == "component":
                continue
            assert schema.get("description", "").strip(), f"{name}.{prop} has no description"
