"""The CAPABILITIES prompt section — what this run can ACTUALLY do, stated up front:
model + context window, the action kinds usable this run, the held permissions with their
short conduct notes, spawnable workflow patterns, and the util catalog at name+summary
altitude (exact usage stays on-demand via `util name=list`).
"""

from __future__ import annotations

from .run_context import RunContext

_PERMISSION_NOTE_MAX_LINES = 14


def _permission_notes(ctx: RunContext, g) -> str:
    """Usage notes for the held permissions that carry one — the library permission's body,
    capped. This is the ONLY prose a permission contributes to the prompt (permissions are
    an enforcement surface, not standards); the general RULES carry the principles.
    """
    from .. import library_docs

    home = ctx.server.permissions_home
    chunks = []
    for slug in g.active:
        raw = library_docs.read_doc(home, slug)
        if not raw:
            continue
        body = library_docs.doc_body(raw).strip()
        lines = list(body.splitlines())
        if not lines:
            continue
        if len(lines) > _PERMISSION_NOTE_MAX_LINES:
            lines = [*lines[:_PERMISSION_NOTE_MAX_LINES], "[…]"]
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


# The always-on util catalog is grouped by a controlled category vocabulary (D52 Phase 1):
# a flat, alphabetical 90+-line list is scanned poorly, so each util is filed under the FIRST
# category whose keyword set intersects the util's freeform `tags:`. Order matters — it resolves
# collisions: Connectors and the meta/logs/audit group sit above Health so e.g. `google-api`
# (tags include health/fitness) and `health-events`/`service-logs` (daemon "health"/logs) do NOT
# land under "Health & fitness". Nothing is hidden: every util's one-line summary stays visible
# under its group. This is a first-cut vocabulary, tunable as tags are normalized.
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
     frozenset({"shell", "system", "escape-hatch", "ssh", "remote", "machines", "gpu",
                "seedbox", "rtorrent", "rutorrent", "xmlrpc"})),
)
_UTIL_CATEGORY_OTHER = "Other"


def _util_category(tags) -> str:
    """The catalog category a util belongs to — the FIRST vocabulary entry whose keyword set
    intersects the util's tags, else "Other". Order in `_UTIL_CATEGORIES` is the tie-breaker.
    """
    tagset = {str(t).strip().lower() for t in (tags or [])}
    for label, keys in _UTIL_CATEGORIES:
        if tagset & keys:
            return label
    return _UTIL_CATEGORY_OTHER


def _util_catalog_block(utils: list[dict], kinds: list[str], g) -> str:
    """The always-on util catalog, grouped by the controlled category vocabulary (D52 Phase 1):
    a run scans ~14 labelled groups instead of a flat 90+-line alphabetical list. Every util's
    one-line summary stays visible under its group; groups are emitted in `_UTIL_CATEGORIES`
    order, "Other" last. Reserved-but-ungranted utils keep their annotation.
    """
    if not utils:
        return "Global utils: (none in the library yet)."
    buckets: dict[str, list[str]] = {}
    for u in utils:
        head = u["summary"] or u["name"]
        if not head.startswith(u["name"]):
            head = f"{u['name']} — {head}"
        note = ""
        if g is not None and u["name"] in g.gated_utils and u["name"] not in g.utils:
            # a deny-forever tombstone reads differently from merely-not-granted:
            # the first is a settled decision (never re-request), the second is
            # requestable (grants.request_route names the way).
            note = ("  [reserved — declined by the user]"
                    if f"util:{u['name']}" in g.denied
                    else "  [reserved — not granted to this routine]")
        buckets.setdefault(_util_category(u.get("tags")), []).append(f"- {head}{note}")
    order = [label for label, _ in _UTIL_CATEGORIES] + [_UTIL_CATEGORY_OTHER]
    group_blocks = [f"### {label} ({len(buckets[label])})\n" + "\n".join(sorted(buckets[label]))
                    for label in order if buckets.get(label)]
    header = (f'Global utils ({len(utils)}, grouped by domain; run '
              '`util name=list args=["<name>"]` for one\'s exact usage before calling it, '
              'or `util name=search args=["<keywords>"]` to find one by need):'
              if "util" in kinds else
              f"Global utils ({len(utils)}, grouped by domain — this workflow cannot CALL "
              "utils; the list tells you what a routine can be built to do):")
    return header + "\n" + "\n\n".join(group_blocks)


def capabilities_digest(ctx: RunContext, allowed_kinds: set[str] | None = None) -> str:
    """What this run can ACTUALLY do, stated up front: model + context window, the action
    kinds usable this run (workflow tools ∩ grants), the held permissions with their
    capability notes, and the util catalog at one line per util. Every run — including the
    clarify session, whose tools allowlist can't even call `util name=list` —
    plans against this instead of guessing. Exact usage flags still come from
    `util name=list` (live, never stale).
    """
    from .. import utils_lib
    from .kindsurface import effective_kinds

    parts: list[str] = []
    try:
        _endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
        parts.append(f"Model: {ref.endpoint}/{ref.model} — context window ≈ "
                     f"{ref.context_chars:,} chars; the engine archives the middle of "
                     "the conversation to on-disk history at ~60-80% of that, so budget your "
                     "reads (large files via read_file ranges, not whole).")
    except Exception:
        pass
    g = ctx.grants
    kinds = effective_kinds(allowed_kinds, g)
    parts.append("Action kinds usable this run: " + ", ".join(kinds) + ". Anything else is "
                 "rejected by the engine before it becomes a turn.")
    if g is not None:
        cap_bits = []
        if g.allows_kind("write_util"):
            cap_bits.append({
                "always": "write_util (every create/revise needs the user's approval)",
                "creations": "write_util (NEW utils need approval; revisions are autonomous "
                             "once the selftest passes)",
                "never": "write_util (autonomous, selftest-gated)",
            }[g.confirm])
        if g.allows_kind("remove_util"):
            cap_bits.append("remove_util (delete a global util the library no longer needs; "
                            "refused while another util still calls it)")
        if g.allows_kind("schedule_run"):
            cap_bits.append("schedule_run (arm/cancel a one-shot future run of a routine — "
                            "self-target always; other routines via the scheduling permission)")
        if "create_routine" in kinds:
            cap_bits.append("create_routine (graduate THIS conversation into a new scheduled "
                            "routine — the only way a routine is created)")
        if "manage_group" in kinds:
            cap_bits.append("manage_group (create/update/delete/order/schedule/fire routine "
                            "GROUPS from this conversation — the routines page's group "
                            "surface as an action; `cron` sets the group schedule, `split` "
                            "marks two-phase members, no operator needed)")
        cap_bits += [f"reserved util {u!r}" for u in sorted(g.utils)]
        if g.run_history != "none":
            cap_bits.append("read previous runs under runs/ "
                            + ("(the last run only)" if g.run_history == "last"
                               else "(all of them)"))
        if getattr(g, "workflows", "catalog") == "generate":
            cap_bits.append("generate a NEW workflow pattern for a subtask when none in the "
                            "catalog fits (set that subtask's workflow to 'generate')")
        parts.append("Capabilities enabled (user-set, engine-enforced): "
                     + ("; ".join(cap_bits) if cap_bits else "(none beyond the base kinds)")
                     + ". Held permissions (conduct notes below): "
                     + (", ".join(g.active) if g.active else "(none)") + ".")
        if g.granted_now:
            # a once-grant (D65) is narrower than the run: one matching action spends it
            once = ctx.granted_once
            parts.append("Granted for THIS RUN only (one-time user approvals — they do "
                         "not persist beyond this run): "
                         + ", ".join(e + " (one action only)" if e in once else e
                                     for e in sorted(g.granted_now)) + ".")
        notes = _permission_notes(ctx, g)
        if notes:
            parts.append(notes)
    # D46: surface the NAMES of the secrets provisioned in the central store — no consent, values
    # NEVER shown to a run — so a run knows up front which credentials exist (and which do not)
    # instead of probing with a util. A util still only RECEIVES a secret it declares on its
    # `secrets:` header; naming them here is informational and cannot leak a value.
    try:
        from ..secrets import secret_keys

        provisioned = secret_keys()
    except Exception:
        provisioned = []
    if provisioned:
        parts.append(
            "Secrets provisioned in the central store (NAMES only — a run never sees a secret's "
            "VALUE; a util receives one only if it DECLARES the var on its `secrets:` header): "
            + ", ".join(provisioned) + ".")
    bound = getattr(ctx.routine, "machines", None)
    if bound:
        # Bound remote machines are a resource the run can act on (via the `remote` util);
        # naming them here saves a discovery turn. Non-secret metadata only — readiness
        # (key/host-key set) is reported live by `remote list`.
        catalog = ctx.server.machines
        rows = []
        for name in bound:
            mac = catalog.get(name)
            if mac is None:
                rows.append(f"- {name} — (not in the instance catalog; ask the user to add it)")
                continue
            desc = mac.description or f"{mac.user}@{mac.host}"
            tags = f" [{', '.join(mac.tags)}]" if mac.tags else ""
            share = (f" · files mounted at mnt/{name}/ (read/write remote files there with "
                     "normal file utils)") if mac.share else ""
            rows.append(f"- {name} — {desc}{tags}{share}")
        parts.append("Remote machines this routine is bound to (run commands with the `remote` "
                     "util — `remote list` for readiness; a mounted share means the filesystem "
                     "is already local):\n" + "\n".join(rows))
    if {"spawn", "subtask", "detach"} & set(kinds):
        try:
            from ..workflows.library import list_workflows

            patterns = list(list_workflows(ctx.server.libraries_home))
        except Exception:
            patterns = []
        if patterns:
            parts.append("Sub-workflow patterns for spawn/subtask/detach — pick the one "
                         "matching the CHILD's purpose, never reflexively the default:\n"
                         + "\n".join(f"- {w['slug']} — {w['description']}" for w in patterns))
    parts.append(_util_catalog_block(utils_lib.list_utils(ctx.server.libraries_home), kinds, g))
    return "\n\n".join(parts)
