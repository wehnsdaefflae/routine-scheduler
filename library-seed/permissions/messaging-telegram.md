---
effect:
  with: sends Telegram messages from your own account
  without: reads your chats wherever the session and API secrets are granted, but never sends
  when: the task needs to reach a person on Telegram — each message goes out as you
tags: [communication, messaging, outbound, as-operator]
requires:
  utils: [telegram:send]
outbox:
  telegram:
    identity:
      paths: [~/telegram-sessions]
    utils:
      telegram:
        never_sends: [login, status, me, dialogs, messages, search, --selftest]
    check: '^util:telegram (messages|search)\b'
    run: gu telegram messages --chat <recipient> --limit 20
---
# permission: messaging-telegram — send Telegram messages as the operator

Never run `login`: it sends a code to the operator's phone and needs him to finish it. A lapsed
session is his to renew — ask once in a deferred question, then carry on without Telegram.
Before EVERY send, read the chat's latest messages from his side: he may have sent it himself,
or an earlier run did. If it is there, record that it went out and do not send it again.
