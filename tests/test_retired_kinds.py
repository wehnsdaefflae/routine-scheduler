"""A RETIRED action kind is named nowhere the engine or the console still decides by kind."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from rsched.engine.actions import KIND_FIELDS
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.completion import diagnose
from rsched.engine.renamedkinds import RETIRED, hint
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

#: Kind names that were RENAMED away (D174, D241) and must never key a table or a comparison
#: again. They live HERE and not in a src register: the register existed to CORRECT a run
#: spelling one of them, and no recipe, library pattern or seed does — while the sweep below
#: is about the code, where a resurrected word would silently decide something. A dead word
#: needs no runtime presence to stay forbidden.
RENAMED_AWAY = frozenset({"task", "spawn", "subtask", "subruns"})

#: Where naming a retired kind is CORRECT, with the reason — never a silent exemption.
#: A kind that retired into a util capability (`RETIRED`, D288) leaves RECORDS behind, and
#: those are read for as long as transcripts exist: a resumed leg replays stored observations,
#: past-run search renders hits, the console renders any run anyone opens, and a child's usage
#: is summed from its recorded payloads. None of these DECIDE anything about a live action —
#: which is what the sweep is about — and none of them has an end date, so they carry no
#: expiry marker either. The ONE thing that must stay true: no entry here may decide what a
#: live action does, so each names the reader rather than the module's whole surface.
RENDERS_RECORDS: dict[str, str] = {
    "engine/obs_retired.py": "the renderer for recorded observations of retired kinds",
    "engine/obs_floor.py": "OWN_ERRORS: whose renderer words an `error` result — floor 1 runs "
                           "BEFORE the per-kind chain, so a recorded error of a retired kind "
                           "would otherwise be re-worded by the generic wording on a replay",
    "engine/subsalvage.py": "a dead child's usage, summed from its RECORDED observations",
    "engine/history.py": "a prior leg's usage, summed from its RECORDED observations",
    "cli_render.py": "the CLI's transcript renderer, over runs recorded at any version",
}

#: The console's counterpart of RENDERS_RECORDS: JS modules that render a RECORDED transcript.
RENDERS_RECORDS_JS: dict[str, str] = {
    "static/components/transcript.js": "the transcript view of any past run",
    "static/components/obs-text.js": "an observation body in a past run",
    "static/components/command-result.js": "a slash command's recorded result",
    "static/components/waiting-label.js": "the in-flight label a replayed turn still carries",
}

#: Floors on the scan's MACHINERY, never on its hits (zero hits is the healthy state): a
#: detector that stops recognising tables must go red, not pass vacuously. Measured 2026-11-21
#: at 196 Python tables and 104 JS comparisons.
MIN_TABLES = 150
MIN_JS_COMPARISONS = 80

_JS_COMPARE = re.compile(r"""kind\s*[!=]==?\s*["']([a-z_]+)["']""")
_JS_ARRAY = re.compile(r"""\[([^\[\]]*)\]""")
_JS_WORD = re.compile(r"""["']([a-z_]+)["']""")


# ── the register ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("old", sorted(RENAMED_AWAY))
def test_a_word_renamed_away_is_no_kind_and_gets_no_correction(old):
    """The rename register that used to turn one of these into "it is `child` now" is gone:
    nothing on disk spelled them, so it corrected a mistake nobody was making. What must stay
    true is that the word is dead — refused by the schema, declaring no fields, and NOT
    quietly answered by the retirement register, whose promise ("a util carries it now") would
    be false for a word that was only renamed.
    """
    assert old not in KINDS, f"{old!r} is a live kind again — this list is about dead words"
    assert old not in KIND_FIELDS, f"{old!r} still declares action fields"
    assert old not in RETIRED, f"{old!r} was renamed, not retired into a capability"
    assert validate({"say": "s", "kind": old}, ACTION_SCHEMA) != []
    assert hint({"say": "s", "kind": old}) == ""


@pytest.mark.parametrize("kind", sorted(RETIRED))
def test_a_kind_retired_into_a_capability_is_not_a_kind_and_has_no_fields(kind):
    """D288's shape: the replacement is a CAPABILITY, so there is no kind to fall back on."""
    assert kind not in KINDS, f"{kind!r} is still live — it is not retired"
    assert kind not in KIND_FIELDS, f"{kind!r} still declares action fields"
    assert kind not in RENAMED_AWAY, f"{kind!r} was renamed, not retired — keep them disjoint"
    capability, why = RETIRED[kind]
    assert len(capability.strip()) >= 12 and why.strip()


@pytest.mark.parametrize("kind", sorted(RETIRED))
def test_the_correction_never_promises_a_resend_that_cannot_work(kind):
    """The F637 shape, in its one dangerous variant: a correction saying "send the SAME action
    again with kind=X" is true only when X is a kind. Promising it for a capability would cost
    a run a turn per attempt forever — there is no action to resend.
    """
    said = hint({"say": "s", "kind": kind})
    assert "RETIRED" in said and "util" in said
    assert "send the SAME action again" not in said
    assert f"kind={kind!r}" not in said
    assert said in diagnose({"say": "s", "kind": kind})   # it reaches the model on the retry


def test_a_retired_kind_is_refused_by_the_schema_itself():
    """Belt and braces: the hint teaches, but the schema is what makes it unemittable."""
    for kind in RETIRED:
        assert validate({"say": "s", "kind": kind, "prompt": "x"}, ACTION_SCHEMA) != []


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
    live, dead = set(KINDS), RENAMED_AWAY | set(RETIRED)
    tables, bad = 0, []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in REGISTERS:
            continue
        # Counted either way (the floor measures the whole package); only a record-renderer's
        # HITS are allowed, for the reason RENDERS_RECORDS states per file.
        exempt = rel in RENDERS_RECORDS
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            words = _strings(node)
            if words & live:
                tables += 1
                bad += [f"{rel}:{node.lineno} table names {w!r}" for w in sorted(words & dead)
                        if (rel, w) not in NOT_THE_KIND and not exempt]
            if isinstance(node, ast.Compare):
                bad += [f"{rel}:{node.lineno} compares against {c.value!r}"
                        for c in node.comparators
                        if isinstance(c, ast.Constant) and c.value in dead
                        and (rel, c.value) not in NOT_THE_KIND and not exempt]
    assert tables >= MIN_TABLES, f"the scan found only {tables} kind tables — widen it"
    assert bad == [], "retired kinds still decide something:\n" + "\n".join(bad)


def test_no_console_module_names_a_retired_kind():
    live, dead = set(KINDS), RENAMED_AWAY | set(RETIRED)
    comparisons, bad = 0, []
    for path in sorted(STATIC.rglob("*.js")):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        # Every file is COUNTED, so the machinery floor below still measures the whole console;
        # only the HITS of a record-renderer are allowed (skipping the files would have hidden
        # 24 live comparisons from the floor and weakened the guard — measured 2026-11-25).
        exempt = rel in RENDERS_RECORDS_JS
        for m in _JS_COMPARE.finditer(text):
            comparisons += 1
            if m.group(1) in dead and not exempt:
                bad.append(f"{rel}: {m.group(0)}")
        for m in _JS_ARRAY.finditer(text):
            words = set(_JS_WORD.findall(m.group(1)))
            if words & live and words & dead and not exempt:
                bad.append(f"{rel}: [{m.group(1)[:60]}]")
    assert comparisons >= MIN_JS_COMPARISONS, f"only {comparisons} kind comparisons — widen it"
    assert bad == [], "the console still keys on retired kinds:\n" + "\n".join(bad)
