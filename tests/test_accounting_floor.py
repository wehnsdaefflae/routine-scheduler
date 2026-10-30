"""The accounting's CONTENT floor: a placeholder evidence clause is not an account.

MEASURED, on `birthday-admin:20261022-080004` (F748). That run finished `ok` carrying
`d1 met: test`, `d2 met: test`, `d3 not due: test`, `g1 distance: test` — twenty entries of
those shapes — behind a 47-character summary. The accounting check verified that an entry
EXISTED for each id and that its verdict parsed; nothing looked at what came AFTER the verdict,
so a placeholder satisfied the account and the run's status said every line was met.

The floor is calibrated on the fleet, not invented. Over every routine's transcripts for the
14 days to 2026-10-23, 286 finishes carried 2 093 accounting entries; the evidence clause (the
text after the verdict word) ran p0.5 = 4 chars, p1 = 9, p2 = 50, median 240 — and EVERY entry
under 9 characters was one of birthday-admin's `test` placeholders. The shortest LEGITIMATE
entries in that corpus were vodafone-outage-claims' `not due` lines: `no filing` (9 chars, two
words), `no letter due`, `no letter to archive`, `no letter was due (d5)`. A plain character
floor around 30 would have refused all of those, so the rule is TWO WORDS plus a small
vocabulary of words a real account never consists of alone. Both directions are pinned here:
the birthday-admin shapes refused, the vodafone shapes accepted VERBATIM.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import WORKFLOW_MD, finish, make_test_server  # noqa: F401  (fixture side effects)
from helpers import server_for
from rsched import jobs
from rsched.config import ServerConfig
from rsched.engine import accounting, runrecord
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261023-090000"

DONE = [{"id": "d1", "stage": "", "text": "a"}, {"id": "d2", "stage": "", "text": "b"},
        {"id": "d3", "stage": "", "text": "c"}]
OUTCOMES = [{"id": "g1", "judge": "run", "text": "x"}]

#: Verbatim from `birthday-admin:20261022-080004` — every shape its twenty entries took.
SPECIMEN = ["d1 met: test", "d2 met: test", "d3 not due: test", "g1 distance: test"]

#: Verbatim from vodafone-outage-claims — the SHORTEST legitimate entries the fleet carries.
#: Each is a real account of a line that was genuinely not due; none may ever be refused.
REAL_SHORT = ["d1 not due: no filing", "d2 not due: no letter due",
              "d3 not due: no letter to archive", "g1 distance: no letter was due (d5)"]

DONE_WHEN_MD = WORKFLOW_MD.rstrip() + """

## Done when

- d1 — the figures are checked against the ledger
- d2 — the digest is published and its link resolves
"""


# ---- the floor itself --------------------------------------------------------------------------

def test_the_placeholder_shapes_the_specimen_landed_are_all_refused():
    found = accounting.problems(accounting.parse(SPECIMEN), DONE, OUTCOMES)
    assert found["placeholder"] == SPECIMEN            # verbatim, in the order they were owed
    assert found["missing"] == [] and found["bare"] == [] and found["refused"] == []


def test_the_shortest_real_entries_the_fleet_carries_are_accepted():
    """The six vodafone-outage-claims shapes a character floor would have refused."""
    found = accounting.problems(accounting.parse(REAL_SHORT), DONE, OUTCOMES)
    assert found == {"absent": [], "missing": [], "bare": [], "placeholder": [], "refused": []}


@pytest.mark.parametrize("clause", ["test", "TBD", "tbd.", "n/a", "N/A", "na", "none",
                                    "nothing", "todo", "placeholder", "-", "?", "...", "x",
                                    "xxx", "ok", "OK.", "done", "yes", "same", "ok, done",
                                    "  ", "—", "stub"])
def test_a_clause_of_only_placeholder_words_or_punctuation_is_a_placeholder(clause):
    assert accounting.is_placeholder(clause), clause


@pytest.mark.parametrize("clause", ["no filing", "no letter due", "no letter to archive",
                                    "no letter was due (d5)", "the page reads back",
                                    "62 messages swept", "3 of 5 feeds parsed",
                                    "hwm advanced 363 to 364", "state/a.txt written",
                                    "nothing new since r:4", "no bookings this week"])
def test_a_clause_that_says_something_is_an_account(clause):
    assert not accounting.is_placeholder(clause), clause


def test_a_placeholder_is_never_also_reported_bare():
    """`bare` is the EMPTY clause and `placeholder` the hollow one — one entry, one category,
    so the refusal says one thing about it."""
    found = accounting.problems(accounting.parse(["d1 met:", "d2 met: test",
                                                  "d3 met: the ledger matches"]), DONE, [])
    assert found["bare"] == ["d1"] and found["placeholder"] == ["d2 met: test"]


def test_an_entry_for_a_line_the_finish_does_not_owe_is_not_judged():
    """Only what the finish OWES is weighed, as `bare` already was: a stray id is `missing`
    territory, not a content question."""
    found = accounting.problems(accounting.parse(["d1 met: the ledger matches", "d9 met: test"]),
                                [DONE[0]], [])
    assert found["placeholder"] == [] and found["missing"] == []


# ---- what the refusal says ---------------------------------------------------------------------

def test_the_refusal_names_the_offending_entry_and_what_an_account_carries():
    msg = accounting.deferral(accounting.problems(
        accounting.parse(["d1 met: test", "d2 met: the digest is published", "d3 not due: n/a"]),
        DONE, []))
    assert msg.startswith("OBSERVATION (finish deferred): your `accounting` is incomplete")
    assert "d1 met: test" in msg and "d3 not due: n/a" in msg   # the entries, verbatim
    assert "d2" not in msg.split(". Carry one entry")[0]        # and only the offending ones
    assert "placeholder" in msg
    assert "observation or artefact" in msg       # what a real account carries


def test_the_gaps_clause_lists_a_placeholder_beside_the_other_findings():
    found = {"missing": ["d3"], "bare": ["d2"], "placeholder": ["d1 met: test"], "refused": []}
    text = accounting.gaps(found)
    assert "no entry for d3" in text
    assert "nothing after the verdict for d2" in text
    assert "d1 met: test" in text


# ---- the finish guard --------------------------------------------------------------------------

def test_a_finish_whose_accounting_is_a_placeholder_is_set_aside(make_routine, scripted,
                                                                 monkeypatch):
    """End to end, the specimen's own turn: the finish is deferred once, the refusal names the
    entry, and the run finishes when it accounts for the lines.

    The verifier is stubbed — this is the ACCOUNTING rung's test, and a live judge would
    consume a scripted reply for a verdict it says nothing about.
    """
    from rsched.engine import verifier

    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [])
    d = make_routine(slug="floorr", workflow_md=DONE_WHEN_MD)
    scripted([
        {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"},
        {**finish(summary="Test call to diagnose accounting field parsing."),
         "accounting": ["d1 met: test", "d2 not due: test"]},          # the specimen → deferred
        {**finish(summary="Figures checked, digest published."),
         "accounting": ["d1 met: the figures match the ledger",
                        "d2 not due: no digest was due this week"]},
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert status == "ok"
    rejected = [e["payload"] for e in events if e["type"] == "observation"
                and e["payload"].get("accounting")]
    assert len(rejected) == 1
    assert rejected[0]["accounting"]["placeholder"] == ["d1 met: test", "d2 not due: test"]
    assert "d1 met: test" in rejected[0]["message"]
    # the accounting that LANDED is the one that says something
    status_doc = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status_doc["accounting"][0] == "d1 met: the figures match the ledger"


# ---- the job checkpoint inherits it ------------------------------------------------------------

def test_a_job_checkpoint_refuses_a_placeholder_too(make_routine, scripted):
    """`jobops._checkpoint` answers for the JOB recipe's Done when through the same
    `accounting.problems`/`gaps` seam, so it inherits the floor without its own rule."""
    d = make_routine(slug="floorj")
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["capabilities"] = {"actions": [], "utils": [], "runs": "last", "jobs": "on"}
    cfg["permissions"] = []
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    jobs.save(d, {"version": 1, "deleted": [], "run": {}, "jobs": [
        {"id": "alpha", "title": "Alpha", "brief": "steward alpha", "state": "active"}]})
    (d / "jobs" / "alpha").mkdir(parents=True, exist_ok=True)
    (d / "jobs" / "alpha" / "main.md").write_text(
        "# Alpha\n\n## Done when\n\n- d1 — the alpha page is current\n")
    scripted([
        {"say": "Open it.", "kind": "job", "verb": "open", "id": "alpha"},
        {"say": "Checkpoint.", "kind": "job", "verb": "checkpoint", "id": "alpha",
         "outcome": "advanced", "summary": "x", "accounting": ["d1 met: test"]},   # refused
        {"say": "Checkpoint.", "kind": "job", "verb": "checkpoint", "id": "alpha",
         "outcome": "advanced", "summary": "x",
         "accounting": ["d1 met: republished rev 4"]},
        finish(summary="alpha processed"),
    ])
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    rows = [json.loads(line) for line in
            (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    obs = [r["payload"] for r in rows if r.get("type") == "observation"]
    refused = [o for o in obs if o.get("rejected") and "d1 met: test" in o.get("reason", "")]
    assert len(refused) == 1, obs
    assert jobs.find(jobs.load(d), "alpha")["last"]["accounting"] == ["d1 met: republished rev 4"]


# ---- what the console RENDERS ------------------------------------------------------------------

def test_the_console_renders_every_category_problems_can_report():
    """Lockstep, in the spirit of `BRIEF_FIELD` (tests/test_actions.py): `static/` is no-build
    and cannot import the engine, so a category the engine adds and the console does not render
    shows the operator a finish deferred for a reason they cannot see. That is exactly how the
    placeholder clause would have been missed.
    """
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "static" / "components"
          / "obs-accounting.js").read_text(encoding="utf-8")
    for key in accounting.problems({}, DONE, OUTCOMES):
        assert f"found.{key}" in js, key


def test_the_transcript_reads_the_accounting_renderers_from_their_own_module():
    """The extraction the ratchet forced (static/components/transcript.js was at its ceiling):
    the renderers moved out, so the import has to come back in — a no-build ES module that
    calls a function it does not import is a ReferenceError in the browser and nowhere else.

    The import follows the CALLER, which is the whole property worth pinning: F776 split the
    per-kind observation chain out of transcript.js into `obs-text.js`, taking the
    `accountingGaps` call (the finish-gate rung that names an incomplete accounting) with it,
    while `accountingLine` stayed with the `stopping_update` row that renders it. Each module
    must import what IT calls and nothing it does not — so this reads the caller of each
    renderer rather than assuming both live in one file.
    """
    from pathlib import Path

    components = Path(__file__).resolve().parents[1] / "static" / "components"
    module = (components / "obs-accounting.js").read_text(encoding="utf-8")
    callers = {"accountingGaps": "obs-text.js", "accountingLine": "transcript.js"}
    for fn, caller in callers.items():
        source = (components / caller).read_text(encoding="utf-8")
        assert f"export function {fn}(" in module      # defined there…
        assert f"function {fn}(" not in source         # …and nowhere else
        assert f"{fn}(" in source                      # …but still called here
        imports = [line for line in source.splitlines()
                   if "/static/components/obs-accounting.js" in line or (
                       line.startswith("import {") and fn in line)]
        assert any(fn in line for line in imports), (
            f"{caller} calls {fn}() without importing it from obs-accounting.js — a "
            "ReferenceError in the browser and nowhere else")


# ---- what the console COUNTS -------------------------------------------------------------------

def test_a_placeholder_entry_is_still_counted_by_its_verdict():
    """DELIBERATE: `accounting_counts` is a RULER, not a gate.

    It feeds the `met_rate` completeness signal (readmodels/change_signals.py), which compares
    runs of the same routine across releases, and `runevidence.quality_of` re-derives it from
    an old run's `status.json` — so discounting placeholders here would silently re-score every
    historical run, which is exactly what `COUNTED_SINCE` exists to prevent. The finish guard
    is where a placeholder is stopped; no new run can land one. The runs that already did stay
    counted the way they were counted then.
    """
    assert runrecord.accounting_counts(["d1 met: test", "d2 unmet: test"]) == \
        {"owed": 2, "met": 1, "unmet": 1, "not_due": 0}
