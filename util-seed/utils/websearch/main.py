# /// script
# dependencies = ["ddgs"]
# ///
"""websearch — web search via DuckDuckGo (keyless): a query in, ranked results out.

usage: gu websearch QUERY [--max N] [--region us-en] [--json]
calls: (none)
tags: web, research, search
net: outbound

Returns title / url / snippet for the top results. No API key required. With --json emits a
structured list; otherwise a readable digest. Network is needed for a live query; --selftest
runs offline against a fixture (result normalization), per the util doc standard."""

import argparse
import json
import sys

FIXTURE = [
    {"title": "Example Result", "href": "https://example.com/a", "body": "A snippet of text."},
    {"title": "Second", "href": "https://example.com/b", "body": "Another snippet."},
]


def _normalize(raw: list[dict]) -> list[dict]:
    """DDG's {title, href, body} → our {title, url, snippet}. Pure; testable offline."""
    out = []
    for r in raw:
        url = r.get("href") or r.get("url") or ""
        if not url:
            continue
        out.append({"title": (r.get("title") or "").strip(),
                    "url": url,
                    "snippet": (r.get("body") or r.get("snippet") or "").strip()})
    return out


def run(query: str, max_results: int = 8, region: str = "us-en") -> list[dict]:
    if not query.strip():
        raise ValueError("empty query")
    from ddgs import DDGS

    with DDGS() as ddgs:
        raw = list(ddgs.text(query, region=region, max_results=max_results))
    return _normalize(raw)


def selftest() -> int:
    results = _normalize(FIXTURE)
    assert len(results) == 2, results
    assert results[0] == {"title": "Example Result", "url": "https://example.com/a",
                          "snippet": "A snippet of text."}, results[0]
    assert _normalize([{"title": "no url"}]) == [], "results without a url are dropped"
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu websearch", description="Keyless web search (DuckDuckGo).")
    p.add_argument("query", nargs="?", help="the search query")
    p.add_argument("--max", type=int, default=8, help="max results (default 8)")
    p.add_argument("--region", default="us-en", help="DDG region (default us-en)")
    p.add_argument("--json", action="store_true", help="structured JSON on stdout")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.query:
        p.error("provide a QUERY")
    try:
        results = run(args.query, max_results=args.max, region=args.region)
    except Exception as exc:  # network / package errors → clean nonzero exit
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
        if not results:
            print("(no results)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
