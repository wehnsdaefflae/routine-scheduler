"""The suite's test DOUBLES, read statically with `ast` — the extraction half of the
double-conformance floor. The rules that judge what this module finds, and the design record of
why each rule is shaped the way it is, live in tests/test_double_conformance.py; this module only
reads, and is a helper like tests/class_survey.py, not a test module.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "rsched"
TESTS = REPO / "tests"

#: The two modules that READ doubles instead of building them — this one and the rules file whose
#: fixtures are INPUTS to the detectors. A fixture fed to a detector is not a suite double.
NOT_SUITE = frozenset({"double_survey.py", "test_double_conformance.py"})

#: The convention name an object carries in production parameters AND in test doubles → the
#: real class behind it. Each entry is evidenced: `loop` is F644's own class; `ctx`, `budgets`
#: and `transcript` are the engine objects the doubles at tests/test_live_grants_patch.py:23,
#: tests/test_rules_manage.py:204 and tests/test_child.py:110 impersonate in 4-5-attribute
#: slices, none of them checked against the real class before this test existed.
IMPERSONATED: dict[str, tuple[Path, str]] = {
    "loop": (SRC / "engine" / "loop.py", "EngineLoop"),
    "ctx": (SRC / "engine" / "run_context.py", "RunContext"),
    "budgets": (SRC / "engine" / "budgets_config.py", "Budgets"),
    "transcript": (SRC / "engine" / "transcript.py", "Transcript"),
    # `routine` is the pydantic RoutineConfig, and it is checkable by exactly the same `ast`
    # pass: pydantic POPULATES an instance from a dict, but the SURFACE is declared in the file
    # as ordinary annotated class attributes (`slug: str`, `dir: Path`, …) — measured, the ast
    # read covers all 38 of `model_fields` and nothing is missing from it. It is also the class
    # the F803 defect landed in: a stub defined `instruction` and `task`, which the real config
    # declares nowhere and `extra="ignore"` cannot carry either, so production's probe returned
    # the display name on every rung for weeks behind a green suite.
    "routine": (SRC / "config" / "routine.py", "RoutineConfig"),
}

# STILL EXCLUDED, and why — a silent exclusion is one nobody can argue with. `ServerConfig` is
# declared the same way and is checkable by the same read, but its doubles are `_Cfg`/`_Server`
# stubs built around ONE field each (`browser_view_url`, `desktop_view_url`) assigned in
# `__init__`, not config slices, so it is its own increment with its own census rather than a
# key appended here.

#: Hand-written stub classes → the convention name of what they impersonate (shape 3).
STUBS: dict[str, str] = {
    "_Loop": "loop", "_EngineLoop": "loop",
    "_Ctx": "ctx", "_Context": "ctx", "_RunContext": "ctx",
    "_Budgets": "budgets", "_Transcript": "transcript",
    "_Routine": "routine",
}

#: Where a dataclass must live for `stand_ins()` to claim a namespace impersonates it: the
#: engine's own objects and the endpoint contracts production dispatches through. MEASURED, not
#: chosen — see the SCOPE paragraph in tests/test_double_conformance.py for the counts that
#: rejected the whole of `src/rsched`.
SCOPE: tuple[str, ...] = ("endpoints", "engine")


class Double(NamedTuple):
    """One stand-in for a real class, as found in the suite's source."""

    where: str          # path:lineno
    impersonates: str   # a key of IMPERSONATED
    shape: str          # which of the three recognition shapes matched
    owner: str          # the stub class's name, or "" for a SimpleNamespace
    attrs: tuple[str, ...]


class StandIn(NamedTuple):
    """A `SimpleNamespace` the real dataclass would have constructed from the same keywords."""

    where: str          # path:lineno
    cls: str            # the dataclass it is a stand-in for
    module: str         # where that dataclass is defined, repo-relative
    kwargs: tuple[str, ...]


def py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def parse_file(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _targets(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[ast.expr]:
    raw = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    out: list[ast.expr] = []
    for target in raw:
        out.extend(target.elts if isinstance(target, ast.Tuple | ast.List) else [target])
    return out


def _assignments(tree: ast.AST) -> list[ast.Assign | ast.AnnAssign | ast.AugAssign]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Assign | ast.AnnAssign | ast.AugAssign)]


def _is_namespace(node: ast.expr) -> bool:
    """`SimpleNamespace(...)` — imported by name or reached as `types.SimpleNamespace`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return ((isinstance(func, ast.Name) and func.id == "SimpleNamespace")
            or (isinstance(func, ast.Attribute) and func.attr == "SimpleNamespace"))


def base_name(node: ast.expr) -> str | None:
    """The convention NAME an attribute access hangs off: `loop.x` → "loop", and
    `self.ctx.transcript.x` → "transcript". Production reaches these objects both ways, so the
    surface reader and the exemption guard must resolve them identically — a guard that only
    matched a bare local would have missed every `ctx.transcript.x` in `src/`.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def self_assigned(cls: ast.ClassDef) -> set[str]:
    """Every attribute the class's own methods assign to `self`."""
    return {t.attr for node in _assignments(cls) for t in _targets(node)
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
            and t.value.id == "self"}


def _declared(cls: ast.ClassDef) -> set[str]:
    """Class-level annotations and assignments, plus the names of its methods."""
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.AnnAssign | ast.Assign):
            names |= {t.id for t in _targets(node) if isinstance(t, ast.Name)}
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


@cache
def _collaborator_surface() -> dict[str, frozenset[str]]:
    """`<name>.x = …` anywhere under `src/rsched`, per convention name."""
    found: dict[str, set[str]] = {name: set() for name in IMPERSONATED}
    for path in py_files(SRC):
        for node in _assignments(parse_file(path)):
            for target in _targets(node):
                if isinstance(target, ast.Attribute) and (
                        name := base_name(target.value)) in found:
                    found[str(name)].add(target.attr)
    return {name: frozenset(attrs) for name, attrs in found.items()}


@cache
def surface(name: str) -> frozenset[str]:
    """The real attribute surface of the class `name` impersonates, read from its four places."""
    path, class_name = IMPERSONATED[name]
    tree = parse_file(path)
    cls = next((n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == class_name), None)
    assert cls is not None, f"{class_name} is no longer defined in {path} — fix this map"
    return frozenset(_declared(cls) | self_assigned(cls) | _collaborator_surface()[name])


def _kwargs(call: ast.Call) -> tuple[str, ...]:
    return tuple(sorted(kw.arg for kw in call.keywords if kw.arg))


def doubles_in(tree: ast.Module, where: str) -> list[Double]:
    """Every double in one parsed module, by the three recognition shapes."""
    found: list[Double] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_namespace(node.value):
            assert isinstance(node.value, ast.Call)
            found.extend(Double(f"{where}:{node.lineno}", target.id, "assignment", "",
                                _kwargs(node.value))
                         for target in _targets(node)
                         if isinstance(target, ast.Name) and target.id in IMPERSONATED)
        if _is_namespace(node):
            assert isinstance(node, ast.Call)
            found.extend(Double(f"{where}:{kw.value.lineno}", str(kw.arg), "keyword", "",
                                _kwargs(kw.value))
                         for kw in node.keywords
                         if kw.arg in IMPERSONATED and _is_namespace(kw.value))
        if isinstance(node, ast.ClassDef) and node.name in STUBS:
            found.append(Double(f"{where}:{node.lineno}", STUBS[node.name], "stub class",
                                node.name, tuple(sorted(self_assigned(node)))))
    return found


@cache
def suite_doubles() -> tuple[Double, ...]:
    found: list[Double] = []
    for path in py_files(TESTS):
        if path.name in NOT_SUITE:
            continue
        found.extend(doubles_in(parse_file(path), str(path.relative_to(REPO))))
    return tuple(found)


def _is_dataclass(cls: ast.ClassDef) -> bool:
    """`@dataclass` or `@dataclass(...)`, plain or reached as `dataclasses.dataclass`."""
    for dec in cls.decorator_list:
        func = dec.func if isinstance(dec, ast.Call) else dec
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "dataclass":
            return True
    return False


def _fields(cls: ast.ClassDef) -> tuple[frozenset[str], frozenset[str]]:
    """(every field, the fields that have NO default) from the class body's annotations."""
    every: set[str] = set()
    required: set[str] = set()
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        head = node.annotation
        head = head.value if isinstance(head, ast.Subscript) else head
        if (isinstance(head, ast.Name) and head.id == "ClassVar") or (
                isinstance(head, ast.Attribute) and head.attr == "ClassVar"):
            continue
        every.add(node.target.id)
        if node.value is None:
            required.add(node.target.id)
    return frozenset(every), frozenset(required)


@cache
def scoped_dataclasses() -> tuple[tuple[str, str, frozenset[str], frozenset[str]], ...]:
    """Every dataclass under SCOPE a namespace could stand in for: (name, module, fields, required)."""
    out: list[tuple[str, str, frozenset[str], frozenset[str]]] = []
    for folder in SCOPE:
        for path in py_files(SRC / folder):
            for node in ast.walk(parse_file(path)):
                if not isinstance(node, ast.ClassDef) or not _is_dataclass(node) or node.bases:
                    continue
                every, required = _fields(node)
                if every and required:
                    out.append((node.name, str(path.relative_to(REPO)), every, required))
    return tuple(sorted(out))


def stand_ins_in(tree: ast.AST, where: str) -> list[StandIn]:
    """Every `SimpleNamespace(...)` in one parsed module that a scoped dataclass would have built."""
    found: list[StandIn] = []
    for node in ast.walk(tree):
        if not _is_namespace(node):
            continue
        assert isinstance(node, ast.Call)
        if any(kw.arg is None for kw in node.keywords):
            continue
        kwargs = _kwargs(node)
        if not kwargs:
            continue
        named = frozenset(kwargs)
        hits = [(name, module) for name, module, every, required in scoped_dataclasses()
                if named <= every and required <= named]
        if len(hits) == 1:
            found.append(StandIn(f"{where}:{node.lineno}", hits[0][0], hits[0][1], kwargs))
    return found


@cache
def stand_ins() -> tuple[StandIn, ...]:
    found: list[StandIn] = []
    for path in py_files(TESTS):
        if path.name in NOT_SUITE:
            continue
        found.extend(stand_ins_in(parse_file(path), str(path.relative_to(REPO))))
    return tuple(found)


@cache
def namespace_sites() -> int:
    """How many keyworded `SimpleNamespace(...)` calls the stand-in scan actually examined."""
    total = 0
    for path in py_files(TESTS):
        if path.name in NOT_SUITE:
            continue
        for node in ast.walk(parse_file(path)):
            if _is_namespace(node):
                assert isinstance(node, ast.Call)
                total += bool(_kwargs(node))
    return total
