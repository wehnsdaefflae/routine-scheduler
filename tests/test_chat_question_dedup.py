"""F264 + R132: a blocking question renders ONE full answer form, and its buttons in view.

A conversation renders a pending question on TWO surfaces: inline in the chat transcript
(chat.js `questionInline`, fed by the `question` transcript event) and in the pinned panel
above the composer (conversations.js `questionPanel`, fed by status.json's question). The
run view mirrors this (transcript.js `questionNode` + the page-top panel). Two rules hold:

- F264: the pinned panel owns the ONE full form (free text, ask-back, expires chrome). The
  inline rendering of a BLOCKING question must never build a second full form.
- R132: the inline rendering must still carry the one-click controls (option buttons /
  typed-decision buttons, `quick: true` in answerform.js) — a util approval whose only
  buttons live on another surface sent the operator hunting through the Decisions tab.

Source-level guard (like test_static_imports / test_lint) because the console is no-build
vanilla ES modules; the browser-level half lives in tests/ui/test_flows.py (the run-view
inline approve flow), which needs the Playwright harness.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _body(path: str, func: str) -> str:
    text = (STATIC / path).read_text(encoding="utf-8")
    m = re.search(r"function " + func + r"\(ev\)\s*\{(.*?)\n  \}", text, re.DOTALL)
    assert m, f"{func}(ev) not found in {path} — did it move/rename?"
    return m.group(1)


def _blocking_branch_is_quick_only(body: str, where: str) -> None:
    # The non-deferred (blocking) branch must exist and build ONLY the quick strip.
    assert 'p.mode !== "deferred"' in body, (
        f"{where} must branch on deferred vs blocking mode — a blocking question gets the "
        "quick strip, never a second full form (F264)")
    blocking = body[body.index('p.mode !== "deferred"'):]
    blocking = blocking[:blocking.index("const form")]   # up to the deferred full form
    assert "quick: true" in blocking, (
        f"{where}: the blocking branch must render the one-click strip (answerForm "
        "quick: true) — without it an approval has no buttons where the user reads (R132)")
    assert "placeholder" not in blocking, (
        f"{where}: the blocking branch must not build a free-text form — the pinned panel "
        "owns the one full form (F264)")


def test_chat_inline_blocking_is_quick_strip_only():
    _blocking_branch_is_quick_only(_body("components/chat.js", "questionInline"),
                                   "chat.js questionInline")


def test_transcript_inline_blocking_is_quick_strip_only():
    _blocking_branch_is_quick_only(_body("components/transcript.js", "questionNode"),
                                   "transcript.js questionNode")


def test_answerform_quick_mode_drops_the_free_text_row():
    """quick mode is buttons-only by construction: no input row, no ask-back — so a quick
    strip can never grow into the duplicate full form F264 was about."""
    text = (STATIC / "components" / "answerform.js").read_text(encoding="utf-8")
    assert "const row = quick ? null" in text, (
        "answerform.js quick mode must omit the free-text row entirely")
    assert "askBack && !quick" in text, (
        "answerform.js quick mode must omit the ask-back button")
