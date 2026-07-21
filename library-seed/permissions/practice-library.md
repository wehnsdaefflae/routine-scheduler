---
tags: [practice, library, self-management]
requires:
  actions: [read_trait]
---
# permission: practice library — consult a practice module you do not already hold

Lets you `read_trait` — pull one practice module out of the shared library and apply it for the
REST OF THIS RUN. `read_trait name=list` shows the catalog (each entry flagged if it is already
one of your own standing practices); `read_trait name=<slug>` returns one module's prose. Reach
for it when the work turns out to need a discipline your recipe does not carry — a task that
became a UI job, a review, or code others will run. Nothing is written: your `traits/` directory
is your recipe and only the user changes it, so a module you consult applies now and is gone by
the next run. If one keeps proving necessary, say so in your finish summary or file a deferred
`ask_user` naming it, and the user can make it permanent. Consulting costs a turn and the prose
then sits in your context for the rest of the run — take one you will actually act on, not the
whole catalog.
