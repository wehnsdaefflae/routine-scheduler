"""The catalogs a ROUTE's limits are published in, each read at most once per refresh.

`limits.refresh` asks `Sources.route` for the figures of a route `routes.resolve` named, and
`Sources.output_for` for a real output maximum when the one a catalog published is a
placeholder. Four catalogs, one answer shape (`(context window, output maximum | None)`):

- **CLIProxyAPI's model catalog** (`CLIPROXY_CATALOG_URL`, the `router-for-me/models` listing the
  proxy itself serves from), grouped by PLAN (`claude`, `codex-plus`, `antigravity`, …). A
  subscription route's figures are its plan's, and the plan comes from config, never a guess.
  Rows carry `context_length`/`max_completion_tokens`, or Gemini's own
  `inputTokenLimit`/`outputTokenLimit`.
- **OpenRouter's public `/models`**, by EXACT id, sized by `top_provider` — the provider
  OpenRouter actually routes to, whose window can be smaller than the row's headline
  (`xiaomi/mimo-v2.6-pro`: 1,050,000 headline, 1,048,576 served). The same listing is the price
  source (`prices.refresh`), so it is fetched once for both.
- **Nano-GPT's detailed listing** (`/api/v1/models?detailed=true`), by exact id.
- **Anthropic's own API**: the static window table in `limits` (its listing needs a key only the
  gateway holds).

An unreachable catalog, an id it does not list, or a plan pair that disagrees is UNKNOWN — the
model then drops to the next tier of `EndpointRegistry.resolve`, visibly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from . import catalogs

log = logging.getLogger("rsched.limits")

Figures = tuple[int, int | None]

#: CLIProxyAPI's model catalog, by plan group — what each subscription login is served.
CLIPROXY_CATALOG_URL = "https://raw.githubusercontent.com/router-for-me/models/HEAD/models.json"
#: OpenRouter fills `max_completion_tokens` with ⌊0.9 × context_length⌋ for models whose real
#: output maximum it does not carry (z-ai/glm-5.3 and google/gemini-4-argon both read 943,718 on a
#: 1,048,576 window, where Nano-GPT publishes 131,072 and Google 65,536). One token either way
#: covers the rounding seen live (Together's 943,717 on a 1,048,575 window).
_PLACEHOLDER_SHARE = 0.9
_PLACEHOLDER_SLACK = 1


def is_placeholder(figures: Figures) -> bool:
    """Is the published output maximum the WINDOW restated rather than a cap a request can ask
    for? Its 0.9 placeholder, or a figure as large as the window itself: providers validate
    `input + requested_output <= window`, so asking for either leaves the prompt nothing.
    """
    ctx, out = figures
    return out is not None and (out >= ctx
                                or abs(out - _PLACEHOLDER_SHARE * ctx) <= _PLACEHOLDER_SLACK)


def cliproxy_figures(row: dict) -> Figures | None:
    out = row.get("max_completion_tokens") or row.get("outputTokenLimit")
    ctx = row.get("context_length") or row.get("inputTokenLimit")
    if not (isinstance(ctx, int | float) and ctx > 0):
        return None
    return int(ctx), int(out) if isinstance(out, int | float) and out > 0 else None


class Sources:
    """Every catalog a refresh may consult, each fetched on first use and kept for the rest of
    the refresh. `anthropic` answers from the static table `limits` hands in.
    """

    def __init__(self, anthropic: Callable[[str], int | None]) -> None:
        self._anthropic = anthropic
        self._openrouter: dict | None = None
        self._cliproxy: dict | None = None
        self._nanogpt: dict[str, Figures] | None = None

    def openrouter_body(self) -> dict:
        if self._openrouter is None:
            self._openrouter = catalogs.fetch_reference() or {}
        return self._openrouter

    def _cliproxy_groups(self) -> dict:
        if self._cliproxy is None:
            self._cliproxy = catalogs.get_json(CLIPROXY_CATALOG_URL) or {}
        return self._cliproxy

    def nanogpt(self) -> dict[str, Figures]:
        if self._nanogpt is None:
            self._nanogpt = catalogs.nanogpt_detailed()[0]
        return self._nanogpt

    def openrouter(self, model_id: str) -> Figures | None:
        for row in self.openrouter_body().get("data") or []:
            if isinstance(row, dict) and row.get("id") == model_id:
                return catalogs.openrouter_figures(row)
        return None

    def cliproxy(self, model_id: str, plans: tuple[str, ...]) -> Figures | None:
        """The id's figures in the given plan groups. Two plans that list it with DIFFERENT
        figures leave it unknown: which login serves the call is the proxy's choice.
        """
        groups = self._cliproxy_groups()
        found = {fig for plan in plans for row in groups.get(plan) or []
                 if isinstance(row, dict) and row.get("id") == model_id
                 and (fig := cliproxy_figures(row))}
        if len(found) > 1:
            log.info("limits: %s differs between plans %s — left unknown", model_id, plans)
        return found.pop() if len(found) == 1 else None

    def route(self, route) -> Figures | None:
        """The figures `route` (routes.Route) publishes in its own catalog."""
        mid = route.catalog_id()
        if route.catalog == "cliproxy":
            return self.cliproxy(mid, route.plans)
        if route.catalog == "openrouter":
            return self.openrouter(mid)
        if route.catalog == "nanogpt":
            return self.nanogpt().get(mid)
        if route.catalog == "anthropic":
            window = self._anthropic(mid)
            return (window, None) if window else None
        return None

    def output_for(self, model_id: str) -> int | None:
        """A REAL output maximum for a model whose route published a placeholder: the figure the
        other catalogs agree on under the name vendors share (`catalogs.reference_name`) —
        Nano-GPT's listing and every CLIProxyAPI plan — placeholders excluded. Sources that
        disagree, or none at all, leave it unknown.
        """
        name = catalogs.reference_name(model_id)
        rows = [fig for mid, fig in self.nanogpt().items() if catalogs.reference_name(mid) == name]
        rows += [fig for group in self._cliproxy_groups().values() if isinstance(group, list)
                 for row in group if isinstance(row, dict)
                 and catalogs.reference_name(str(row.get("id", ""))) == name
                 and (fig := cliproxy_figures(row))]
        outs = {out for fig in rows if not is_placeholder(fig) and (out := fig[1])}
        return outs.pop() if len(outs) == 1 else None

