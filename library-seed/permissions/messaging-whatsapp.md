---
effect:
  with: sends WhatsApp messages from your own paired account
  without: reads the mirrored history wherever the session store is granted, but never sends
  when: the task needs to reach a person on WhatsApp — each message goes out as you
tags: [communication, messaging, outbound, as-operator]
requires:
  utils: [whatsapp:send]
outbox:
  whatsapp:
    identity:
      paths: [~/whatsapp-sessions]
    utils:
      whatsapp:
        never_sends: [pair, status, sync, chats, messages, contacts, search, resolve, transcript, download, --selftest]
    check: '^util:whatsapp (messages|transcript|search)\b(?!.*--no-sync)'
    run: gu whatsapp messages --chat <recipient> --limit 20
---
# permission: messaging-whatsapp — send WhatsApp messages as the operator

Voice notes and photos are messages: download them and transcribe the voice notes before you
call a chat answered or silent.
Before EVERY send, read the chat's latest messages from his side: he may have sent it himself,
or an earlier run did. If it is there, record that it went out and do not send it again.
Pairing is the operator's act; a run never pairs a device.
