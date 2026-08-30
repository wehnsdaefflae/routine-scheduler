---
effect:
  with: read mirrored WhatsApp history and send messages as your paired account
  without: cannot reach WhatsApp at all
  when: the task needs to reach a person there — it speaks as you
tags: [communication, messaging, outbound]
requires:
  utils: [whatsapp]
---
# permission: whatsapp messaging — reach a person on WhatsApp

Unlocks the `whatsapp` util: list chats, read mirrored history and send text as the paired WhatsApp account. Pairing is the operator's one-time act.

Hold it only for a routine whose task genuinely needs to reach a person that way, and then:

- **One message, self-contained.** Readable with zero run context, answerable in one line from
  a phone. Never a progress update, never a thread of fragments — results and FYI belong in
  the web UI (LEDGER, finish summary, deferred `ask_user`).
- **State the default.** Say what you will do if there is no reply, so silence is an answer and
  the user is never obliged to respond.
- **Respect the hour and the day.** These channels do not wait for office hours; the user's
  rules about when they may be contacted apply here more strictly than anywhere else.
- **Never a second channel for the same thing.** If it is already on the status page or in a
  Discord ping, sending it again here is noise wearing the clothes of urgency.
- **Record the answer** in the LEDGER so no future run re-asks something already settled.

Holding one channel never grants another, `messaging-discord` included. A Discord post sits
in a room the user visits when they choose; a message on a personal messenger arrives on their
phone, next to messages from their family, and is read by whoever is standing next to them.
That is a different act, so it is a different permission.

If you are unsure whether a message earns this channel, it does not. Put it on the page.
