---
effect:
  with: send email from your own address
  without: can read and draft mail, but never send it
  when: sending is the point of the task — a sent message cannot be recalled
tags: [communication, email, outbound]
requires:
  utils: [fau-mail-send]
  util_tags: [smtp]
---
# permission: outbound mail — send email in the user's name

Unlocks the utils that TRANSMIT email from the user's own address. Reading and drafting are
not gated by this: opening a mailbox you already hold credentials for is recoverable, and
sending is not. A message that has left the building cannot be recalled, is read as the user's
own words by its recipient, and can damage a working relationship the routine does not own.

Hold this only for a routine whose task genuinely includes correspondence, and then:

- **Never send unshown or unapproved text.** The user reviews and approves every outgoing
  message on its status page first, in an editable field. What you send is the text he
  approved, read from his approval — not the draft you wrote, which he may have edited.
- **One approval authorizes ONE send of that text.** It does not authorize a correction, a
  follow-up, or a re-send. If a wrong message went out, stop and say so; do not repair it by
  sending more mail.
- **Write in the user's voice and register**, matching what he has actually sent before, and
  learn from every edit he makes to a draft rather than reproducing the same wording.
- **Thread the reply** into the conversation it belongs to instead of opening a new one, and
  route a question to whoever owns it rather than to whoever is most senior on the thread.
- **Verify before you assert.** Read the saved correspondence before claiming something is
  outstanding — chasing a partner for work they already delivered costs the user standing that
  is not yours to spend.
- **Respect the clock.** Honour the user's rules about when correspondence may go out, and
  leave proof of every send where he can find it.

Sending is the last step of work already done, never a shortcut past it.
