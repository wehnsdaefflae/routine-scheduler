---
effect:
  with: sends email from your own mailboxes in your name — the text you approved, to the people you approved
  without: reads, drafts and files mail wherever its mailbox credential is granted, but never sends
  when: correspondence is part of the task — a sent mail cannot be recalled
tags: [communication, email, outbound]
requires:
  utils: [gmail:send, fau-mail:send]
---
# permission: outbound-mail — send email from the operator's own mailboxes

Send the text as it stands in his approval, not your draft (he edits drafts), to the addresses he
approved. One approval covers one send; a correction or a follow-up needs its own. If a wrong
mail went out, say so; never repair it with another.
A reply continues its thread: `In-Reply-To` carries the answered mail's `Message-ID`,
`References` the whole chain, and the subject stays the same behind `Re:` or `Fwd:`.
Keep the copy in Sent: it is his record of what went out in his name.
Before a draft goes up for approval, `pangram` flags passages that read as machine-written;
`plain-language` and `voice-rewrite` rewrite them.
