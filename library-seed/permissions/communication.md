---
tags: [communication, policy, notification]
requires:
  utils: [discord, signal, telegram, whatsapp, zulip]
  util_tags: [chat, messaging]
---
# permission: communication — chat channels as a second decision surface

Unlocks the chat-channel utils: `discord`, plus every util tagged `chat` or `messaging`
(`signal`, `telegram`, `whatsapp`, `zulip`, and any the library gains later). These are ONE
channel beside the web UI, for decisions the user must see promptly. Discord is the
engine-integrated one; the others are reached only by calling their util deliberately, and
landing in someone's personal messenger is a far bigger interruption than a Discord ping — so
hold a channel only if the routine's task truly needs it, and apply everything below to
whichever one you use. The engine mirrors blocking questions to Discord automatically when
this permission is held — you normally do NOT message Discord yourself. Use the util
directly only when the workflow explicitly calls for an outbound notification, and then:
batch everything into ONE self-contained message (readable with zero run context,
answerable from a phone in one line), state the options and the default you will take
without a reply, and never send progress noise — results and FYI stay in the UI (LEDGER,
finish summary, deferred ask_user). Record any answer in the LEDGER so no future run
re-asks.
