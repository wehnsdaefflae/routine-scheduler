"""What an ANSWER says to the run that asked, beyond the words — in one order, in one place.

Three facts now ride an answer pair, each from its own module and each worded there:

* the patch receipt — whether the `config_patch` the question carried is LIVE
  (`patchreceipt.digest_suffix`, R2778: a run read an affirmative "Ok", recorded a budget
  raise as applied and planned 600 turns against a 400-turn ceiling);
* the decline verdict — whether the answer is a first-class **no** (`decline.digest_suffix`,
  R2197: a refusal that arrives as prose is a refusal a model reads as a go-ahead);
* the proof — whether the answer SETTLES the decision or only starts it (`proofs.digest_suffix`,
  D220: *"accept / decline my response based on the result of you checking the proof"*).

The two seams that deliver an answer to a run — the boot digest (`engine/composer`) and the
live turn boundary (`engine/control.drain_injections`) — each used to compose that list by
naming every module inline. Two copies of an order is one copy too many: the third contributor
would have had to be added twice, in the same breath, or a fact would reach a run at boot and
not mid-run (or the reverse), which is the shape of the defect R2778 and R2197 both describe.

So the ORDER lives here and the WORDING stays with each verdict. Nothing is decided in this
module: it is the composition, and that is the whole of it.
"""

from __future__ import annotations

from . import decline, patchreceipt, proofs


def suffixes(pair: dict) -> str:
    """Every verdict this answer pair carries, as indented continuation lines, or "".

    Order: what happened to the change (applied or not), then whether the answer was a
    refusal, then whether the decision is settled by it. Each contributor returns "" when it
    has nothing to say, so an ordinary answer gains nothing and no reader learns to expect a
    line that is usually absent.
    """
    return (patchreceipt.digest_suffix(pair) + decline.digest_suffix(pair)
            + proofs.digest_suffix(pair))
