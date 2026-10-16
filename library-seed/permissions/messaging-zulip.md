---
effect:
  with: posts to your Zulip streams and private messages as you
  without: reads your streams wherever the Zulip secrets are granted, but never posts
  when: the task needs to reach a room or a person on Zulip — each post goes out as you
tags: [communication, messaging, outbound, as-operator]
requires:
  utils: [zulip:send]
outbox:
  zulip:
    identity:
      secrets: [ZULIP_API_KEY]
    utils:
      zulip:
        never_sends: [read, --selftest]
    check: '^util:zulip read\b'
    run: gu zulip read --stream <stream> --topic <topic> (--narrow private for a direct message)
---
# permission: messaging-zulip — post to Zulip as the operator

A stream post is read by everyone in the stream, under the operator's name. Answer inside the
topic it belongs to; say what is meant for one person in a private message (`--pm`).
Before EVERY post, read the topic's latest messages from his account: he may have posted it
himself, or an earlier run did. If it is there, record that it is posted and do not post it
again.
