"""LOSSLESS re-encodings of command output — each one a transform AND its exact inverse.

A compressor that may lose data cannot be trusted with an agent's evidence; "probably
faithful" is how Headroom's JSON crusher dropped array elements for a week without anyone
noticing. So every encoding here comes with the function that undoes it; the caller keeps
a candidate only when `decode(encode(x)) == x` BYTE FOR BYTE (JSON: value for value). A shape
the encoder does not recognise returns None and the output goes through as it was.

What each one removes is repetition a reader does not need twice, in a layout a model reads as
easily as the original:

- `grep`   — `path:line:text` search hits, every path written ONCE as a heading over its
             hits (ripgrep's --heading layout). Measured over a month of fleet traffic:
             grep-shaped output was the largest share of util output (code-search alone ~21%
             of the context-weighted volume) and headings remove ~30% of it.
- `paths`  — one-path-per-line listings (find, globs): every folder written once over the
             names inside it (~31% on the listings measured).
- `json`   — minified with the stdlib, with every array of same-keyed flat objects written as
             one `{"$table": {"cols": […], "rows": […]}}` — column names once, not once per
             row (~12% of JSON against ~7.5% for minifying alone).
"""

from __future__ import annotations

import json
import re

GREP_LINE = re.compile(r"^(?P<path>[^\s:][^:\n]*?):(?P<line>\d+)(?P<sep>[:-])(?P<rest>.*)$")
HEAD = "== "
RAW = "~ "


# ------------------------------------------------------------------------------ grep hits


def encode_grep(text: str) -> str | None:
    lines = text.split("\n")
    hits = [GREP_LINE.match(ln) for ln in lines]
    matched = sum(1 for h in hits if h)
    if matched < 5 or matched < 0.6 * len([ln for ln in lines if ln]):
        return None
    out: list[str] = []
    current = None
    for line, hit in zip(lines, hits, strict=True):
        if hit:
            if hit["path"] != current:
                out.append(HEAD + hit["path"])
                current = hit["path"]
            out.append(f"{hit['line']}{hit['sep']}{hit['rest']}")
        else:
            out.append(RAW + line)
            current = None
    return "\n".join(out)


def decode_grep(text: str) -> str:
    out: list[str] = []
    current = None
    for line in text.split("\n"):
        if line.startswith(HEAD):
            current = line[len(HEAD):]
        elif line.startswith(RAW):
            out.append(line[len(RAW):])
            current = None
        else:
            m = re.match(r"^(\d+)([:-])(.*)$", line)
            if current is None or m is None:
                raise ValueError("not a grep encoding")
            out.append(f"{current}:{m[1]}{m[2]}{m[3]}")
    return "\n".join(out)


# ------------------------------------------------------------------------------ path listings


def encode_paths(text: str) -> str | None:
    lines = text.split("\n")
    body = [ln for ln in lines if ln]
    if len(body) < 10 or any(" " in ln.strip("/") and "/" not in ln for ln in body):
        return None
    if sum(1 for ln in body if "/" in ln and not ln.startswith(HEAD)
           and not ln.startswith(RAW)) < 0.8 * len(body):
        return None
    out: list[str] = []
    current = None
    for line in lines:
        head, sep, name = line.rpartition("/")
        if sep and name and not line.startswith((HEAD, RAW)):
            if head != current:
                out.append(HEAD + head + "/")
                current = head
            out.append(name)
        else:
            out.append(RAW + line)
            current = None
    return "\n".join(out)


def decode_paths(text: str) -> str:
    out: list[str] = []
    current = None
    for line in text.split("\n"):
        if line.startswith(HEAD):
            current = line[len(HEAD):-1]
        elif line.startswith(RAW):
            out.append(line[len(RAW):])
            current = None
        elif current is None:
            raise ValueError("not a path encoding")
        else:
            out.append(f"{current}/{line}")
    return "\n".join(out)


# ------------------------------------------------------------------------------ json

TABLE = "$table"


def _tabulate(value: object) -> object:
    if isinstance(value, list):
        rows = [_tabulate(v) for v in value]
        dicts = [r for r in rows if isinstance(r, dict) and r]
        if (len(rows) >= 4 and len(dicts) == len(rows)
                and all(list(r) == list(dicts[0]) for r in dicts)
                and all(not isinstance(v, (dict, list)) for r in dicts for v in r.values())):
            cols = list(dicts[0])
            return {TABLE: {"cols": cols, "rows": [[r[c] for c in cols] for r in dicts]}}
        return rows
    if isinstance(value, dict):
        return {k: _tabulate(v) for k, v in value.items()}
    return value


def _untabulate(value: object) -> object:
    if isinstance(value, dict):
        if set(value) == {TABLE} and isinstance(value[TABLE], dict):
            spec = value[TABLE]
            return [dict(zip(spec["cols"], row, strict=True)) for row in spec["rows"]]
        return {k: _untabulate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_untabulate(v) for v in value]
    return value


def encode_json(text: str) -> tuple[str, bool] | None:
    """(encoded, tabulated) — minified, then tabulated when a uniform array is present and the
    payload does not already use the table key (which would make the encoding ambiguous).
    """
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, (dict, list)):
        return None
    minified = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if f'"{TABLE}"' in text:
        return minified, False
    tabled = json.dumps(_tabulate(value), separators=(",", ":"), ensure_ascii=False)
    return (tabled, True) if len(tabled) < len(minified) else (minified, False)


def decode_json(text: str, tabulated: bool) -> object:
    value = json.loads(text)
    return _untabulate(value) if tabulated else value


LABELS = {
    "grep": ("[search hits grouped by file: each `== path` heading is followed by that "
             "file's `line:text` hits; `~ ` marks a line kept as printed; nothing removed]"),
    "paths": ("[paths grouped by folder: each `== folder/` heading is followed by the names "
              "inside it; `~ ` marks a line kept as printed; nothing removed]"),
    "json": "[minified JSON; nothing removed; read full output for original text]",
    "json-table": ('[minified JSON; each {"$table": {cols, rows}} is an array of objects '
                   "with those keys, one row per object; nothing removed]"),
}
