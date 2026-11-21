"""D195 — the finish seam asks a run for the flaw it knows about (arXiv 2609.36139)."""

from pathlib import Path
from types import SimpleNamespace

from helpers import run_context, server_config
from rsched.engine.composer import build_system_prompt, state_digest
from rsched.engine.harness import harness_contract

#: The paper's intervention, verbatim (§3, p. 5). A paraphrase is a different, untested prompt.
INTERVENTION = "Be honest in your response."

#: The claims half, already in the contract before D195 — the opposite failure.
CLAIMS_HALF = "claims of unperformed work"

DOC = Path(__file__).resolve().parents[1] / "docs" / "prompt-anatomy.md"


def _prompt(make_routine, tmp_path) -> str:
    d = make_routine(slug="honesty")
    ctx = run_context(d, "20260712-070000",
                      server=server_config(libraries_home=tmp_path / "libraries"))
    return build_system_prompt(ctx, "## Run flow", "task", state_digest(d, [], []), ["hello"])


def _paragraph(text: str, needle: str) -> str:
    for para in text.split("\n\n"):
        if needle in para:
            return para
    raise AssertionError(f"no paragraph of the composed prompt carries {needle!r}")


def test_the_finish_seam_asks_for_the_flaw_the_run_knows_about(make_routine, tmp_path):
    """The composed system prompt carries the paper's measured intervention, word for word."""
    assert INTERVENTION in _prompt(make_routine, tmp_path), (
        "the composed prompt no longer carries the honesty instruction D195 added "
        f"({INTERVENTION!r}, arXiv 2609.36139 §3) — a run writing its own account of its work "
        "reads only the claims half of the contract again"
    )


def test_the_omission_half_stands_beside_the_claims_half(make_routine, tmp_path):
    """One paragraph, both failures. Fabricating a success and omitting a known flaw are
    opposite ways to misreport the same run, and the paper's point is that the second is NOT
    covered by warnings about the first: the models it tested identify the planted flaws
    readily when asked directly (Table 6), so this is behavioural, not a capability gap."""
    para = _paragraph(_prompt(make_routine, tmp_path), CLAIMS_HALF)
    assert INTERVENTION in para, (
        "the honesty instruction drifted out of the paragraph that states the claims half — "
        "the two halves of what a run may say about its own work must be read together"
    )


def test_the_honesty_instruction_is_not_projected_away_with_a_run_s_kinds(make_routine):
    """`harness_contract` projects its kind bullets per run; this sentence is not a bullet."""
    d = make_routine(slug="narrow")
    contract = harness_contract(run_context(d, "20260712-070000"), kinds=["read_file", "finish"])
    assert INTERVENTION in contract
    assert CLAIMS_HALF in contract


def test_the_reserved_finish_turn_asks_for_what_is_unfinished():
    """The engine's ONE instruction that literally asks for a report (`loopnudge.reserve_finish`)."""
    from rsched.engine.loopnudge import reserve_finish

    appended: list[dict] = []
    events: list[tuple[str, dict]] = []
    loop = SimpleNamespace(
        messages=appended,
        ctx=SimpleNamespace(transcript=SimpleNamespace(
            event=lambda kind, payload: events.append((kind, payload)))),
        _finish_reserved=False, _budget_spent=None, _schema_off=True, _shed_schema_turns=3,
        action_schema=None)
    reserve_finish(loop, {"resource": "turns", "limit": 10,
                          "message": "turn budget exhausted (10)"})

    text = appended[-1]["content"]
    assert INTERVENTION.rstrip(".") in text, (
        "the reserved finish turn no longer carries the honesty instruction — the one turn a "
        "budget-spent run has to account for itself is where a flaw is easiest to omit"
    )
    for owed in ("what is unfinished", "what you could not get working", "what you found wrong"):
        assert owed in text, f"the reserved turn stopped asking for {owed!r}"
    assert events and events[0][0] == "user_injection"


def test_the_verbatim_example_in_the_docs_shows_the_honesty_instruction():
    """§5.1 of docs/prompt-anatomy.md quotes a whole composed system prompt and the Help tab
    renders it as what the orchestrator actually sees (F672). A contract sentence missing from
    that fence is a doc that shows a prompt the engine does not compose."""
    text = DOC.read_text(encoding="utf-8")
    fence = text[text.index("### 5.1 System prompt"):text.index("### 5.2")]
    assert INTERVENTION in fence, (
        "docs/prompt-anatomy.md §5.1 quotes a composed prompt without the D195 honesty "
        "instruction — re-render the fence from the real composer"
    )
