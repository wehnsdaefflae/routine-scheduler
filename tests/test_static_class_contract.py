"""Every class a static/ module mounts is STYLED, and a STATE class is REACHABLE where it is
mounted (R2885). The widened form of the conv-*/pane-* class contract that used to live in
tests/test_static_layout.py, which now points here. The extraction is tests/class_survey.py;
this module is the two rules and the design record of what they waive.

WHY TWO RULES, MEASURED. `views.css:300-328` is a design document in comments: the refusal's
dashed border means "nothing is known about this work", a cancel takes the injection palette
because "nothing failed", an oversight rung keeps the injection's weight so "a reader must be
able to tell a rung redirecting the run from the operator doing it".
`tests/test_stylesheet_tokens.py` machine-checks the palette's INPUTS — every colour a token,
every token defined for both themes. Nothing checked the OUTPUT, and 0.399.2 is what that cost:
`ladderstrip.js` emitted a bare `bad`/`warn`/`ok` and *a rung that BROKE rendered in exactly the
ink of a rung that declined to run*. Found by rendering the strip, not by reading it.

Widening the old check's SCOPE alone would NOT have caught it. `bad` IS named in a stylesheet —
`.test-result.bad` (views.css:511), `.outcome-move.bad` and `.outcome-table .bad` (dev.css:10) —
and `ladderstrip.js` can reach none of them: it mounts neither `test-result` nor `outcome-table`.
Verified by reproducing the defect: with the three `.ladderstrip .bad|.warn|.ok` rules removed,
rule A below PASSES and rule B reds, naming all three tokens and the rules that cannot match
them. So:

  rule A  every class token a module mounts is NAMED by some rule in some stylesheet
          (`test_every_mounted_class_is_styled`) — waivable through ALLOWLIST below;
  rule B  for the palette's STATE words, being named somewhere is not enough: some rule naming
          the token must be REACHABLE from the module that mounts it
          (`test_a_state_class_is_reachable_where_it_is_mounted`) — never waivable, because the
          allowlist is for classes that carry no meaning and a state word carries nothing else.
"""
from __future__ import annotations

from tests.class_survey import reachable, selectors_naming, survey

#: The console's STATE vocabulary — the words that mean "what has become of this thing", which is
#: the whole subject of the palette (base.css:455-485 `.chip.*`, views.css:594-597 `.dot.*`,
#: views.css:296-302 `.ev.*`, views.css:1336-1338 + 1387 the surface's severities, dev.css the
#: outcome panel's). A word here is checked by rule B wherever a module mounts it; one no module
#: mounts is never checked, so the list may name the whole vocabulary.
SEVERITY = frozenset({
    "ok", "bad", "warn", "err", "error", "failed", "failing", "finished", "aborted", "partial",
    "idle", "deferred", "paused", "running", "starting", "queued", "active", "waiting_user",
    "blocking", "blocked", "danger", "summons", "signal", "refusal", "cancelled", "held", "lost",
    "skipped", "stuck", "pending", "problem", "systemic", "redundancy", "improvement", "info",
    "blocks", "interrupts", "no-work", "unservable", "disabled", "attention", "live",
})

#: CLASSES THAT CARRY NO MEANING AND NEED NO RULE — a design record, not a suppression dump:
#: reason → the tokens waived under it. Rule A skips exactly these; rule B skips nothing.
#: `test_the_allowlist_holds_no_stale_waiver` reds on an entry no longer mounted or since given a
#: rule, so every line here stays a live statement about the tree. 44 tokens waived of the 746
#: mounted (2026-10-24, 0.447.3) — an entry, never a pattern: a prefix rule would have waived the
#: `unservable` this check was written to find.
ALLOWLIST: dict[str, frozenset[str]] = {
    # A class that exists to be LOCATED, not painted: the browser suite selects on it.
    # components/held-accept.js:12 says so in its own words — "the `.held-accept` hook exists
    # for the tests, not as the place the meaning lives".
    "a DOM hook the browser suite selects on": frozenset({
        "activity-panel",    # tests/ui/test_activity_feed.py:33, test_views_coverage.py:123
        "att-file",          # tests/ui/test_transcript_events.py:216
        "avail-fold",        # tests/ui/test_setup_check.py:315
        "chg-table",         # tests/ui/test_view_teardown.py:306, test_bus_budget.py:52
        "delib",             # tests/ui/test_flows.py:598
        "fit-table",         # tests/ui/test_changes.py:193
        "gate-cell",         # tests/ui/test_gate_history.py:61
        "held-accept",       # components/held-accept.js:12
        "hist",              # tests/ui/test_flows.py:259 (`.file-row.hist`)
        "ladder-set",        # tests/ui/test_ladder_settings.py:51
        "oneshot-at",        # tests/ui/test_schedule_once.py:48
        "oneshot-reason",    # tests/ui/test_schedule_once.py:49
        "oneshot-row",       # tests/ui/test_schedule_once.py:29
        "operator-flag",     # tests/ui/test_refusal_flag.py:163
        "q-settled",         # tests/ui/test_decision_decline.py:58
        "runs-box",          # tests/ui/test_routine_page.py:270
        "triggers-body",     # tests/ui/test_triggers.py:55
    }),
    # A bare flex/grid/table wrapper: its children are styled, it holds them in a row or a
    # column and says nothing about them. `conv-head` is carried over verbatim from the
    # conv-*/pane-* check this module supersedes — it holds the styled `.conv-head-row` pair.
    "a plain container whose children carry the styling": frozenset({
        "ab-what", "acct-main", "chart-plot", "col", "conv-head", "dw-main", "fl-add", "hb-row",
        "hero-hb", "oneshot-body", "pat-list", "rem-body", "rem-head", "rem-main", "rem-picker",
        "rn-node", "sr-node", "surface-table",
    }),
    # The element already wears a styled token that gives it its look (`muted`, `pill`, `chip
    # bare disabled`); this one names the slot for a reader and adds no appearance of its own.
    "a variant name beside a styled token on the same element": frozenset({
        "gate-tally",   # `muted gate-tally`
        "gate-why",     # `muted gate-why`
        "soft",         # `pill soft` — the quiet pill IS the base `.pill` look
        "turn-usage",   # `muted turn-usage`
        "v-more",       # `chip bare disabled v-more`
    }),
    # A text span that inherits its parent's type and colour. Named so a rule CAN reach it later
    # and so a reader can find it; nothing today depends on it looking different.
    "a text span that inherits its parent's type": frozenset({
        "lt-usage", "pb-when", "pg-name", "turn-model",
    }),
}

#: Floors, so a refactor that breaks the extraction REDS instead of passing green over nothing.
#: Measured 2026-10-24 on 0.447.3: 155 js modules, 5 stylesheets, 2254 class-bearing elements,
#: 746 distinct tokens mounted, 836 tokens named in CSS, 88 state-class checks.
MIN_JS_FILES = 140
MIN_STYLESHEETS = 5
MIN_ELEMENTS = 1800
MIN_TOKENS = 600
MIN_NAMED = 600
MIN_STATE_CHECKS = 50


def _waived() -> set[str]:
    return set().union(*ALLOWLIST.values())


def test_every_mounted_class_is_styled():
    """Rule A: a class a module mounts that no rule anywhere names does nothing at all."""
    s = survey()
    waived = _waived()
    missing: dict[str, list[str]] = {}
    for rel, tokens in s["mounted"].items():
        for token in sorted(tokens - s["named"] - waived):
            missing.setdefault(token, []).append(rel)
    assert not missing, ("classes mounted but styled by no rule in any stylesheet — give each a "
                         f"rule, or waive it in ALLOWLIST with its reason: {missing}")


def test_a_state_class_is_reachable_where_it_is_mounted():
    """Rule B: for a STATE word a rule somewhere is not enough. 0.399.2's bare `bad` was named
    by `.test-result.bad`, which `ladderstrip.js` could never match — so a rung that broke and
    a rung that declined to run rendered in the same ink."""
    s = survey()
    unreachable = {}
    for rel, tokens in s["mounted"].items():
        for token in sorted(t for t in tokens if t in SEVERITY):
            if reachable(token, rel, s) is None:
                unreachable[f"{rel}::{token}"] = (selectors_naming(token, s)
                                                  or ["no rule names it at all"])
    assert not unreachable, (
        "state classes the mounting module can never match — two states that mean opposite "
        f"things render identically (0.399.2). Scope the rule, or mount what it asks for, or "
        f"make the class compound: {unreachable}")


def test_the_allowlist_holds_no_stale_waiver():
    """The allowlist is a design record: an entry no longer mounted, or since given a rule, is a
    statement that has stopped being true — and a STATE word may never be waived."""
    s = survey()
    all_mounted = set().union(*s["mounted"].values())
    for reason, tokens in ALLOWLIST.items():
        for token in sorted(tokens):
            assert token in all_mounted, \
                f"ALLOWLIST waives {token!r} ({reason}) but no static/ module mounts it — drop it"
            assert token not in s["named"], \
                f"ALLOWLIST waives {token!r} ({reason}) but a rule now styles it — drop it"
        assert not (tokens & SEVERITY), \
            f"ALLOWLIST cannot waive a STATE word ({reason}): {sorted(tokens & SEVERITY)}"


def test_the_survey_is_not_vacuous():
    """A refactor that breaks the extraction must RED, not pass green over nothing."""
    s = survey()
    assert s["modules"] >= MIN_JS_FILES, f"only {s['modules']} js modules found"
    assert len(s["sheets"]) >= MIN_STYLESHEETS, f"only {s['sheets']} stylesheets found"
    assert s["elements"] >= MIN_ELEMENTS, f"only {s['elements']} class-bearing elements found"
    assert len(set().union(*s["mounted"].values())) >= MIN_TOKENS, "too few class tokens found"
    assert len(s["named"]) >= MIN_NAMED, f"only {len(s['named'])} tokens named in the sheets"
    checks = sum(len({t for t in toks if t in SEVERITY}) for toks in s["mounted"].values())
    assert checks >= MIN_STATE_CHECKS, f"only {checks} state-class checks — rule B went blind"
