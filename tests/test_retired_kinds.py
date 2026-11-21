"""A RETIRED action kind is named nowhere the engine or the console still decides by kind."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from rsched.engine.actions import KIND_FIELDS, validate_action
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.completion import diagnose
from rsched.engine.renamedkinds import RENAMED, hint
from rsched.schema_guard import validate

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "rsched"
STATIC = ROOT / "static"

#: (file relative to src/rsched, retired word) pairs where the word is NOT the retired kind.
#: `task` is also the pre-D174 key of a run-gate check's job (`jobs_compat`), read beside `job`
#: — a config spelling with its own expiry, not an action kind.
NOT_THE_KIND = frozenset({("gatekit/__init__.py", "task"), ("jobs_compat.py", "task")})
#: Files whose whole job is the retired spelling: the register itself and the D241 migration.
REGISTERS = frozenset({"engine/renamedkinds.py", "migrate_child_kind.py"})

#: Floors on the scan's MACHINERY, never on its hits (zero hits is the healthy state): a
#: detector that stops recognising tables must go red, not pass vacuously. Measured 2026-11-21
#: at 196 Python tables and 104 JS comparisons.
MIN_TABLES = 150
MIN_JS_COMPARISONS = 80

_JS_COMPARE = re.compile(r"""kind\s*[!=]==?\s*["']([a-z_]+)["']""")
_JS_ARRAY = re.compile(r"""\[([^\[\]]*)\]""")
_JS_WORD = re.compile(r"""["']([a-z_]+)["']""")


# ── the register ───────────────────────────────────────────────────────────────────────────

def test_every_entry_maps_a_retired_kind_to_a_live_one():
    for old, (new, why, added) in RENAMED.items():
        assert old not in KINDS, f"{old!r} is still a live kind — it is not retired"
        assert new in KINDS, f"{old!r} → {new!r}, which is not a kind either"
        assert why.strip(), old
        fields = set().union(*KIND_FIELDS[new])
        assert set(added) <= fields, f"{old!r} adds {set(added) - fields} that {new!r} lacks"


@pytest.mark.parametrize(("old", "new"), [("spawn", "child"), ("subtask", "child"),
                                          ("subruns", "children")])
def test_a_d241_kind_is_told_its_new_name(old, new):
    stale = {"say": "s", "kind": old}
    said = hint(stale)
    assert f"`{new}` now" in said and f"kind={new!r}" in said
    assert "D241" in said and "recipe" in said
    assert said in diagnose(stale)   # it reaches the model through the retry's diagnosis


def test_a_subtask_is_told_to_add_its_mode_or_it_would_become_a_parallel_child():
    """`subtask` meant SEQUENTIAL. "Every other field unchanged" alone would turn it into a
    parallel child — and refuse its `turns`, which only a sequential child takes."""
    assert "mode='sequential'" in hint({"kind": "subtask"})
    assert "mode=" not in hint({"kind": "spawn"})


@pytest.mark.parametrize("stale", [
    {"say": "s", "kind": "spawn", "prompt": "do x", "label": "x", "workflow": "general-task"},
    {"say": "s", "kind": "subtask", "prompt": "do y", "label": "y", "turns": 6},
    {"say": "s", "kind": "subruns"},
    {"say": "s", "kind": "task", "verb": "list"},
])
def test_following_the_correction_yields_a_valid_action(stale):
    """The hint is a promise: send the same action with the new kind (and the named fields)
    and it is accepted. A correction that leads to a second refusal is the F637 shape again."""
    assert validate(stale, ACTION_SCHEMA) != []
    new, _why, added = RENAMED[stale["kind"]]
    fixed = {**stale, "kind": new, **added}
    assert validate(fixed, ACTION_SCHEMA) == []
    assert validate_action(fixed) == []


def test_an_ordinary_action_gets_no_hint():
    assert hint({"kind": "child", "prompt": "x"}) == ""
    assert hint({"kind": "children"}) == ""


# ── the sweep ──────────────────────────────────────────────────────────────────────────────

def _strings(node: ast.AST) -> set[str]:
    """The string members of a literal table: a set/tuple/list, a frozenset() of one, or a
    dict's keys (`KIND_FIELDS`-shaped tables are keyed by kind)."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"frozenset", "set", "tuple"} and node.args):
        node = node.args[0]
    if isinstance(node, ast.Dict):
        elts: list = [k for k in node.keys if k is not None]
    elif isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        elts = node.elts
    else:
        return set()
    return {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def test_no_python_table_or_comparison_names_a_retired_kind():
    live, dead = set(KINDS), set(RENAMED)
    tables, bad = 0, []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in REGISTERS:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            words = _strings(node)
            if words & live:
                tables += 1
                bad += [f"{rel}:{node.lineno} table names {w!r}" for w in sorted(words & dead)
                        if (rel, w) not in NOT_THE_KIND]
            if isinstance(node, ast.Compare):
                bad += [f"{rel}:{node.lineno} compares against {c.value!r}"
                        for c in node.comparators
                        if isinstance(c, ast.Constant) and c.value in dead
                        and (rel, c.value) not in NOT_THE_KIND]
    assert tables >= MIN_TABLES, f"the scan found only {tables} kind tables — widen it"
    assert bad == [], "retired kinds still decide something:\n" + "\n".join(bad)


def test_no_console_module_names_a_retired_kind():
    live, dead = set(KINDS), set(RENAMED)
    comparisons, bad = 0, []
    for path in sorted(STATIC.rglob("*.js")):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for m in _JS_COMPARE.finditer(text):
            comparisons += 1
            if m.group(1) in dead:
                bad.append(f"{rel}: {m.group(0)}")
        for m in _JS_ARRAY.finditer(text):
            words = set(_JS_WORD.findall(m.group(1)))
            if words & live and words & dead:
                bad.append(f"{rel}: [{m.group(1)[:60]}]")
    assert comparisons >= MIN_JS_COMPARISONS, f"only {comparisons} kind comparisons — widen it"
    assert bad == [], "the console still keys on retired kinds:\n" + "\n".join(bad)
