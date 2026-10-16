---
effect:
  with: sends email from your own mailboxes in your name — the text you approved, to the people you approved
  without: reads, drafts and files mail wherever its mailbox credential is granted, but never sends
  when: correspondence is part of the task — a sent mail cannot be recalled
tags: [communication, email, outbound, as-operator]
requires:
  utils: [gmail:send, fau-mail:send]
outbox:
  gmail:
    identity:
      secrets: [GMAIL_APP_PASSWORD, GMAIL_SOURCES]
    utils:
      gmail:
        never_sends: [mailboxes, list, read, attachments, draft, mark-read, trash, body-dump, --selftest]
        send_if: '--yes\b'
    check: '(?i)^util:gmail (list|read) .*--mailbox[ =]\S*(sent|all|gesendet)'
    run: gu gmail list --mailbox sent --days 14 --query "to:<recipient>"
  fau-mail:
    identity:
      secrets: [FAU_PASSWORD]
    utils:
      fau-mail:
        never_sends: [list, body, headers, attachments, draft, mark, delete, filters, --selftest]
        send_if: '^(?!.*--(dry-run|sent-only)\b)'
    check: '(?i)^util:fau-mail (list|headers|body) .*--folder[ =]\S*(sent|gesendet)'
    run: gu fau-mail list --folder Sent --to <recipient> --since <the draft's date>
---
# permission: outbound-mail — send email from the operator's own mailboxes

Send the text as it stands in his approval, not your draft (he edits drafts), to the addresses he
approved. One approval covers one send; a correction or a follow-up needs its own. If a wrong
mail went out, say so; never repair it with another.
A reply continues its thread: `In-Reply-To` carries the answered mail's `Message-ID`,
`References` the whole chain, and the subject stays the same behind `Re:` or `Fwd:`.
Keep the copy in Sent: it is his record of what went out in his name.
Before EVERY send, search Sent for this message — the same recipient, subject or thread, since
the draft was made: he may have sent it himself, or an earlier run did. If it is there, record
that it went out and do not send it again.
Before a draft goes up for approval, `pangram` flags passages that read as machine-written;
`plain-language` and `voice-rewrite` rewrite them.
