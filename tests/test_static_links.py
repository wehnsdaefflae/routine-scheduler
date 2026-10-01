"""Every link to an item's card is built by `reflinks.refHref` — the one encoded address.

Five call sites spelled `#/messages?focus=${id}` by hand, unencoded, while the `s.by` link
beside one of them encoded its value; a hand-built address is how the two forms drifted apart.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
HAND_BUILT = re.compile(r"#/messages\?focus=\$\{")


def test_card_links_go_through_ref_href():
    offenders = [f"{p.relative_to(STATIC.parent)}:{src[:m.start()].count(chr(10)) + 1}"
                 for p in sorted(STATIC.rglob("*.js"))
                 if p.name != "reflinks.js"
                 for src in [p.read_text(encoding="utf-8")]
                 for m in HAND_BUILT.finditer(src)]
    assert not offenders, ("build an item-card link with refHref() from "
                           "components/reflinks.js:\n" + "\n".join(offenders))
