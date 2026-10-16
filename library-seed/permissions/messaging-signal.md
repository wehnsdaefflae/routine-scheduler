---
effect:
  with: sends Signal messages from your own number
  without: reads the mirrored history wherever the session store is granted, but never sends
  when: the task needs to reach a person on Signal — each message goes out as you
tags: [communication, messaging, outbound, as-operator]
requires:
  utils: [signal:send]
outbox:
  signal:
    identity:
      paths: [~/signal-sessions]
    utils:
      signal:
        never_sends: [setup, status, link, receive, chats, messages, contacts, groups, accounts, transcript, --selftest]
    check: '^util:signal (messages|transcript)\b'
    run: gu signal receive, then gu signal messages --chat <recipient>
---
# permission: messaging-signal — send Signal messages as the operator

Run `receive` before you read a chat or call it silent: the mirror holds only what the last
receive captured, from the moment this device was linked.
Before EVERY send, run `receive` and read the chat's latest messages from his side: he may have
sent it himself, or an earlier run did. If it is there, record that it went out and do not send
it again.
Linking is the operator's act; a run never links a device.
