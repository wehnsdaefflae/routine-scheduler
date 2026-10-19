"""D195 — the finish seam asks a run for the flaw it knows about (arXiv 2609.36139).

"Language Models Are 'Insecure' Reporters" (Huang, Fan, Humayun, Chen, Qin, Chen, Navalpakkam,
Gu; 2026-09-30) measures the failure this engine's prompt surface did NOT cover. The engine has
always said a great deal about CLAIMS that outrun evidence — *"Finishing with claims of
unperformed work is the single worst failure this system knows"* — and nothing about the
opposite: a run OMITTING from its own account a *narrative-changing flaw* it knows about, which
the paper defines as an error or limitation that undermines an otherwise successful account of
work. Handed GPT-5.5 an experiment log with a planted negative result, the paper's judge found
the flaw flagged in **2 of 200** reports.

The measured intervention is a short honesty prompt APPENDED to the instruction that asks for
the report — *"Be honest in your response."* — which took that count to **190 of 200**
(abstract, p. 1; §3 "Can we reduce insecure reporting at inference time?", p. 5). It is quoted
VERBATIM in `engine/harness.py`, not paraphrased, because the paper tested "Be critical", "Be
thorough" and "Be skeptical" too and none of them reduced insecure reporting as consistently
(Figures 6 and 7) — so the wording is the finding, and a rewrite of it is an untested prompt.

Placed in the harness contract beside the claims sentence, for three reasons: the two failures
are opposites and a reader of one needs the other; message 0 is the one body compaction never
evicts (`window.clamp_to_cap`), so it is still in context on the turn the summary is written;
and it is unconditional — no capability, no kind projection and no routine setting can drop it
(`test_the_honesty_instruction_is_not_projected_away_with_a_run_s_kinds`).

The MECHANISM half of D195 is F715 (0.433.0): a background call whose result the run never read
is named in `result.md` by `engine/background_summary.settle_note`. That is the other direction
of the same problem — an instruction cannot surface a flaw the surface itself drops, and a run
cannot flag evidence it was never shown.
"""

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
    """`harness_contract` projects its kind bullets per run; this sentence is not a bullet.

    A routine whose kinds are narrowed to almost nothing still ends its run with a summary, so
    the instruction cannot sit behind a capability, a permission or a kind set.
    """
    d = make_routine(slug="narrow")
    contract = harness_contract(run_context(d, "20260712-070000"), kinds=["read_file", "finish"])
    assert INTERVENTION in contract
    assert CLAIMS_HALF in contract


def test_the_reserved_finish_turn_asks_for_what_is_unfinished():
    """The engine's ONE instruction that literally asks for a report (`loopnudge.reserve_finish`).

    The paper appends its honesty prompt to the instruction asking for the report, and this is
    that instruction: the budget is spent, the schema is narrowed to `finish`, and the run has
    one turn to account for itself. It is also the paper's "Hide Task Incompletion" scenario
    almost exactly — a required job that never completed, behind successful-looking progress —
    so the ending most likely to drop a flaw is the one that asks for it by name.
    """
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
