---
effect:
  with: read a Discord channel and post to it as your linked account
  without: cannot reach Discord at all
  when: the task needs to reach a person there — it speaks as you
tags: [communication, messaging, outbound]
requires:
  utils: [discord]
---
# permission: discord messaging — reach a person on Discord

Unlocks the `discord` util: read the channel's recent messages and post to it as the linked
account. The account is linked once by the operator and its credentials live in the Secrets
store — a run never authenticates.

Hold it only for a routine whose task genuinely needs to reach a person that way, and then:

- **One message, self-contained.** Readable with zero run context, answerable in one line from
  a phone. Never a progress update, never a thread of fragments — results and FYI belong in
  the web UI (LEDGER, finish summary, deferred `ask_user`).
- **State the default.** Say what you will do if there is no reply, so silence is an answer and
  the user is never obliged to respond.
- **Respect the hour and the day.** These channels do not wait for office hours; the user's
  rules about when they may be contacted apply here more strictly than anywhere else.
- **Never a second channel for the same thing.** If it is already on the status page, sending
  it again here is noise wearing the clothes of urgency.
- **Record the answer** in the LEDGER so no future run re-asks something already settled.

Holding one channel never grants another. A Discord post sits in a room the user visits when
they choose; a message on a personal messenger arrives on their phone, next to messages from
their family, and is read by whoever is standing next to them. Those are different acts, so
they are different permissions.

Nothing here is a decision surface. A decision the user must make is `ask_user` — it lands on
the Decisions page and reaches their phone by browser push. Never route a decision through this
channel and never treat a reply here as an answer to one.

If you are unsure whether a message earns this channel, it does not. Put it on the page.
