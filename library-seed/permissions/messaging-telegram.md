---
effect:
  with: sends Telegram messages from your own account
  without: reads your chats wherever the session and API secrets are granted, but never sends
  when: the task needs to reach a person on Telegram — each message goes out as you
tags: [communication, messaging, outbound]
requires:
  utils: [telegram:send]
---
# permission: messaging-telegram — send Telegram messages as the operator

Never run `login`: it sends a code to the operator's phone and needs him to finish it. A lapsed
session is his to renew — ask once in a deferred question, then carry on without Telegram.
