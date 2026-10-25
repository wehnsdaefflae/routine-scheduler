"""The double-conformance floor, in BOTH directions: what a double ASSIGNS must exist on the
class it impersonates, and a `SimpleNamespace` the real dataclass would have CONSTRUCTED is not
allowed to exist. The extraction is tests/double_survey.py; this module is the rules and the
design record of what they waive and why.

F644 (0.399.1, 2026-10-09): `engine/ladder.py` dispatched through `loop.subs` where the real
attribute is `loop.subruns`, so "a rung has never judged anything" — for months, behind a green
suite. The release's own words: *"The ladder's own tests passed because their fake loop defined
`subs` to match the caller."* That mechanism, not the typo, is what this file exists for: a
hand-built double's attribute surface is AUTHORED BY THE TEST, out of the same misreading that
produced the calling code, so such a test asserts only that the two agree with each other.
`SimpleNamespace` is the suite's universal double (410 occurrences over 81 files, measured
2026-10-10) and nothing here bans it, rewrites one or adopts a mocking framework: this test
READS the doubles that already exist and checks them against the real class.

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

THE SECOND DIRECTION, and why the first one could not have caught F766 (0.448.0, 2026-10-24).
`Completion` is what every scripted endpoint in the suite returns, and
tests/test_util_guard.py:85 stood in for one with `SimpleNamespace(parsed=…, text="")`. Both of
those ARE real `Completion` fields, so the assigns-only rule above was GREEN: what broke the
release was the field the stand-in OMITTED — `parsed_rest`, which F754 had just added and
`engine/completion.py:279` reads through `replyactions.py:124`
(`AttributeError: 'types.SimpleNamespace' object has no attribute 'parsed_rest'`, found by a
25-minute release gate, paid for with a restore and a full rebuild). No check of assigned names
can ever see a field that is not there: a hand-built surface is missing tomorrow's field by
construction, and the defect arrives when production starts reading it, never when the double is
written. So the second rule removes the hand-built surface instead of policing it — a
`SimpleNamespace` that the real dataclass would have CONSTRUCTED from the very same keywords is
the violation, and the fix is always the same one line: construct the dataclass.

WHY CONSTRUCTIBILITY AND NOT A FIFTH `IMPERSONATED` ENTRY — measured on this tree, 2026-10-25,
and the numbers are why a hand-maintained coverage list was not extended:
  * `IMPERSONATED` detects by NAME, and NOT ONE Completion stand-in in the suite is held in a
    variable called `completion` (or `comp`): all of them are
    `def complete(...): return SimpleNamespace(...)`, returned straight out of a fake endpoint,
    or bound into `SimpleNamespace(complete=lambda …)`. A fifth entry would have been VACUOUS —
    red-free on arrival, exactly like the green run that shipped F766.
  * Constructibility, on 441 keyworded `SimpleNamespace(...)` sites in tests/: 4 flagged, all 4
    `Completion`, 0 false. It also found a FOURTH site the report's own grep missed
    (tests/test_create_routine_outcome.py:133), which is the argument for a predicate over a
    list in one line.
  * SCOPE was measured too. Over all 48 dataclasses under `src/rsched` the same rule flags 12
    sites of which 8 are false, every one of them from a dataclass with NO required field:
    `GrantPolicy` (20 fields, 0 required) claims five `SimpleNamespace(libraries_home=…)`
    server doubles, and `Commit` claims a bare `status=`. Hence two exclusions in
    `scoped_dataclasses()` — a class needs at least one required field to be claimable, and the
    scan covers `endpoints/` and `engine/`, the objects production dispatches through.
  * A namespace is claimed only when exactly ONE scoped dataclass fits. Two fits is ambiguous
    and silence is correct there; `**rest` is skipped because its keywords are not static.

THE FLOORS are what stop this file passing vacuously, and the two rules need DIFFERENT ones.
Rule 1 has standing violations to count: MIN_DOUBLES reds if detection breaks, MIN_SURFACE reds
if surface extraction stops finding a real class. Rule 2's healthy state is ZERO hits, so a
floor on its hits would be a floor on the defect — it gets a floor on its MACHINERY instead
(MIN_SCOPED dataclasses discovered, MIN_NAMESPACE_SITES namespaces actually walked) plus
`test_the_detector_catches_the_f766_shape`, which feeds it the historic stand-in verbatim and
requires the violation the suite was green on. `test_the_detector_catches_the_f644_shape` does
the same for rule 1.

ONE EXEMPTION KIND, and it is itself guarded: a stub that RECORDS what it was told carries
bookkeeping the real class has no reason to have (`_Transcript.events`, a list the test reads
back where the real `Transcript.event()` appends to a file). Those sit in RECORDERS with the
reason written out, and `test_an_exempt_recorder_is_never_read_by_production` reds if any
module under `src/` ever READS an exempted name off the real object — without that guard, the
table would be a hole of exactly F644's shape. Rule 2 has no exemption table at all: the fix
for every hit is to construct the real class, so a waiver would only ever buy back the defect.

WHAT THIS DOES NOT COVER, stated so nobody mistakes a green run for more than it is. Rule 1 is
by NAME, so a double held in a variable called something else (`fake`, `l2`, a fixture return)
is invisible to it, and only the four classes in IMPERSONATED are checked. Rule 2 is by SHAPE
and so needs no list — but it sees only `SimpleNamespace`, only dataclasses whose fields are
readable from their own file (one with base classes is skipped), and only keywords written
literally at the call. A class whose attributes come from a dict at runtime (the pydantic
`RoutineConfig`/`ServerConfig`) cannot be checked statically at all, which is why neither is
in either rule.
"""

from __future__ import annotations

import ast

import pytest
from tests.double_survey import (
    IMPERSONATED,
    REPO,
    SRC,
    STUBS,
    base_name,
    doubles_in,
    namespace_sites,
    parse_file,
    py_files,
    scoped_dataclasses,
    stand_ins,
    stand_ins_in,
    suite_doubles,
    surface,
)

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

#: Rule 2's machinery floors, ~75% of the 2026-10-25 census: 14 claimable dataclasses under
#: `endpoints/`+`engine/` (16 dataclasses there, less the 2 the exclusions drop) and 441
#: keyworded `SimpleNamespace(...)` sites in tests/. A collapse in either means the stand-in
#: scan went quiet, and a quiet scan reports a clean tree it never read.
MIN_SCOPED = 10
MIN_NAMESPACE_SITES = 330

#: The fake loop exactly as the ladder's tests carried it before R2348/R2352 fixed it — the
#: shape rule 1 must red on, kept verbatim because a conformance test nobody has seen fail is
#: not evidence.
F644_FAKE_LOOP = '''
from types import SimpleNamespace


class _Loop:
    """The slice of EngineLoop the ladder touches."""

    def __init__(self, ctx, subs, says=()):
        self.ctx = ctx
        self.subs = subs
        self.turn_records = [{"say": s} for s in says]
'''

#: The Completion stand-in exactly as tests/test_util_guard.py:85 carried it into 0.448.0 — the
#: shape rule 2 must red on, and the one rule 1 was green on: `parsed` and `text` are both real
#: `Completion` fields, and `parsed_rest` is the one it does not mention.
F766_STAND_IN = """
from types import SimpleNamespace


def test_denial_rides_the_schema_retry_cycle(tmp_path):
    completion = SimpleNamespace(parsed={"kind": "write_util"}, text="")
    candidate, problems = action_candidate(_loop(tmp_path), completion)
    assert candidate["kind"] == "write_util"
"""


@pytest.mark.parametrize("name", sorted(IMPERSONATED))
def test_every_attribute_a_double_assigns_exists_on_the_real_class(name: str) -> None:
    real = surface(name)
    bad = [(d.where, d.shape, attr) for d in suite_doubles() if d.impersonates == name
           for attr in d.attrs
           if attr not in real and (d.owner, attr) not in RECORDERS]
    _, class_name = IMPERSONATED[name]
    lines = "\n".join(f"  {where}  ({shape}) assigns {attr!r}" for where, shape, attr in bad)
    assert not bad, (
        f"{len(bad)} attribute(s) assigned by a double of {class_name} do not exist on it "
        f"(F644's mechanism). Fix the PRODUCTION name if the double is right, the double if it "
        f"is not:\n{lines}")


def test_no_namespace_stands_in_for_a_real_dataclass() -> None:
    """Rule 2 (F766): a `SimpleNamespace` the real dataclass would have built is the violation.

    There is no exemption table and there is no fix but one: construct the dataclass. A
    hand-built surface is missing the field production adds TOMORROW, which is what broke
    0.448.0 — and the assigns-only rule above cannot see a missing field at all.
    """
    bad = stand_ins()
    lines = "\n".join(
        f"  {s.where}  SimpleNamespace({', '.join(f'{k}=…' for k in s.kwargs)}) "
        f"→ {s.cls}({', '.join(f'{k}=…' for k in s.kwargs)})  [{s.module}]" for s in bad)
    assert not bad, (
        f"{len(bad)} SimpleNamespace stand-in(s) for a real dataclass — every field the class "
        f"requires is already supplied, so the real class constructs here and the hand-built "
        f"surface is a choice. It is the F766 shape: the stand-in is missing whatever field "
        f"production reads next (`Completion.parsed_rest` cost a release gate, a restore and a "
        f"full rebuild). Construct the dataclass, import it, and delete the namespace:\n{lines}")


@pytest.mark.parametrize("name", sorted(IMPERSONATED))
def test_the_check_cannot_pass_vacuously(name: str) -> None:
    """Rule 1's floors: a refactor that breaks detection or surface extraction reds here."""
    counts = {key: sum(1 for d in suite_doubles() if d.impersonates == key)
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


def test_the_stand_in_scan_cannot_pass_vacuously() -> None:
    """Rule 2's floors are on its MACHINERY, because its healthy state is zero hits.

    A floor on hits would be a floor on the defect. What must not collapse is what the scan
    READ: the claimable dataclasses it compares against, and the namespaces it walked.
    """
    scoped = scoped_dataclasses()
    names = {name for name, _m, _f, _r in scoped}
    sites = namespace_sites()
    assert len(scoped) >= MIN_SCOPED, (
        f"only {len(scoped)} claimable dataclass(es) found under endpoints/+engine/ (floor "
        f"{MIN_SCOPED}): {sorted(names)}. Discovery broke, and a stand-in scan with nothing to "
        f"compare against reports a clean tree it never read")
    assert sites >= MIN_NAMESPACE_SITES, (
        f"only {sites} keyworded SimpleNamespace site(s) walked in tests/ (floor "
        f"{MIN_NAMESPACE_SITES}) — the scan stopped seeing the suite's doubles")
    assert "Completion" in names, (
        "Completion is what every scripted endpoint in the suite returns and the class F766 was "
        "made of — if it stops being discovered, this rule is checking the wrong tree")


def test_an_exempt_recorder_is_never_read_by_production() -> None:
    """An exemption may cover BOOKKEEPING, never a name production code dispatches through.

    Without this, `RECORDERS` would be a hole of exactly F644's shape: a double and a caller
    agreeing on a name the real class does not have, waved through by a table in the test.
    """
    # (convention name, attribute) → the exemption it would be using, e.g.
    # ("transcript", "events") → "_Transcript.events".
    exempt = {(STUBS[stub], attr): f"{stub}.{attr}" for stub, attr in RECORDERS}
    reads: dict[str, set[str]] = {}
    for path in py_files(SRC):
        for node in ast.walk(parse_file(path)):
            if isinstance(node, ast.Attribute) and (
                    label := exempt.get((base_name(node.value), node.attr))):
                reads.setdefault(label, set()).add(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not reads, (
        f"production code reads an exempted attribute off the real object: {reads}. That is "
        f"F644 again — drop the exemption from RECORDERS and fix whichever side is wrong")


def test_the_detector_catches_the_f644_shape() -> None:
    found = doubles_in(ast.parse(F644_FAKE_LOOP), "<f644>")
    assert [d.impersonates for d in found] == ["loop"], "the stub class was not recognised"
    bad = sorted(a for d in found for a in d.attrs if a not in surface("loop"))
    assert bad == ["subs"], (
        "this checker must report `subs` — the attribute `engine/ladder.py` dispatched through "
        f"for months while the real one was `subruns`. It reported: {bad}")


def test_the_detector_catches_the_f766_shape() -> None:
    """The stand-in 0.448.0 shipped, fed back in: rule 2 reds on it, rule 1 cannot."""
    found = stand_ins_in(ast.parse(F766_STAND_IN), "<f766>")
    assert [(s.cls, s.kwargs) for s in found] == [("Completion", ("parsed", "text"))], (
        "rule 2 must report this namespace as a Completion the dataclass would have built — it "
        f"is the stand-in that broke the 0.448.0 release gate. It reported: {found}")
    assert not doubles_in(ast.parse(F766_STAND_IN), "<f766>"), (
        "and rule 1 must stay silent on it, which is the whole reason rule 2 exists: `parsed` "
        "and `text` are both REAL Completion fields, so nothing this double assigns is wrong — "
        "what broke production was the field it left out")


def test_the_real_loop_surface_pins_f644_both_ways() -> None:
    real = surface("loop")
    assert "subruns" in real, "the real sub-run manager attribute"
    assert "subs" not in real, "F644's misreading must never become real"
    assert "_recipe_unlocked" in real, (
        "set only at engine/loopsetup.py:155 — if this drops out, the surface is being read "
        "from engine/loop.py alone and every collaborator-assigned attribute reds falsely")


def test_the_completion_surface_pins_f766() -> None:
    """The field whose absence from a hand-built stand-in broke 0.448.0 is a REQUIRED part of
    the comparison: `parsed_rest` has a default, so a stand-in supplying only `parsed`+`text`
    still constructs — which is exactly why the stand-in is the thing that must not exist."""
    fields = {name: (every, required) for name, _m, every, required in scoped_dataclasses()}
    every, required = fields["Completion"]
    assert {"parsed", "text", "parsed_rest"} <= every, (
        "Completion lost a field this rule is pinned to — `parsed_rest` (F754) is what "
        f"engine/completion.py reads and the stand-in lacked. Fields seen: {sorted(every)}")
    assert required == {"text"}, (
        f"Completion's required fields are what make a `parsed`+`text` namespace claimable; "
        f"they came out as {sorted(required)}. If this changes, re-measure the census in this "
        f"file's docstring before trusting either number in it")
