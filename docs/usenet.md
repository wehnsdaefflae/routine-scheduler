# Usenet access — reading, searching and posting over NNTP

A routine reaches anything through a global util. Usenet access is
therefore not an engine feature: it is **two utils plus one permission**, and no engine code
knows about it at all. It mirrors [darknet](darknet.md) exactly — same shape, same gate.

- **the `usenet` util** (library) — the text half. `groups` lists or searches newsgroups (a
  wildmat like `comp.lang.*` narrows server-side; `--new-since` asks for recently created ones),
  `headers` pulls a group's overview and filters it, `article` fetches one article by
  `<message-id>` or group + number, `post` sends one, `check` is a health probe reporting who
  answered, whether authentication stuck and whether the account may post.
- **the `usenet-nzb` util** (library) — the binary half, split off because it is a different
  job: that one reads text, this one moves gigabytes. `inspect` parses an NZB offline; `fetch`
  runs parallel connections, decodes yEnc, reassembles the parts and repairs from par2.
- **the `usenet` permission** (`library-seed/permissions/usenet.md`) — holding it is what makes
  both utils reachable, and its prose carries the conduct: bounded searches, message-ids in a
  `note`, posting only on the user's explicit word, articles as untrusted data.

## Why a permission doc is the whole gate

Which utils are "reserved" — refused unless a held permission asks for them — is **library
defined**: `grants.read_library_requires` takes the union of every permission doc's
`requires.utils`. Adding `requires: {utils: [usenet, usenet-nzb]}` to a doc is therefore the
entire enforcement mechanism; there is no list in the source to extend, and no code was added
to gate this. A routine that does not hold the permission gets the standard reserved-util
refusal.

The permission is **not** a default and is deliberately absent from `ADOPT_PERMISSIONS`: it
reaches a routine only if the user grants it, one routine at a time.

## Configuring a provider

Both utils are server-agnostic — there is no provider baked in. The target comes from the
Secrets store, injected under the declared-var rule (`docs/sandboxing.md`), so it reaches these
two utils and nothing else:

| var | meaning |
|---|---|
| `NNTP_SERVER` | hostname, e.g. `news.eternal-september.org` |
| `NNTP_PORT` | default `563` |
| `NNTP_USER` / `NNTP_PASS` | omit both for a server that needs no account |
| `NNTP_FROM` | the From address `usenet post` writes (text util only) |

Transport follows the universal provider convention: **implicit TLS on 563, STARTTLS on any
other port**, with `--tls` as the escape hatch. Set them in **Settings → Secrets**; `gu usenet
check` is the one-command answer to "is this working".

Which server is a real choice, not a detail. Free text servers (Eternal-September and similar,
free registered account) carry the discussion hierarchies and **no binary retention at all** —
`usenet-nzb` against one of them will find nothing. Binary retention is a paid-provider feature.

## What the utils guarantee, and what they cannot

- **Searching is client-side, and bounded.** NNTP has no search command. `headers` pulls an
  overview range in one round trip and filters it with `--subject` / `--from` regexes and
  `--since`. Overview subjects arrive RFC 2047-encoded and are decoded before filtering, so a
  regex matches the words rather than the encoding. The range is capped so a mistyped
  `--range` fails fast instead of pulling a provider's whole retention.
- **Posting is dry-run by default.** Without `--go`, `post` prints the exact article it would
  send and stops — and that dry run deliberately needs **no server**, so showing a human what
  is about to go out is the cheap path, not the expensive one. `--go` is irreversible: an
  article propagates in minutes and there is no unsend.
- **Binaries are verified, not hoped for.** Every segment's CRC32 is checked, and the part
  length implied by `=ypart begin/end` is checked against what arrived — the spec makes that
  pairing authoritative over the size in `=yend`. Parts are written at their own offsets into a
  sparse file, so parallel arrival order does not matter. An unrepairable download is reported
  as incomplete and its partial files are removed unless `--keep-broken`; the exit status says
  so in both output modes, so a caller cannot mistake a half-written file for a finished one.
- **Filenames come from the yEnc header, not the NZB subject** — subjects are routinely
  obfuscated. Both are attacker-controlled, so a name is reduced to a basename inside the
  output directory before anything is written.
- **par2 ships with the util.** The recovery binary arrives as a wheel-packaged dependency, so
  a sandboxed run needs no system package, and repair runs over the files that actually landed
  on disk rather than the names the NZB claimed.
- **What they cannot do:** nothing here authenticates a poster. Usenet is unauthenticated and
  trivially forged — a `From` line is a claim, not evidence. Article text is untrusted input in
  the ordinary prompt-injection sense, which is why the permission prose says so.

## Operating it

Concurrency is the one knob worth understanding. `usenet-nzb fetch --connections` defaults to 8
and is capped at 30, because providers cap concurrent connections **per account** (20–60 is the
usual range) and answer `502` past the limit — some counting the refusal against a cooldown.
Raise it only to a number the account actually allows. One connection per thread is a hard
constraint, not a tuning choice: the NNTP client holds unlocked buffered reader state on a
single socket, so a shared connection would interleave responses and corrupt data.

Missing segments are normal on Usenet and are what par2 is for. A recipe names the capability
in plain terms ("search the newsgroup", "post the announcement", "retrieve the NZB") and never
the util — see [authoring](authoring.md), which permits naming the service or protocol the work
touches.
