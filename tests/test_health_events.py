"""The health stream's VOCABULARY is machine-checked against what the code emits."""

from __future__ import annotations

import ast
from pathlib import Path

from rsched import health_events, health_vocab

SRC = Path(health_events.__file__).parent


def _documented() -> set[str]:
    """`health_vocab.EVENTS` — the list a reader builds a filter from."""
    return set(health_vocab.EVENTS)


def _literal_strings(node: ast.AST) -> set[str]:
    """The string constants one simple expression can evaluate to — a literal, or either arm of
    a conditional between two (`"run_canceled" if user_cancel else "orphaned_run"`)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_strings(node.body) | _literal_strings(node.orelse)
    return set()


def _emitted() -> set[str]:
    """Every event name reachable at a `log_health_event` call site in src/rsched."""
    names: set[str] = set()
    prefixes: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bound: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound.setdefault(target.id, set()).update(_literal_strings(node.value))
            elif isinstance(node, ast.keyword) and node.arg == "event":
                bound.setdefault("event", set()).update(_literal_strings(node.value))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called != "log_health_event" or len(node.args) < 2:
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Name):
                names |= bound.get(arg.id, set())
            elif isinstance(arg, ast.JoinedStr):
                prefixes |= {v.value for v in arg.values
                             if isinstance(v, ast.Constant) and isinstance(v.value, str)}
            else:
                names |= _literal_strings(arg)
    assert names, "no log_health_event call sites found — the walker is broken, not the code"
    return names | prefixes


def test_every_emitted_event_is_in_the_documented_vocabulary():
    documented = _documented()
    unknown = {n for n in _emitted()
               if n not in documented and not any(d.startswith(n) for d in documented)}
    assert not unknown, (
        f"health events emitted but absent from health_vocab.EVENTS: "
        f"{sorted(unknown)} — every reader filters on that list, so an undocumented event "
        f"is written and read by nobody")


def test_the_documented_vocabulary_is_not_a_wish_list():
    """The other direction, loosely: a documented name nothing emits is a reader chasing an
    event that cannot arrive. Family names (`lane_chain_done`) are emitted through an f-string,
    so a documented name counts as emitted when an emitted prefix covers it."""
    emitted = _emitted()
    orphaned = {d for d in _documented()
                if d not in emitted and not any(d.startswith(e) for e in emitted)}
    assert not orphaned, f"documented health events nothing emits: {sorted(orphaned)}"
