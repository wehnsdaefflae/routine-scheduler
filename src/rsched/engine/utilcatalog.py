"""The util catalog as the prompt's INDEX of the library — names, grouped by category.

NAMES ONLY (an operator trial): the per-util summaries cost every composed prompt thousands of
chars, so they are moved one call away (`util name=search`, `util name=list`). Restoring them is
restoring one composition here.
"""

from __future__ import annotations

# The always-on util catalog is grouped by a controlled category vocabulary (D52 Phase 1):
# a flat, alphabetical 90+-line list is scanned poorly, so each util is filed under the FIRST
# category whose keyword set intersects the util's freeform `tags:`. Order matters — it resolves
# collisions: Connectors and the meta/logs/audit group sit above Health so e.g. `google-api`
# (tags include health/fitness) and `health-events`/`service-logs` (daemon "health"/logs) do NOT
# land under "Health & fitness". A first-cut vocabulary, tunable as tags are normalized.
_UTIL_CATEGORIES: tuple[tuple[str, frozenset[str]], ...] = (
    ("Jobs & freelance", frozenset({"jobs", "freelance", "procurement", "clera"})),
    ("Newsletter & digest", frozenset({"newsletter", "digest", "voting", "feedback"})),
    ("Connectors & accounts",
     frozenset({"oauth", "oauth2", "connector", "notion", "calendar", "proemion", "mcp"})),
    ("Scheduler, runs, logs & audit",
     frozenset({"meta", "audit", "runs", "transcript", "triage", "stats", "self-management",
                "tokens", "measurement", "lint", "rsched", "daemon", "discovery", "scheduling",
                "logs", "monitoring", "ops", "diagnostics", "sandbox"})),
    ("Health, fitness & body",
     frozenset({"health", "fitness", "weight", "weight-loss", "body-composition", "food",
                "gps", "coaching", "sleep", "google-fit"})),
    ("Vision, media & photos",
     frozenset({"vision", "multimodal", "photos", "audio", "transcription", "immich",
                "calories", "image"})),
    ("Email & messaging",
     frozenset({"email", "inbox", "communication", "chat", "notification", "imap", "smtp",
                "usenet", "nntp"})),
    ("Documents & PDF", frozenset({"pdf", "documents", "latex", "spreadsheet"})),
    ("Files & transfer",
     frozenset({"files", "file-transfer", "editing", "listing", "ftp", "download", "backup"})),
    ("Code & development",
     frozenset({"code", "git", "dev", "ast", "refactor", "syntax", "grep", "repo", "map"})),
    ("Web, browser & scraping",
     frozenset({"web", "browser", "scraping", "captcha", "http", "links", "stealth", "tor",
                "darknet", "publish", "storage"})),
    ("AI models & text",
     frozenset({"llm", "ai", "models", "ai-models", "uncensored", "inference", "nanogpt",
                "featherless", "detection", "text"})),
    ("Data & formats", frozenset({"json", "schema", "validation", "data", "html", "static-site"})),
    ("System, remote & seedbox",
     frozenset({"system", "ssh", "remote", "machines", "gpu",
                "seedbox", "rtorrent", "rutorrent", "xmlrpc"})),
)
_UTIL_CATEGORY_OTHER = "Other"


def util_category(tags) -> str:
    """The catalog category a util belongs to — the FIRST vocabulary entry whose keyword set
    intersects the util's tags, else "Other". Order in `_UTIL_CATEGORIES` is the tie-breaker.
    """
    tagset = {str(t).strip().lower() for t in (tags or [])}
    for label, keys in _UTIL_CATEGORIES:
        if tagset & keys:
            return label
    return _UTIL_CATEGORY_OTHER


#: Utils that exist for a TEXT-ONLY run, with the note a multimodal run is shown beside them —
#: said BEFORE the call, where it costs no turn. Keyed by util name, so the note travels with
#: the util.
_FOR_TEXT_ONLY_RUNS: dict[str, str] = {
    "vision": "  [for a text-only run — THIS run's model is multimodal: use "
              "`view_image path=…`, which needs no secret and no upload]",
}


def util_catalog_block(utils: list[dict], kinds: list[str], g, *,
                        multimodal: bool = False) -> str:
    """The always-on util catalog as an INDEX, grouped by the controlled category vocabulary
    (D52 Phase 1): a run scans ~14 labelled groups instead of a flat 90+-line alphabetical list.
    Groups are emitted in `_UTIL_CATEGORIES` order, "Other" last. Reserved-but-ungranted utils
    keep their annotation.

    NAMES ONLY (see module docstring). The header names the route to what each util DOES, or a
    catalog of bare names would teach a run that the library is smaller than it is.
    """
    if not utils:
        return "Global utils: (none in the library yet)."
    buckets: dict[str, list[str]] = {}
    for u in utils:
        head = u["name"]
        note = _FOR_TEXT_ONLY_RUNS.get(u["name"], "") if multimodal else ""
        if g is not None and u["name"] in g.gated_verbs and u["name"] not in g.utils:
            verbs = sorted(v for v in g.gated_verbs[u["name"]]
                           if f"{u['name']}:{v}" not in g.utils)
            if verbs:
                note = f"  [{', '.join(verbs)} reserved — not granted to this routine]"
        if g is not None and u["name"] in g.gated_utils and u["name"] not in g.utils:
            # a deny-forever tombstone reads differently from merely-not-granted:
            # the first is a settled decision (never re-request), the second is
            # requestable (grants.request_route names the way).
            note = ("  [reserved — declined by the user]"
                    if f"util:{u['name']}" in g.denied
                    else "  [reserved — not granted to this routine]")
        buckets.setdefault(util_category(u.get("tags")), []).append(f"- {head}{note}")
    order = [label for label, _ in _UTIL_CATEGORIES] + [_UTIL_CATEGORY_OTHER]
    group_blocks = [f"### {label} ({len(buckets[label])})\n" + "\n".join(sorted(buckets[label]))
                    for label in order if buckets.get(label)]
    header = (f'Global utils ({len(utils)} — NAMES ONLY, grouped by category; run '
              '`util name=search args=["<what you need>"]` to find the one for a need and read '
              'what it does, or `util name=list args=["<name>"]` for one util\'s summary and '
              'exact usage before calling it):'
              if "util" in kinds else
              f"Global utils ({len(utils)} — NAMES ONLY, grouped by category — this workflow "
              "cannot CALL utils; the list tells you what a routine can be built to do, and "
              "what each one does is in the library):")
    return header + "\n" + "\n\n".join(group_blocks)

