---
effect:
  with: sends Signal messages from your own number
  without: reads the mirrored history wherever the session store is granted, but never sends
  when: the task needs to reach a person on Signal — each message goes out as you
tags: [communication, messaging, outbound]
requires:
  utils: [signal:send]
---
# permission: messaging-signal — send Signal messages as the operator

Run `receive` before you read a chat or call it silent: the mirror holds only what the last
receive captured, from the moment this device was linked.
Linking is the operator's act; a run never links a device.
