"""F264: a blocking approval must render ONCE in a conversation, not twice.

A conversation renders a pending question on TWO surfaces: inline in the chat transcript
(chat.js `questionInline`, fed by the `question` transcript event) and in the pinned panel
above the composer (conversations.js `questionPanel`, fed by status.json's question). For a
DEFERRED question only the inline chat form is actionable (the panel is empty — deferred
questions never set status.question). For a BLOCKING question the panel owns the answer
(ask-back / expires / util-approval chrome), so the inline chat card must be STATIC text —
otherwise the same approval shows twice, both actionable (the operator's F264 report).

The run view's transcript (transcript.js) already follows this exact rule ("Blocking ones
stay with the run view's panel"). This guards that chat.js keeps the same deferred-only gate.
It is a source-level guard (like test_static_imports / test_lint) because the console is
no-build vanilla ES modules; a browser-level assertion would need a live conversation with a
seeded blocking question, which the UI harness cannot yet seed.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_chat_inline_answer_form_is_deferred_only():
    text = (STATIC / "components" / "chat.js").read_text(encoding="utf-8")
    m = re.search(r"function questionInline\(ev\)\s*\{(.*?)\n  \}", text, re.DOTALL)
    assert m, "questionInline(ev) not found in chat.js — did it move/rename?"
    body = m.group(1)
    # The early-return guard before building the answerForm must exclude non-deferred
    # (blocking) questions, so a blocking question renders as static text and is answered
    # only in the pinned questionPanel.
    assert 'p.mode !== "deferred"' in body, (
        "chat.js questionInline must gate its inline answer form on deferred mode "
        '(p.mode !== "deferred" in the pre-form early return) so a BLOCKING approval does '
        "not render an actionable card in BOTH the chat and the pinned panel (F264).")
    # And that guard must sit before the answerForm is constructed (a return, not dead code).
    guard_at = body.index('p.mode !== "deferred"')
    form_at = body.index("answerForm(")
    assert guard_at < form_at, "the deferred-mode guard must precede the answerForm construction"
