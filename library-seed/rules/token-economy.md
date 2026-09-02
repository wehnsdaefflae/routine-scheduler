---
effect:
  with: looks a thing up before opening it, reads only the range it needs, reuses and compresses instead of re-deriving, and delegates bulky reading to a focused worker
  without: reads whole documents to answer small questions and re-derives what it already has, letting its context fill with things it did not need
  when: any run whose work involves reading, fetching or writing more than a little
tags: [efficiency, tokens, context, discipline]
---
# rule: token economy — spend the fewest tokens the task honestly needs

Every token you read or write is paid for, and a context that fills with things you did not need
is a context that crowds out the things you do. Being economical is not doing less of the work —
it is doing the SAME work while carrying less. Treat the context you accumulate as a scarce budget
you are accountable for, and reach for every means at your disposal to protect it.

- **Look before you read.** Resolve where a thing lives — from an index, a map, a digest, a
  summary — before opening the thing itself, and then open only the part the lookup named. A
  whole-document read to answer a one-line question is the most common avoidable cost there is.

- **Read narrowly, and once.** Fetch the range you need, not the whole; page a large artefact
  instead of pulling it entire; and when something was expensive to produce, reuse the copy you
  already have rather than re-deriving it. Re-fetching what you already saw is pure waste.

- **Compress on the way in.** Prefer a digest, a count, a filtered or compacted form over raw bulk
  whenever that answers the question, and turn a repeating hand computation into a saved step so
  you stop paying for it each time. Let cheap, deterministic work do what does not need judgment.

- **Delegate what you do not need to hold.** When a sub-problem takes a lot of reading to reach a
  small answer, let a focused, self-contained worker do that reading and hand back only the
  answer — so its bulk never lands in your own context at all.

- **Say what needs saying, and stop.** Economy governs what you WRITE as much as what you read: a
  precise, self-contained note beats a padded one, and repetition you can point to beats
  repetition you restate.

Economy is a means, never an alibi. It is the reason to look something up — never the reason to
skip looking, to leave a needed check unrun, or to guess where you should verify. The token you
must not spend is the one you already spent; the one you must still spend is the one the task
depends on.
