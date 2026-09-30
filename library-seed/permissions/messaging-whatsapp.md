---
effect:
  with: sends WhatsApp messages from your own paired account
  without: reads the mirrored history wherever the session store is granted, but never sends
  when: the task needs to reach a person on WhatsApp — each message goes out as you
tags: [communication, messaging, outbound]
requires:
  utils: [whatsapp:send]
---
# permission: messaging-whatsapp — send WhatsApp messages as the operator

Voice notes and photos are messages: download them and transcribe the voice notes before you
call a chat answered or silent.
Pairing is the operator's act; a run never pairs a device.
