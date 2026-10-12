"""Where a report's `detail` is cut, and where it is kept whole (D177 / F660).

The split under test: the `reports.jsonl` ROW stores the detail whole, and only the MESSAGE
delivered into the target's inbox is clipped — because that text rides in another run's prompt,
and the ledger is cheap disk. The operator's words, 2026-10-11: *"the ledger is cheap disk, the
prompt is the scarce resource, and R2436's lost question would have been recoverable"*.

These claims live in their own module (with `rsched.report_detail`) rather than in
`test_reports`, which is over the house size bar and may only shrink.
"""

from __future__ import annotations

import json

from helpers import bare_routine
from rsched.engine.admin_handlers import handle_report
from rsched.engine.observations import format_observation
from rsched.report_detail import DETAIL_MAX, clip_detail
from test_reports import _loop, _rows


def test_the_dropped_count_does_not_depend_on_knowing_the_row_id():
    """The filer's observation measures its clip BEFORE the id is minted (the id is assigned
    under the ledger lock), so the marker's width is reserved against a placeholder. If a
    shorter real id bought back characters, the figure in the observation and the figure in
    the delivered message would disagree — which is the exact defect F660's fix removed."""
    detail = "y" * (DETAIL_MAX * 2)
    assert clip_detail(detail)[1] == clip_detail(detail, "R1")[1]
    assert clip_detail(detail, "R1")[0].endswith("]")
    assert "R1" in clip_detail(detail, "R1")[0]


def test_the_name_every_caller_knows_still_resolves():
    """`reports.clip_detail` / `reports.DETAIL_MAX` are what the engine and the docs name, and
    D177 moved the code to `report_detail`. The re-export is load-bearing: `admin_handlers`
    calls `reports.clip_detail` to measure the filer's warning."""
    from rsched import report_detail, reports

    assert reports.clip_detail is report_detail.clip_detail
    assert reports.DETAIL_MAX == report_detail.DETAIL_MAX == DETAIL_MAX
    assert reports.TRUNCATION_NOTE is report_detail.TRUNCATION_NOTE


def test_an_over_long_detail_says_it_was_cut_everywhere_it_lands(tmp_path):
    """F660: a `detail` past DETAIL_MAX was sliced bare — no marker in the text, no word to
    the filer — so a truncated report was indistinguishable from a complete one.

    Measured on the live ledger 2026-10-10: 273 rows end at exactly 4000 characters,
    mid-word. The cost is not cosmetic. R2436 (scheduler-builder -> self-audit) ended
    `"Changing what "` and its own text said the cut-off part was the one question back to
    the reader and the reason the thread could not close — so the exchange became unclosable
    from the ledger.

    D177 then moved WHERE the cap bites: the row keeps the detail whole, the DELIVERY is cut,
    and the marker names the row that holds the rest.
    """
    loop, home = _loop(tmp_path, slug="self-audit")
    target = bare_routine(home, "routine-improver")
    tail = "THE QUESTION BACK TO YOU"
    detail = ("x" * DETAIL_MAX) + " " + tail
    kept, dropped = clip_detail(detail, "R1")
    assert dropped >= len(tail)                      # the tail is cut from the DELIVERY

    obs = handle_report(loop, {"target": "routine-improver", "title": "long", "detail": detail})

    row = _rows(home)[0]
    # D177: the LEDGER ROW keeps it whole — the ledger is cheap disk, the prompt is not, and
    # R2436's lost question is exactly what has to stay recoverable
    assert row["detail"] == detail
    assert tail in row["detail"]
    delivered = json.loads((target / "inbox" / "msg-rep-R1.json").read_text(encoding="utf-8"))
    assert delivered["text"].count(kept) == 1        # the DELIVERY is the clipped text
    assert tail not in delivered["text"]             # ... and the tail does not ride the prompt
    assert "TRUNCATED" in delivered["text"].upper()  # never silently
    assert "R1" in delivered["text"]                 # and it names the row holding the rest
    assert str(dropped) in delivered["text"]
    # the delivery carries LESS of the detail than the row does — compared on the detail
    # itself, since delivered["text"] also wraps it in the message head and the title
    assert len(kept) < len(row["detail"])

    # the filer learns it IN the observation, with the SAME figure the delivery states, while
    # it can still act — a count that disagreed with the message would be its own defect
    rendered = format_observation(obs)
    assert "truncated" in rendered.lower() and str(dropped) in rendered

    # a detail that fits is untouched and says nothing about truncation
    obs2 = handle_report(loop, {"target": "routine-improver", "title": "short", "detail": "d"})
    assert _rows(home)[1]["detail"] == "d"
    assert "truncated" not in format_observation(obs2).lower()
