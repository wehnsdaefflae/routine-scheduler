"""Every attribute a test double ASSIGNS exists on the class the double impersonates.

F644 (0.399.1, 2026-10-09): `engine/ladder.py` dispatched through `loop.subs` where the real
attribute is `loop.subruns`, so "a rung has never judged anything" — for months, behind a green
suite. The release's own words: *"The ladder's own tests passed because their fake loop defined
`subs` to match the caller."* That mechanism, not the typo, is what this file exists for: a
hand-built double's attribute surface is AUTHORED BY THE TEST, out of the same misreading that
produced the calling code, so such a test asserts only that the two agree with each other.
`SimpleNamespace` is the suite's universal double (410 occurrences over 81 files, measured
2026-10-10) and nothing here bans it, rewrites one or adopts a mocking framework: this test
READS the doubles that already exist and checks their attribute surface against the real class.

HOW A DOUBLE IS RECOGNISED as impersonating class X — by NAME, the same convention production
code follows when it takes one of these objects as a parameter (`ladder.at_boundary(loop)`,
`run_context.work_dir(ctx)`). Three shapes, all read statically with `ast`, never imported: 81
test modules are slow to import and many of them need fixtures.
  1. `<name> = SimpleNamespace(...)` where <name> is a key of IMPERSONATED.
  2. `SimpleNamespace(<name>=SimpleNamespace(...))` — the double a test reaches through that
     keyword (a doubled `ctx` carries `routine=`, `budgets=`, `transcript=`), the keyword
     naming the impersonated class.
  3. `class _Loop:` … `self.x = …` — a hand-written stub class named in STUBS; every attribute
     its methods assign to `self` is its surface. A method such a stub DEFINES is out of scope
     here: this checks the attributes a double ASSIGNS, which is what F644 turned on.

THE TRAP this file had to solve: `dir(EngineLoop)` does not contain `subruns`. The real class
declares most of what it HOLDS as bare class-level annotations filled in by
`loopsetup.configure`, and `_recipe_unlocked` is not named in `engine/loop.py` at all — only
`engine/loopsetup.py:155` assigns it. A class's real surface is therefore read from four
places: class-level annotations, class-level assignments, `self.x = …` inside its own methods,
and COLLABORATOR assignments — `<name>.x = …` anywhere under `src/rsched`, where <name> is
that class's convention parameter name (22 engine modules assign onto a `loop`). A class whose
attributes are declared somewhere none of those four reach cannot be checked this way, which is
what the MIN_SURFACE floors below are for.

The floors are what stops this test from passing vacuously: MIN_DOUBLES reds if a refactor
breaks the detection instead of silently checking nothing, MIN_SURFACE reds if surface
extraction stops finding a real class, and `test_the_detector_catches_the_f644_shape` feeds the
detector the historic fake-loop source and requires the violation it missed.

ONE EXEMPTION KIND, and it is itself guarded: a stub that RECORDS what it was told carries
bookkeeping the real class has no reason to have (`_Transcript.events`, a list the test reads
back where the real `Transcript.event()` appends to a file). Those sit in RECORDERS with the
reason written out, and `test_an_exempt_recorder_is_never_read_by_production` reds if any
module under `src/` ever READS an exempted name off the real object — without that guard, the
table would be a hole of exactly F644's shape, a double and a caller agreeing on a name the
real class lacks because a test waved it through.

WHAT THIS DOES NOT COVER, stated so nobody mistakes a green run for more than it is: detection
is by NAME, so a double held in a variable called something else (`fake`, `l2`, a fixture
return) is invisible to it, and only the four classes in IMPERSONATED are checked out of the
~208 namespace doubles in the suite. Extending it is adding an entry to IMPERSONATED or a shape
to `doubles_in` — and a class whose attributes come from a dict at runtime (the pydantic
`RoutineConfig`/`ServerConfig`) cannot be checked statically at all, which is why neither is
here.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "rsched"
TESTS = REPO / "tests"

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
}

#: Hand-written stub classes → the convention name of what they impersonate (shape 3).
STUBS: dict[str, str] = {
    "_Loop": "loop", "_EngineLoop": "loop",
    "_Ctx": "ctx", "_Context": "ctx", "_RunContext": "ctx",
    "_Budgets": "budgets", "_Transcript": "transcript",
}

#: (stub class, attribute) → why that attribute is RECORDING, not impersonation. A stub that
#: records what it was told (`self.events.append(...)`, read back by the test's own asserts)
#: carries bookkeeping the real class has no reason to have, and the two `_Transcript` stubs
#: below are the only instance of it in the suite. The exemption cannot reopen F644's hole:
#: `test_an_exempt_recorder_is_never_read_by_production` reds the moment any src module reads
#: that name off the impersonated object.
RECORDERS: dict[tuple[str, str], str] = {
    ("_Transcript", "events"):
        "the stub's own recording list — the real Transcript.event() appends to a file, and "
        "these tests read the recording back instead (tests/test_ladder_trigger.py:27, "
        "tests/test_grant_secrets.py:151)",
}

#: Floors — a detection that silently finds nothing must red, not pass. Set at ~75% of the
#: census this file's first green run measured (2026-10-24, commit c03bfb19): 34 `loop`
#: doubles, 72 `ctx`, 17 `transcript`, 3 `budgets`. Churn has room; a broken detector does not.
MIN_DOUBLES: dict[str, int] = {"loop": 25, "ctx": 55, "budgets": 2, "transcript": 12}
#: Likewise ~75% of the measured real surfaces: 86 attributes on EngineLoop, 100 on RunContext,
#: 10 on Budgets, 9 on Transcript. A drop past these means extraction stopped seeing one of the
#: four places a surface is declared, and every real attribute would start reading as a
#: violation — the failure mode that makes a conformance test worse than none.
MIN_SURFACE: dict[str, int] = {"loop": 60, "ctx": 70, "budgets": 7, "transcript": 6}


class Double(NamedTuple):
    """One stand-in for a real class, as found in the suite's source."""

    where: str          # path:lineno
    impersonates: str   # a key of IMPERSONATED
    shape: str          # which of the three recognition shapes matched
    owner: str          # the stub class's name, or "" for a SimpleNamespace
    attrs: tuple[str, ...]


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
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


def _base_name(node: ast.expr) -> str | None:
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


def _self_assigned(cls: ast.ClassDef) -> set[str]:
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
    """`<name>.x = …` anywhere under `src/rsched`, per convention name.

    The reason this function exists: `loop._recipe_unlocked` is read by
    `loopsetup.build_base_policy` and assigned at `engine/loopsetup.py:155` — the class's own
    file never mentions it, so reading only `engine/loop.py` would call a REAL attribute a
    violation. `self.ctx.turn = …` counts too, hence the second branch.
    """
    found: dict[str, set[str]] = {name: set() for name in IMPERSONATED}
    for path in _py_files(SRC):
        for node in _assignments(_parse(path)):
            for target in _targets(node):
                if isinstance(target, ast.Attribute) and (
                        name := _base_name(target.value)) in found:
                    found[str(name)].add(target.attr)
    return {name: frozenset(attrs) for name, attrs in found.items()}


@cache
def surface(name: str) -> frozenset[str]:
    """The real attribute surface of the class `name` impersonates, read from its four places."""
    path, class_name = IMPERSONATED[name]
    tree = _parse(path)
    cls = next((n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == class_name), None)
    assert cls is not None, f"{class_name} is no longer defined in {path} — fix this map"
    return frozenset(_declared(cls) | _self_assigned(cls) | _collaborator_surface()[name])


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
                                node.name, tuple(sorted(_self_assigned(node)))))
    return found


def _kwargs(call: ast.Call) -> tuple[str, ...]:
    return tuple(sorted(kw.arg for kw in call.keywords if kw.arg))


@cache
def _suite_doubles() -> tuple[Double, ...]:
    found: list[Double] = []
    for path in _py_files(TESTS):
        if path.name == Path(__file__).name:
            continue                      # this file's own fixtures are not suite doubles
        found.extend(doubles_in(_parse(path), str(path.relative_to(REPO))))
    return tuple(found)


@pytest.mark.parametrize("name", sorted(IMPERSONATED))
def test_every_attribute_a_double_assigns_exists_on_the_real_class(name: str) -> None:
    real = surface(name)
    bad = [(d.where, d.shape, attr) for d in _suite_doubles() if d.impersonates == name
           for attr in d.attrs
           if attr not in real and (d.owner, attr) not in RECORDERS]
    _, class_name = IMPERSONATED[name]
    lines = "\n".join(f"  {where}  ({shape}) assigns {attr!r}" for where, shape, attr in bad)
    assert not bad, (
        f"{len(bad)} attribute(s) assigned by a double of {class_name} do not exist on it "
        f"(F644's mechanism). Fix the PRODUCTION name if the double is right, the double if it "
        f"is not:\n{lines}")


@pytest.mark.parametrize("name", sorted(IMPERSONATED))
def test_the_check_cannot_pass_vacuously(name: str) -> None:
    """Floors: a refactor that breaks detection or surface extraction reds here."""
    counts = {key: sum(1 for d in _suite_doubles() if d.impersonates == key)
              for key in sorted(IMPERSONATED)}
    sizes = {key: len(surface(key)) for key in sorted(IMPERSONATED)}
    assert counts[name] >= MIN_DOUBLES[name], (
        f"only {counts[name]} double(s) of {name} found (floor {MIN_DOUBLES[name]}); all "
        f"counts {counts}. Detection broke, or the doubles moved to a shape this file does "
        f"not recognise — widen the shapes, never the floor")
    assert sizes[name] >= MIN_SURFACE[name], (
        f"the real surface of {name} came out at {sizes[name]} attributes (floor "
        f"{MIN_SURFACE[name]}); all sizes {sizes}. Surface extraction broke, and an empty "
        f"surface would call every real attribute a violation")


def test_an_exempt_recorder_is_never_read_by_production() -> None:
    """An exemption may cover BOOKKEEPING, never a name production code dispatches through.

    Without this, `RECORDERS` would be a hole of exactly F644's shape: a double and a caller
    agreeing on a name the real class does not have, waved through by a table in the test.
    """
    # (convention name, attribute) → the exemption it would be using, e.g.
    # ("transcript", "events") → "_Transcript.events".
    exempt = {(STUBS[stub], attr): f"{stub}.{attr}" for stub, attr in RECORDERS}
    reads: dict[str, set[str]] = {}
    for path in _py_files(SRC):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Attribute) and (
                    label := exempt.get((_base_name(node.value), node.attr))):
                reads.setdefault(label, set()).add(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not reads, (
        f"production code reads an exempted attribute off the real object: {reads}. That is "
        f"F644 again — drop the exemption from RECORDERS and fix whichever side is wrong")


#: The fake loop exactly as the ladder's tests carried it before R2348/R2352 fixed it — the
#: shape this checker must red on, kept verbatim because a conformance test nobody has seen
#: fail is not evidence.
F644_FAKE_LOOP = '''
from types import SimpleNamespace


class _Loop:
    """The slice of EngineLoop the ladder touches."""

    def __init__(self, ctx, subs, says=()):
        self.ctx = ctx
        self.subs = subs
        self.turn_records = [{"say": s} for s in says]
'''


def test_the_detector_catches_the_f644_shape() -> None:
    found = doubles_in(ast.parse(F644_FAKE_LOOP), "<f644>")
    assert [d.impersonates for d in found] == ["loop"], "the stub class was not recognised"
    bad = sorted(a for d in found for a in d.attrs if a not in surface("loop"))
    assert bad == ["subs"], (
        "this checker must report `subs` — the attribute `engine/ladder.py` dispatched through "
        f"for months while the real one was `subruns`. It reported: {bad}")


def test_the_real_loop_surface_pins_f644_both_ways() -> None:
    real = surface("loop")
    assert "subruns" in real, "the real sub-run manager attribute"
    assert "subs" not in real, "F644's misreading must never become real"
    assert "_recipe_unlocked" in real, (
        "set only at engine/loopsetup.py:155 — if this drops out, the surface is being read "
        "from engine/loop.py alone and every collaborator-assigned attribute reds falsely")
