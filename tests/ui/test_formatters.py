"""util.js's usage line speaks the console's one compact count and one cost rule.

`fmtTokens` (the run header, the routine page's runs table, the transcript's per-turn usage, the
LLM dock) carried private copies of both: a count that never left thousands — a long run read
"2300.0k in", the same total the dashboard and Stats print as "2.30M" — and a cost rule copied
from `fmtCost`. Evaluated as pure functions in the browser ESM context.
"""


def test_the_usage_line_uses_the_shared_count_and_cost(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    out = ui_page.evaluate("""() => import('/static/util.js').then((m) => {
        const usage = { in: 2300000, out: 1500, cached_in: 12000, cost: 0.05 };
        return { line: m.fmtTokens(usage), count: m.fmtNum(usage.in), cost: m.fmtCost(usage),
                 small: m.fmtTokens({ in: 999, out: 0 }), none: m.fmtTokens(null) };
    })""")
    assert out["line"] == f"{out['count']} in (+12.0k cached) / 1.5k out · {out['cost']}"
    assert out["count"] == "2.30M" and out["cost"] == "$0.0500"
    assert out["small"] == "999 in / 0 out"          # no cost, no cache: neither is printed
    assert out["none"] == ""
