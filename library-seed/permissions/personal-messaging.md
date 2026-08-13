---
tags: [communication, messaging, outbound]
requires:
  utils: [signal, telegram, whatsapp, zulip]
  util_tags: [chat, messaging]
---
# permission: personal messaging — reach a person on their own messenger

Unlocks the personal chat-channel utils — `signal`, `telegram`, `whatsapp`, `zulip`, and every
util tagged `chat` or `messaging` the library gains later. Deliberately separate from
`communication` (Discord): a Discord ping sits in a room the user visits when they choose,
while a message on a personal messenger arrives on their phone, next to messages from their
family, and is read by whoever is standing next to them. That is a different act, so it is a
different permission — holding one never grants the other.

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

If you are unsure whether a message earns this channel, it does not. Put it on the page.
