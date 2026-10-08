"""The CAPABILITIES prompt section — what this run can ACTUALLY do, stated up front:
model + context window, the action kinds usable this run, the held permissions with their
short conduct notes, spawnable workflow patterns, and the util catalog at name+summary
altitude (exact usage stays on-demand via `util name=list`).
"""

from __future__ import annotations

from .run_context import RunContext

#: A held permission's note is cut at a CHARACTER budget, on a line boundary. It used to be cut
#: at 14 lines, which dropped the very conduct 18 of 25 docs existed for (a line count says
#: nothing about length; the conduct sat below the mechanism). The docs are now written
#: conduct-first to fit, so the budget is a backstop, not a truncator.
PERMISSION_NOTE_MAX_CHARS = 1_000

#: The script gloss's last sentence, in both its forms (engine/thenscript.py): the turn between
#: a change and the script that checks it is the one most worth not spending.
_THEN_SCRIPT = ("When the step right after a write_file or edit_file is running a script, put "
                'it ON the change: then_script ["<name>", ...args] runs it the moment the change '
                "lands and returns both results in ONE observation — skipped if the change fails")


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
        if not body:
            continue
        if len(body) > PERMISSION_NOTE_MAX_CHARS:
            body = body[:body.rfind("\n", 0, PERMISSION_NOTE_MAX_CHARS) + 1 or
                        PERMISSION_NOTE_MAX_CHARS].rstrip() + "\n[…]"
        chunks.append(body)
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
     frozenset({"system", "ssh", "remote", "machines", "gpu",
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
        buckets.setdefault(_util_category(u.get("tags")), []).append(f"- {head}{note}")
    order = [label for label, _ in _UTIL_CATEGORIES] + [_UTIL_CATEGORY_OTHER]
    group_blocks = [f"### {label} ({len(buckets[label])})\n" + "\n".join(sorted(buckets[label]))
                    for label in order if buckets.get(label)]
    header = (f'Global utils ({len(utils)}, grouped by category; run '
              '`util name=list args=["<name>"]` for one\'s exact usage before calling it, '
              'or `util name=search args=["<keywords>"]` to find one by need):'
              if "util" in kinds else
              f"Global utils ({len(utils)}, grouped by category — this workflow cannot CALL "
              "utils; the list tells you what a routine can be built to do):")
    return header + "\n" + "\n\n".join(group_blocks)


def _secret_notes(ctx: RunContext) -> list[str]:
    """The two secret scopes, as CAPABILITIES lines.

    D46: the NAMES provisioned in the central store — no consent, values NEVER shown to a run
    — so a run knows up front which credentials exist (and which do not) instead of probing
    with a util. D103: the routine's OWN names listed apart, because they need no exposure
    request (they are already its) and they shadow a central name of the same spelling — a
    run that cannot tell them apart spends a turn asking for what it already holds.

    A util still only RECEIVES a secret it declares on its `secrets:` header, so naming
    either set here is informational and cannot leak a value.
    """
    try:
        from ..secrets import routine_secret_keys, secret_keys

        provisioned = secret_keys()
    except Exception:
        provisioned = []
    try:
        own = routine_secret_keys(ctx.routine.slug)
    except Exception:
        own = []
    out = []
    if provisioned:
        out.append(
            "Secrets provisioned in the central store (NAMES only — a run never sees a secret's "
            "VALUE; a util receives one only if it DECLARES the var on its `secrets:` header): "
            + ", ".join(provisioned) + ".")
    if own:
        out.append(
            "Secrets that are THIS ROUTINE's own (NAMES only, same declared-only rule): "
            + ", ".join(own) + ". These are already exposed to you — never file an access "
            "request for them; where a name also exists centrally, yours is what a util gets.")
    return out


def _machine_notes(ctx: RunContext) -> str:
    """The bound remote machines, one row each — a resource the run acts on via the `remote`
    util, named here so it costs no discovery turn. Non-secret metadata only; readiness
    (key/host-key set) is reported live by `remote list`.

    The SHARE half reports what this run actually HAS, never what the catalog asked for
    (R514). The line used to be emitted from `mac.share` alone, so a run whose sshfs mount
    had failed was still told its files were at `mnt/<name>/` — and the empty directory left
    behind read as an empty source. Now a share is advertised only once proven live, and a
    share that did not come up says so with its reason.
    """
    bound = ctx.routine.machines
    if not bound:
        return ""
    catalog = ctx.server.machines
    rows = []
    for name in bound:
        mac = catalog.get(name)
        if mac is None:
            rows.append(f"- {name} — (not in the instance catalog; ask the user to add it)")
            continue
        desc = mac.description or f"{mac.user}@{mac.host}"
        tags = f" [{', '.join(mac.tags)}]" if mac.tags else ""
        if name in ctx.mounted_shares:
            share = (f" · files mounted at mnt/{name}/ (read/write remote files there with "
                     "normal file utils)")
        elif name in ctx.unavailable_shares:
            share = (f" · SHARE NOT MOUNTED this run ({ctx.unavailable_shares[name]}) — there "
                     f"is no mnt/{name}/ directory; move files with the remote machine's own "
                     "transfer capability instead, and do not treat any local path as its "
                     "filesystem")
        else:
            share = ""
        # An exclusive machine's compute is QUEUED, and what the run needs to know is its place
        # in the rotation — not that the resource exists. Read from the daemon's mirror, so this
        # costs no SSH round-trip per run; an unreachable box reads as unknown, never as free.
        queue = ""
        # getattr: a machine stub without the field is simply not exclusive — the same tolerance
        # web/model_fit.discovered applies to a server object without a routines_home
        if getattr(mac, "exclusive", False):
            from ..machine_queue import capability_note
            queue = capability_note(ctx.server.routines_home, name, ctx.routine.slug)
        rows.append(f"- {name} — {desc}{tags}{share}{queue}")
    return ("Remote machines this routine is bound to (run commands with the `remote` util — "
            "`remote list` for readiness; a mounted share means the filesystem is already "
            "local):\n" + "\n".join(rows))


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
                     f"{ref.context_tokens:,} tokens; the engine archives the middle of "
                     "the conversation to on-disk history at ~60-80% of that, so budget your "
                     "reads (large files via read_file ranges, not whole).")
    except Exception as exc:
        # A silent `pass` here deleted the whole "Model: … context window ≈ N tokens" line from
        # the prompt and said nothing: the run then planned its reads against a window it had
        # never been told, and no transcript named the resolution failure. Say what is unknown
        # and why, so the run budgets conservatively instead of guessing (and so the fault is
        # legible to whoever reads the prompt afterwards).
        parts.append(f"Model: this run's main model could not be resolved ({exc}) — its context "
                     "window is UNKNOWN here, so read in ranges and keep the conversation lean "
                     "rather than assuming headroom.")
    g = ctx.grants
    kinds = effective_kinds(allowed_kinds, g)
    parts.append("Action kinds usable this run: " + ", ".join(kinds) + ". Anything else is "
                 "rejected by the engine before it becomes a turn.")
    if "decide" in kinds:
        from .decideaction import catalog_line
        if line := catalog_line(ctx.server, ctx.routine.models):
            parts.append(line + " — a decide call's `model` picks one; without it the default "
                         "for its payload answers.")
    if g is not None:
        cap_bits = []
        # Each line keys on `kinds` — the grant ∩ the workflow's `tools:` — never on the grant
        # alone: a recipe that leaves a granted kind out of its tools would otherwise be told,
        # here, about a kind its schema refuses.
        if "write_util" in kinds:
            placement = (" A util is GLOBAL, for every routine — a helper only THIS "
                         "routine will ever call belongs in its own scripts/, not the "
                         "shared library.")
            cap_bits.append({
                "always": "write_util (every create/revise needs the user's approval).",
                "creations": "write_util (NEW utils need approval; revisions are autonomous "
                             "once the selftest passes).",
                "never": "write_util (autonomous, selftest-gated).",
            }[g.confirm] + placement)
        if "remove_util" in kinds:
            cap_bits.append("remove_util (delete a global util the library no longer needs; "
                            "refused while another util still calls it)")
        if "write_rule" in kinds:
            # Named like the other emittable gated kinds, with its OWN approval dial: left out,
            # a routine holding only rule-authoring read "(none beyond the base kinds)".
            cap_bits.append("write_rule (author or revise a general rule in the shared library "
                            "— a revision reaches every routine holding the rule; " + {
                                "always": "every write needs the user's approval",
                                "creations": "NEW rules need approval, revisions do not",
                                "never": "no approval asked",
                            }[g.rule_confirm] + ")")
        if "shell" in kinds:
            cap_bits.append("shell (run an ad-hoc command on the host — the ESCAPE HATCH "
                            "around the util library; hold it, use it for the one-off, and "
                            "turn anything you run twice into a util or a scripts/ helper)")
        if "schedule_run" in kinds:
            cap_bits.append("schedule_run (arm/cancel a one-shot future run of this routine "
                            "or another)")
        if "script" in kinds:
            from .. import scripts
            have = scripts.list_scripts(ctx.routine.dir)
            if have:
                cap_bits.append(
                    "script — run this routine's OWN persistent helper scripts (scripts/, "
                    "your venv, deterministic work only — no model access inside): "
                    + "; ".join(f"{s['name']} ({s['summary']})" if s["summary"] else s["name"]
                                for s in have)
                    + ". When you notice a repeating deterministic sub-step (fetch, parse, "
                      "compute, render), STOP redoing it by hand: write_file a new "
                      "scripts/<name>.py once and call it every time after — it persists "
                      "across runs, costs no model work, and stays under your control. "
                      "A script may use utils it DECLARES on its docstring 'calls:' line "
                      "(their secrets and network fold into its sandbox); an undeclared "
                      "one is refused. "
                      "Placement test: a script is for THIS routine only — capability "
                      "another routine could plausibly reuse belongs in the shared "
                      "library as a util instead. " + _THEN_SCRIPT)
            else:
                cap_bits.append(
                    "script — run this routine's own Python helpers from scripts/<name>.py "
                    "(none exist yet; author one with write_file: a PEP 723 script, "
                    "docstring header '<name> — <summary>' + 'net:' + 'secrets:' + "
                    "'calls:' for any utils it shells out to, then call it with the "
                    "script action). When you notice a repeating "
                    "deterministic sub-step (fetch, parse, compute, render), write a "
                    "PERSISTENT script for it instead of redoing it by hand — it runs in "
                    "your venv inside your sandbox, survives this run, and future runs "
                    "call it for free. Placement test: a script is for THIS routine only "
                    "— capability another routine could plausibly reuse belongs in the "
                    "shared library as a util instead. " + _THEN_SCRIPT)
        if "create_routine" in kinds:
            cap_bits.append("create_routine (graduate THIS conversation into a new scheduled "
                            "routine — the only way a routine is created)")
        if "manage_lane" in kinds:
            cap_bits.append("manage_lane (create/update/delete/order/schedule/fire routine "
                            "LANES from this conversation — the routines page's lane rows as "
                            "an action; `cron` sets the lane schedule, no operator needed. It "
                            "reaches the TEMPORAL axis only: when a set of routines fires and "
                            "in what order. What a routine may reach — its settings, its roots, "
                            "the stores it shares — is its own config, which no run writes: "
                            "propose such a change with ask_user + config_patch instead)")
        cap_bits += [f"reserved util {u!r}" for u in sorted(g.utils)]
        if g.run_history != "none":
            cap_bits.append("read previous runs under runs/ "
                            + ("(the last run only)" if g.run_history == "last"
                               else "(all of them)"))
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
    parts.extend(_secret_notes(ctx))
    if machines := _machine_notes(ctx):
        parts.append(machines)
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
