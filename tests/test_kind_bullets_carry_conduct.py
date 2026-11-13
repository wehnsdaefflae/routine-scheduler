"""The kind bullets carry IDENTITY and CONDUCT — never a field recital the wire already has.

The operator's decision of 2026-11-13, building goal b5's second step: *"Cut the bullets to
conduct only — drop every field recital the enforced wire already carries."* The block was
21,421 chars on every turn of every run (21 live captures, 2026-11-12), and it was the THIRD
copy of one contract in a single request — `endpoint.complete(schema=…)` puts `ACTION_SCHEMA`'s
own `description` prose in front of the model structurally, `schemaindex.index` states which
fields each kind takes, and the bullets then said it again in prose nothing enforced.

What these tests pin is the ARCHITECTURE, not a char count (`tests/test_prompt_budget.py` holds
the size):

1. no bullet sentence is the schema's own words — a recital that creeps back is caught here,
   where it is cheap, rather than in a prompt-size review nobody runs;
2. every bullet still opens with its kind's IDENTITY, because `ACTION_SCHEMA["kind"]` is a bare
   enum: the wire says a kind EXISTS and never what it is for;
3. the conduct sentences that exist nowhere else survive. That was the operator's stated reason
   for choosing this cut over moving conduct into library rules: not every routine holds the
   corresponding rule, so a rule-only home loses the instruction for those that do not.

The duplication test reads 4-grams rather than whole sentences: a recital reappears paraphrased
as often as verbatim, and a 4-word window is short enough to catch a re-worded one while long
enough that ordinary English ("the run's own", "at most one") does not trip it.
"""

from __future__ import annotations

import re

from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.kindprose import KIND_PROSE, kind_bullets

#: A window short enough to catch a paraphrase, long enough not to fire on ordinary English.
GRAM = 4

#: The share of a sentence's windows that must be on the wire before it reads as a recital.
#: 0.25 admits a sentence that quotes a field NAME and a few of its words — unavoidable when
#: the conduct is about that field — and refuses one that restates the description.
RECITAL = 0.25

#: The ONE place where saying the same thing twice is the point. D204 (the operator,
#: 2026-10-23) requires `partial`'s restriction to be stated identically on three surfaces at
#: once — this bullet, `ACTION_SCHEMA`'s `status` description and the harness honesty
#: paragraph — because a run that reads two different definitions of its own status picks the
#: wrong one, which is what the fleet did (232 `ok` and 4 `partial` in the 14 days to
#: 2026-10-23, every `partial` self-chosen and none of them blocked).
#: `tests/test_partial_means_blocked.py` pins all three copies and is the authority; this
#: carve-out exists so the two pins cannot demand opposite things. It is deliberately ONE
#: literal prefix, not a pattern: a second deliberate agreement has to be argued here in the
#: same way, where the next reader meets the reason.
#: The 2026-11-13 cut DID remove this bullet's elaborations around the trichotomy (what counts
#: as a shortfall, that a waiting decision is not a blocker); those really are on the wire
#: verbatim and are not restored by the carve-out.
AGREED_WITH_THE_WIRE = ("- finish: end the run with status ok",)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _descriptions() -> list[str]:
    """Every `description` in the schema, nested sub-fields included."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("description"), str):
                found.append(node["description"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(ACTION_SCHEMA)
    return found


def _wire_grams() -> set[tuple[str, ...]]:
    words = _words(" ".join(_descriptions()))
    return {tuple(words[i:i + GRAM]) for i in range(len(words) - GRAM + 1)}


def _sentences(prose: str) -> list[str]:
    flat = prose.replace("\\\n", " ").replace("\n", " ")
    return [s.strip() for s in re.split(r"(?<=[.!?]) +", flat) if s.strip()]


def test_no_bullet_sentence_restates_a_field_description():
    """A sentence mostly made of the schema's own words is a recital, not conduct."""
    grams = _wire_grams()
    offenders = []
    for covers, prose in KIND_PROSE:
        for sentence in _sentences(prose):
            if sentence.startswith(AGREED_WITH_THE_WIRE):
                continue
            words = _words(sentence)
            if len(words) < 8:
                continue
            windows = [tuple(words[i:i + GRAM]) for i in range(len(words) - GRAM + 1)]
            share = sum(1 for w in windows if w in grams) / len(windows)
            if share >= RECITAL:
                offenders.append((covers[0], round(share, 2), sentence[:160]))
    assert not offenders, (
        "these bullet sentences restate what ACTION_SCHEMA's own descriptions already say, and "
        "the enforced wire carries those descriptions on every turn — cut the sentence rather "
        "than rewording it (the operator's bound: no field description is reworded):\n"
        + "\n".join(f"  [{lead}] {share:.0%} on the wire: {text}"
                    for lead, share, text in offenders))


def test_every_agreed_duplication_is_still_a_real_sentence():
    """A carve-out that matches nothing is not protection — it is a hole left open.

    If the finish bullet is reworded, this entry stops matching and the duplication check
    silently stops covering whatever replaced it. Fail here instead, where the reason is.
    """
    sentences = [s for _, prose in KIND_PROSE for s in _sentences(prose)]
    for prefix in AGREED_WITH_THE_WIRE:
        assert any(s.startswith(prefix) for s in sentences), (
            f"AGREED_WITH_THE_WIRE still excuses {prefix!r}, which no bullet sentence starts "
            "with any more — either restore the wording D204 requires on all three surfaces "
            "(tests/test_partial_means_blocked.py is the authority) or drop this entry")


def test_every_bullet_opens_with_what_its_kind_is():
    """`ACTION_SCHEMA["kind"]` is a bare enum, so identity is the prompt's to state."""
    enum = set(ACTION_SCHEMA["properties"]["kind"]["enum"])
    for covers, prose in KIND_PROSE:
        lead = covers[0]
        assert lead in enum, f"KIND_PROSE describes {lead!r}, which the schema's enum omits"
        head = _sentences(prose)[0]
        assert head.startswith(f"- {lead}"), f"the {lead!r} bullet does not open by naming it"
        # the identity clause is what follows the colon after the kind's name: "- util: run a
        # global util." A bullet cut down to its name alone would leave the enum's bare word.
        after = head.split(":", 1)
        assert len(after) == 2 and len(after[1].strip()) >= 12, (
            f"the {lead!r} bullet states no identity — the wire's `kind` enum names the kind "
            "and nothing else, so the prompt owes what it IS")


def test_the_conduct_that_lives_nowhere_else_survives():
    """The operator's reason for keeping conduct in the bullets rather than in rules."""
    block = kind_bullets({covers[0] for covers, _ in KIND_PROSE},
                         util_confirm="", ask_timeout_min=480)
    for conduct in (
        "Ask sparingly",                                   # ask_user
        "write the util (or a scripts/ helper)",            # shell: promote what you repeat
        "it gives no reasons, so ask `llm` when you need one",   # decide
        "guessing wrong sends the work to someone who will bounce it",   # report
        "never because it got hard",                       # goal: dropping one
        "Children never outlive you",                      # kill/wait
        "revise notes that turned out wrong",               # memory
        "apply it to the case in front of you",             # read_rule
        "never let one job's work hide inside another's checkpoint",      # job
        # NOT "a checkpoint accounts for that recipe's `## Done when` lines": this very test
        # caught that sentence at 31% on the wire (the `verb` and `accounting` descriptions
        # carry it), and it was cut. A conduct list is only honest if its own entries pass the
        # duplication check above — so an entry added here must be conduct, not a recital.
    ):
        assert conduct in block, (
            f"the conduct sentence {conduct!r} is gone from the kind bullets. It exists nowhere "
            "else for a routine holding no corresponding library rule, which is why the "
            "operator chose 'conduct only' over moving conduct into the rules.")


def test_a_bullet_never_recites_a_field_the_schema_describes_in_full():
    """A surviving sentence may NAME a field, but not teach what the schema teaches.

    The check is narrow on purpose: a sentence naming exactly one field in backticks and
    overlapping that ONE field's description is the shape the 2026-11-13 cut removed five of,
    and the shape most likely to come back when someone adds a bullet.
    """
    props = ACTION_SCHEMA["properties"]
    offenders = []
    for covers, prose in KIND_PROSE:
        for sentence in _sentences(prose):
            named = {f for f in re.findall(r"`([a-z_]+)`", sentence)
                     if isinstance(props.get(f), dict) and props[f].get("description")}
            if len(named) != 1:
                continue
            field = named.pop()
            desc = _words(props[field]["description"])
            grams = {tuple(desc[i:i + GRAM]) for i in range(len(desc) - GRAM + 1)}
            words = _words(sentence)
            if len(words) < 8:
                continue
            windows = [tuple(words[i:i + GRAM]) for i in range(len(words) - GRAM + 1)]
            share = sum(1 for w in windows if w in grams) / len(windows)
            if share >= RECITAL:
                offenders.append((covers[0], field, round(share, 2), sentence[:140]))
    assert not offenders, (
        "these sentences teach one field what that field's own description already teaches:\n"
        + "\n".join(f"  [{lead}] `{field}` {share:.0%}: {text}"
                    for lead, field, share, text in offenders))
