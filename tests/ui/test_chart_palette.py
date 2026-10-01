"""The charts' categorical palette is base.css's --series tokens, so it follows the theme.

components/charts.js hard-coded six hex colours tuned for one old dark plate and painted them
in BOTH themes — on white, one fell under 3:1 and the pink/violet pair sat under the
normal-vision separation floor in either. The palette is now --series-1..6 / --series-other,
each defined per theme with light-dark(); a chart mark and a legend swatch carry a var()
reference, so a theme switch repaints them with no re-render.
"""

from __future__ import annotations

CHART = """(theme) => import('/static/components/charts.js').then((m) => {
  document.documentElement.dataset.theme = theme;
  const day = new Date().toISOString().slice(0, 10);
  const runs = ['alpha', 'beta'].map((routine, i) => ({
    day, routine, tokens_in: 100 * (i + 1), tokens_out: 0, cost: 0, elapsed_s: 1 }));
  const node = m.chartNode({ metric: 'tokens', group: 'routine', range: 7, type: 'bar' },
                           runs, m.colorMap(runs, 'routine'));
  document.body.append(node);
  const probe = document.createElement('span');
  document.body.append(probe);
  const token = (name) => { probe.style.color = `var(${name})`; return getComputedStyle(probe).color; };
  const out = {
    fills: [...node.querySelectorAll('svg rect[stroke]')].map((r) => getComputedStyle(r).fill),
    swatches: [...node.querySelectorAll('.chart-swatch')].map((s) => getComputedStyle(s).backgroundColor),
    tokens: [token('--series-1'), token('--series-2')],
    slug: m.slugColor('anything'),
  };
  node.remove(); probe.remove();
  return out;
})"""


def test_chart_marks_read_the_theme_series_tokens(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    dark = ui_page.evaluate(CHART, "dark")
    light = ui_page.evaluate(CHART, "light")
    for got in (dark, light):
        # beta (more tokens) ranks first: slot 1; alpha slot 2 — fills AND legend swatches
        assert sorted(set(got["fills"])) == sorted(got["tokens"]), got
        assert got["swatches"] == [got["tokens"][0], got["tokens"][1]], got
        assert got["slug"].startswith("var(--series-"), got
    assert dark["tokens"][0] != light["tokens"][0], "the series colour ignores the theme"
