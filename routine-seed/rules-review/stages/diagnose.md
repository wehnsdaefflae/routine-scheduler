# diagnose — is the TEXT the cause?

You have rows of evidence. Decide what, if anything, they imply for the rule's wording. The
question at every step is the same: would a different sentence have produced a different run?

**Find the cause, not the symptom.** A run that got something wrong while holding a rule is
not itself proof the rule is at fault. Work backwards: which sentence was it acting on, and
what would a careful reader have concluded from that sentence alone, without your context?
If a careful reader would have got it right, the rule is not the cause — the recipe, the
config, or a tool is, and that goes to its owner.

Weigh the classes against each other:

- **misread + misread across two routines** — the text is genuinely ambiguous. Strongest case
  for a revision, and the revision is usually a disambiguation, not an addition.
- **misread once, followed several times** — likelier a routine-specific problem than a rule
  one. Consider whether that routine should hold this rule at all, and route it.
- **ignored, repeatedly, by different holders** — the rule may be unreadable at the moment it
  is needed, buried, or too abstract to act on. The fix is usually shorter and more concrete,
  not more emphatic. A rule that has to shout is a rule nobody could apply.
- **invented, and it worked** — promote the interpretation into the text, in the general form.
  Take care: what one routine invented may be right only for its domain. Generalize it or drop
  it; a rule that names a routine's particular case has stopped being a rule.
- **followed** — protection. Any revision must keep every followed reading valid, and you check
  this explicitly before you write.

Two standing cautions. A rule is held by routines you did not examine — write for them too,
which means resisting wording that fits only the evidence in front of you. And the rules layer
is a whole: check the change against the other rules before you write it, because a
contradiction between two rules is worse than either one being vague.

Record the verdict for this rule — `revise` with the specific defect named, or `leave-alone`
with the reason — then advance to **revise** (or, for `leave-alone`, straight back to
**gather-evidence** for the next rule in the queue, or to **record** when the queue is empty).
