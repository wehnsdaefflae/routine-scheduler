"""Unit tests for the D31=B finish claim guard (finding F127): reject an ok-finish whose
summary claims a high-signal action (report/ask_user/schedule_run) the run never took."""
from rsched.engine.finish_guard import (
    normalize_escaped_newlines,
    unbacked_action_claims,
)

# the real finish summary of uncensored-model-radar:20260719-181148 (excerpt) — the run that
# claimed a report it never filed; F127's positive example.
RADAR_SUMMARY = (
    "Steady run — status heartbeat; no watchlist change. Retested one model (MIXED), "
    "escalated the OpenRouter two-key gap to self-audit, and fixed a false-qualification "
    "bug in the test util. OPENROUTER_UTIL_KEY is set (73 chars). Filed report to "
    "self-audit asking them to identify both keys and update model-refusal-test."
)


def test_real_radar_summary_flagged():
    # radar took util/write_util/finish but NO report — must be flagged for a non-meta routine
    got = unbacked_action_claims(RADAR_SUMMARY, {"util", "write_util", "finish"}, is_meta=False)
    assert got == ["report"], got


def test_meta_routine_exempt_even_on_radar_summary():
    # a meta routine (self-audit) quoting the same text must NOT self-reject
    assert unbacked_action_claims(RADAR_SUMMARY, {"util", "spawn", "finish"}, is_meta=True) == []


def test_no_flag_when_action_actually_taken():
    assert unbacked_action_claims("Filed report to self-audit.", {"report"}, is_meta=False) == []


def test_negation_not_flagged():
    s = "No report was warranted; did not file a report this run."
    assert unbacked_action_claims(s, set(), is_meta=False) == []


def test_natural_language_paraphrase_not_flagged():
    # no literal engine token -> too ambiguous to reject on (precision over recall)
    s = "Escalated the issue to the operator and asked them to confirm the keys."
    assert unbacked_action_claims(s, set(), is_meta=False) == []


def test_multiple_action_tokens():
    s = "Queued a schedule_run for +3d and posted a report about the failure."
    assert unbacked_action_claims(s, set(), is_meta=False) == ["report", "schedule_run"]
    assert unbacked_action_claims(s, {"schedule_run", "report"}, is_meta=False) == []


def test_bare_discussion_token_not_flagged():
    # mentioning the action name without an affirmative completion verb is not a claim
    s = "The report action is ungated by default; ask_user has a blocking mode."
    assert unbacked_action_claims(s, set(), is_meta=False) == []


def test_empty_and_actionless_summaries():
    assert unbacked_action_claims("", set(), is_meta=False) == []
    assert unbacked_action_claims("Did the work, all green.", {"util"}, is_meta=False) == []


# ---- R82: normalize double-escaped newlines in finish summaries ---------------------------


def test_wholesale_double_escape_is_repaired():
    # a summary that double-escaped EVERY newline (literal \n, no real newline) is repaired so
    # result.md / the digest render real line breaks instead of verbatim "\n" (F82/R82).
    bad = "**Done.**\\n\\nShipped the fix.\\nGate green."
    assert normalize_escaped_newlines(bad) == "**Done.**\n\nShipped the fix.\nGate green."


def test_real_newlines_present_leaves_literal_backslash_n_intact():
    # if the summary ALREADY has real newlines, a literal \n is intentional (e.g. a code
    # snippet showing an escape) and must be preserved untouched.
    ok = "Line one.\nExplained `print('a\\nb')` renders a break.\nLine three."
    assert normalize_escaped_newlines(ok) == ok


def test_plain_text_and_empty_untouched():
    assert normalize_escaped_newlines("just one line, no breaks at all") == \
        "just one line, no breaks at all"
    assert normalize_escaped_newlines("") == ""


def test_escaped_tab_and_crlf_normalized_in_wholesale_case():
    bad = "col1\\tcol2\\r\\nval1\\tval2"
    assert normalize_escaped_newlines(bad) == "col1\tcol2\nval1\tval2"
