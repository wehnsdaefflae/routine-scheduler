"""The double-conformance floor, in BOTH directions: what a double ASSIGNS must exist on the
class it impersonates, and a `SimpleNamespace` the real dataclass would have CONSTRUCTED is not
allowed to exist. The extraction is tests/double_survey.py; this module is the rules and the
design record of what they waive and why.
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
    ("_Ctx", "booked"):
        "the stub's own recording list — the real RunContext.add_usage() folds into `usage` "
        "under a lock, and what D288's spend seam must prove is WHICH readings reached it and "
        "which were refused, which the folded total cannot show (tests/test_util_spend.py:14)",
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
    """Rule 2 (F766): a `SimpleNamespace` the real dataclass would have built is the violation."""
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
    """Rule 2's floors are on its MACHINERY, because its healthy state is zero hits."""
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
    """An exemption may cover BOOKKEEPING, never a name production code dispatches through."""
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
    # D241 renames the model-facing VOCABULARY; `loop.subruns` is an internal attribute and is
    # the second increment's business (with the budget keys and the event names).
    assert "subruns" in real, "the real child-run manager attribute"
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
