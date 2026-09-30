---
effect:
  with: posts to your Zulip streams and private messages as you
  without: reads your streams wherever the Zulip secrets are granted, but never posts
  when: the task needs to reach a room or a person on Zulip — each post goes out as you
tags: [communication, messaging, outbound]
requires:
  utils: [zulip:send]
---
# permission: messaging-zulip — post to Zulip as the operator

A stream post is read by everyone in the stream, under the operator's name. Answer inside the
topic it belongs to; say what is meant for one person in a private message (`--pm`).
