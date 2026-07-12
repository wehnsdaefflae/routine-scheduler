---
tags: [tool-use, utils, authoring]
grants:
  actions: [write_util]
  confirm: true
---
# permission: util authoring — create and revise global utils, user-approved

Unlocks the `write_util` action: when no existing util fits, write one; when a util is
broken, repair it (read its source first: `util` name `show`, args `["<name>"]`). Every
create/revise files a blocking approval question to the user automatically — plan around
the wait and batch other work while it is pending. The engine selftests every script
before committing it; the write_util action description states the full script contract
(PEP 723 header, docstring + usage line, `--json`, `--selftest`, `secrets:` declaration).
Utils are a shared toolbox: single-purpose, reusable, never a one-off. Hold at most one
util-authoring variant.
