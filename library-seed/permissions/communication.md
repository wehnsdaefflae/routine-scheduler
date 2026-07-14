---
tags: [communication, policy, notification]
requires:
  utils: [discord]
---
# permission: communication — Discord as a second decision surface

Unlocks the reserved `discord` util: ONE channel beside the web UI, for decisions the user
must see promptly. The engine mirrors blocking questions to Discord automatically when
this permission is held — you normally do NOT message Discord yourself. Use the util
directly only when the workflow explicitly calls for an outbound notification, and then:
batch everything into ONE self-contained message (readable with zero run context,
answerable from a phone in one line), state the options and the default you will take
without a reply, and never send progress noise — results and FYI stay in the UI (LEDGER,
finish summary, deferred ask_user). Record any answer in the LEDGER so no future run
re-asks.
