"""What the settings-patterns migration writes, routine by routine (MIGRATION(expires=2026-10-20)).

Data only; `migrate_settings_patterns.py` applies it. Each routine is assigned its pattern and
takes that pattern's conduct settings — permissions, capabilities, rules, shared reminders,
retention, deliberation — plus the entries below, which are what the retained runs
show THIS routine uses beyond its pattern (so nothing it does stops working) or what the review
found dead (a trigger nobody calls, a machine never touched). Everything that names the
routine's own world — folders, accounts, machines, secrets, models, budgets, tags — is kept as the
routine holds it, with the domain's live values folded in. A `draft` is a recommendation the
person accepts or discards on the routine page; nothing in it is applied.
"""

from __future__ import annotations

#: The two shared stores routines really use: the FAU conventions canon and the Labs GPU lease.
FAU_STORE = "~/routines/.control/group-stores/grp-8bfd2aa6"
LABS_STORE = "~/routines/.control/group-stores/grp-80fd7080"
FAU_READ_ROOTS = ["/mnt/sshd_volume1/work", "/srv/Dropbox/Unterlagen/arbeit/uni_erlangen",
                  "/srv/ObsidianVault/05. Employment/Uni Erlangen"]
FAU_SECRETS = ["FAU_USER", "FAU_PASSWORD", "FTP_SOURCES", "WEB_AUTH_SOURCES"]
FAU_IMAP = {"host": "faumail.fau.de", "user_secret": "FAU_USER",
            "password_secret": "FAU_PASSWORD"}
GMAIL_IMAP = {"host": "imap.gmail.com", "accounts_secret": "GMAIL_SOURCES"}
INSTITUTIONAL = ["fau.de", "uni-erlangen.de"]

#: Every retired rule and where its conduct lives now (docs/curated-rules.md).
RULE_MAP: dict[str, list[str]] = {
    "ai-writing-tells": ["write-as-the-principal"],
    "change-restraint": ["change-scope"],
    "decision-commitment": ["work-order"],
    "email-thread-continuation": ["correspondence"],
    "engagement-accountability": ["feedback-loop"],
    "error-recovery": ["evidence-discipline", "fix-the-cause"],
    "failure-visibility": ["make-failure-visible"],
    "feedback-implementation-gate": ["feedback-loop"],
    "independent-verification": ["verify-independently"],
    "intent-inference": ["fix-the-cause"],
    "interface-copy": ["interface-craft"],
    "interface-design": ["interface-craft"],
    "outbound-followthrough": ["correspondence"],
    "ponytail": ["change-scope"],
    "reference-decay": ["change-scope"],
    "review-recall": ["audit-coverage"],
    "risk-first": ["work-order"],
    "root-cause-fix": ["fix-the-cause"],
    "status-page": ["feedback-loop"],
    "suggestion-discipline": ["ask-policy"],
    "teaching-insights": [],
    "test-design": ["make-failure-visible"],
    "token-economy": [],
    "unexamined-is-not-clean": ["audit-coverage"],
}

#: Permission docs that are gone: held by everyone, a no-op, or folded into another doc.
PERMISSION_MAP: dict[str, list[str]] = {
    "global-utils": [], "memory": [], "scripts": [], "run-history": [], "background-tasks": [],
    "workflow-generation": [], "reminders": [], "remote-machines": [], "personal-messaging": [],
    "util-revision": ["util-authoring"],
}

#: Library workflows the twelve abstract workflows replace.
SUPERSEDED_WORKFLOWS = ("application-coaching-steward", "config-audit",
                        "cumulative-feedback-research-site", "distribution-remap-research",
                        "feed-monitor-interactive-digest", "improvement-proposer",
                        "predatory-publisher-tar-pit", "repo-maintenance-sweep",
                        "steward-project-feedback-site")

#: Bare reserved-util entries a capability list may still carry, as the verbs now reserved.
UTIL_VERBS: dict[str, list[str]] = {
    "gmail": ["gmail:send"], "fau-mail": ["fau-mail:send"], "fau-mail-send": [],
    "discord": ["discord:send"], "signal": ["signal:send"], "telegram": ["telegram:send"],
    "whatsapp": ["whatsapp:send"], "zulip": ["zulip:send"], "usenet": ["usenet:post"],
    "usenet-nzb": [], "remote": [],
}
MAIL_SEND = ["gmail:send", "fau-mail:send"]


def _gate(*checks: dict, timeout: int = 45) -> dict:
    return {"enabled": True, "timeout_s": timeout,
            "checks": [{"id": c.pop("id"), **c} for c in (dict(x) for x in checks)]}


def _quiet(days: int) -> dict:
    return {"id": "heartbeat", "kind": "max_quiet", "days": days}


def _feedback(project: str) -> dict:
    return {"id": "feedback", "kind": "hub_feedback", "project": project}


def _fau_store(**more: object) -> dict:
    return {"add_grants": FAU_SECRETS, "add_read_roots": FAU_READ_ROOTS,
            "add_write_roots": [FAU_STORE], "hub_tab": "FAU", **more}


ROUTINES: dict[str, dict] = {
    "aisafety-grant-steward": {
        "pattern": "project-steward", "hub_tab": "Förderung & Aufträge",
        "drop_perms": ["outbound-mail"], "drop_utils": MAIL_SEND,
        "gate": _gate(_feedback("aisafety-grant-steward"),
                      {"id": "funder-mail", "kind": "mail", **GMAIL_IMAP, "mode": "new",
                       "folders": ["\\All"], "from_any": ["aisafetyaachen"],
                       "subject_any": ["AI Safety Aachen", "aisafetyaachen"]},
                      {"id": "funder-page", "kind": "url_changed",
                       "url": "https://aisafetyaachen.org/grants/", "select": "body"},
                      _quiet(5))},
    "ards": {
        "pattern": "project-steward", **_fau_store(),
        "add_perms": ["browser-sessions"], "add_utils": ["browser-session"],
        "drop_machines": ["omen"], "drop_triggers": ["t-f0677c26"],
        "gate": _gate({"id": "phase", "kind": "state", "file": "state/phase.json",
                       "key": "phase", "idle_values": ["wind-down"]},
                      _feedback("ards"),
                      {"id": "partner-mail", "kind": "mail", **FAU_IMAP, "mode": "new",
                       "folders": ["INBOX"], "from_any": ["bertermann", "sozialstiftung-bamberg"],
                       "subject_any": ["ARDS"]},
                      {"id": "payment-due", "kind": "dates", "from": "2026-10-18"},
                      _quiet(7)),
        "draft_drop_grants": ["DEEPGRAM_API_KEY", "PANGRAM_API_KEY"]},
    "bahnbonus-seat-position": {
        "pattern": "one-job", "add_perms": ["messaging-discord"], "add_utils": ["discord:send"],
        "add_grants": ["GMAIL_APP_PASSWORD", "GMAIL_SOURCES"],
        "models": {"main": "Sonnet", "tool_call": "Sonnet"}},
    "birthday-admin": {
        "pattern": "personal-steward",
        "add_perms": ["messaging-signal", "messaging-telegram", "messaging-whatsapp"],
        "add_utils": ["signal:send", "telegram:send", "whatsapp:send"]},
    "bkm-foerder-steward": {
        "pattern": "radar", "hub_tab": "Förderung & Aufträge",
        "drop_perms": ["outbound-mail"], "drop_utils": MAIL_SEND},
    "config-optimizer": {
        "pattern": "instance-auditor", "add_perms": ["recipe-authoring"],
        "add_actions": ["write_recipe"],
        "draft_drop_grants": ["DEEPGRAM_API_KEY", "FEATHERLESS_API_KEY", "NANO_GPT_API_KEY",
                              "OPENROUTER_UTIL_KEY", "PANGRAM_API_KEY", "RSCHED_API_TOKEN"]},
    "doppelcheck-maintainer": {
        "pattern": "code-maintainer", "drop_machines": ["predator"],
        "gate": _gate({"id": "phase", "kind": "state", "file": "state/phase.json",
                       "key": "phase", "idle_values": ["steady"]},
                      {"id": "content-questions", "kind": "state", "file": "state/findings.json",
                       "key": "content_proposals.open"},
                      {"id": "repos", "kind": "url_changed",
                       "url": "https://api.github.com/orgs/Doppelcheck/repos?per_page=100",
                       "select": "json:*.pushed_at", "token_secret": "GITHUB_TOKEN"},
                      {"id": "issues", "kind": "url_changed",
                       "url": "https://api.github.com/search/issues?q=org:Doppelcheck+is:open"
                              "&per_page=100",
                       "select": "json:items.*.updated_at", "token_secret": "GITHUB_TOKEN"},
                      _quiet(7), timeout=120),
        "draft_drop_grants": ["BROWSER_CDP_TOKEN"]},
    "eye-stabilize-folder": {
        "pattern": "queue-worker", "cron": "0 3 * * *",
        "gate": _gate({"id": "queue", "kind": "unpaired_files", "path": "/srv/.p/funscript",
                       "match": "*.mp4", "output": "{stem} [eye-stabilized]{suffix}",
                       "exclude": ["tmp*"]}, timeout=30)},
    "fau-grant-prep": {
        "pattern": "project-steward", **_fau_store(),
        # roots REMOVED from the routine, not a place anything is written
        "drop_read_roots": ["/tmp"], "drop_write_roots": ["/tmp"],  # noqa: S108
        "gate": _gate({"id": "phase", "kind": "state", "file": "state/progress.json",
                       "key": "phase", "idle_values": ["steady-frozen-awaiting-partner"]},
                      _feedback("fau-grant-prep"),
                      {"id": "partner-mail", "kind": "mail", **FAU_IMAP, "mode": "new",
                       "folders": ["INBOX", "Sent"],
                       "from_any": ["bertermann", "wienke", "jadeconsult", "btr-rosswag",
                                    "lothar-rapp", "gmp-geo"],
                       "subject_any": ["Förderbekanntmachung", "IPCEI", "Code Red", "EWSan",
                                       "Skizze"]},
                      {"id": "obligations", "kind": "dates",
                       "file": "state/obligations-register.json",
                       "key": "obligations.*.due_date", "within_days": 4, "done_key": "status",
                       "done_values": ["sent", "declined-by-user", "done"]},
                      {"id": "submission-window", "kind": "dates", "from": "2026-10-17",
                       "until": "2026-10-31"},
                      _quiet(7))},
    "folder-reorg": {
        "pattern": "queue-worker", "add_perms": ["steward-publishing"],
        "add_rules": ["feedback-loop", "interface-craft"]},
    "freelance-radar": {
        "pattern": "radar", "hub_tab": "Förderung & Aufträge", "cron": "",
        "draft_drop_grants": ["FAU_USER", "FAU_PASSWORD"],
        "draft_budgets": {"max_turns": 260}},
    "funscript-trainer": {"pattern": "model-trainer", "add_write_roots": [LABS_STORE]},
    "global-utils-review": {"pattern": "library-curator"},
    "grants-radar": {
        "pattern": "radar", "hub_tab": "Förderung & Aufträge", "budgets": {"max_turns": 260},
        "draft_drop_grants": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
                              "GOOGLE_OAUTH_SCOPES"]},
    "library-sync": {
        "pattern": "mirror-sync",
        "draft_models": {"main": "Sonnet", "tool_call": "Sonnet"}},
    "llmsectest-weekday": {
        "pattern": "code-maintainer", "add_perms": ["browser-sessions", "messaging-zulip"],
        "add_utils": ["browser-session", "zulip:send"]},
    "miz-grant-steward": {
        "pattern": "project-steward", "hub_tab": "Förderung & Aufträge", "improve": False,
        "leave_lanes": True},
    "moltbook-heartbeat": {"pattern": "daily-operator", "add_rules": ["write-as-the-principal"]},
    "nanogeofeld": {
        "pattern": "project-steward", **_fau_store(),
        "drop_machines": ["omen"], "drop_triggers": ["t-1707f33a"],
        "gate": _gate({"id": "phase", "kind": "state", "file": "state/phase.json",
                       "key": "phase", "idle_values": ["waiting", "maintenance", "wind-down"]},
                      _feedback("nanogeofeld"),
                      {"id": "external-mail", "kind": "mail", **FAU_IMAP, "mode": "new",
                       "folders": ["INBOX"], "from_domains_not": INSTITUTIONAL},
                      _quiet(7))},
    "newsletter-digest": {
        "pattern": "watcher", "hub_tab": "AI Radar",
        "add_perms": ["outbound-mail"], "add_utils": MAIL_SEND,
        "add_grants": ["DISCORD_BOT_TOKEN", "DISCORD_SOURCES"],
        "models": {"main": "Sonnet", "tool_call": "Sonnet"}, "drop_triggers": ["t-0cfa6fc6"],
        "delete_files": ["scripts/gate.py"],
        "gate": _gate({"id": "newsletters", "kind": "mail", **GMAIL_IMAP, "mode": "unseen",
                       "folders": ["INBOX"], "senders_file": "state/newsletter_senders.json"},
                      _feedback("newsletter-digest"), _quiet(7))},
    "predatory-publisher-tarpit": {
        "pattern": "mailbox-service",
        "gate": _gate({"id": "junk", "kind": "mail", **FAU_IMAP, "mode": "new",
                       "folders": ["Junk"]},
                      {"id": "external-mail", "kind": "mail", **FAU_IMAP, "mode": "new",
                       "folders": ["INBOX"], "from_domains_not": INSTITUTIONAL},
                      {"id": "bounces", "kind": "mail", **FAU_IMAP, "mode": "new",
                       "folders": ["INBOX"], "from_any": ["mailer-daemon", "postmaster"]},
                      _quiet(14))},
    "routine-improver": {
        "pattern": "library-curator", "add_perms": ["recipe-authoring"],
        "add_actions": ["write_recipe"]},
    # the curator of the shared reminder store (its `reminders` stage) — the one routine that
    # writes it, each write approved by the operator
    "rules-review": {"pattern": "library-curator", "caps_settings": {"reminders": "global"},
                     "draft_drop_grants": ["RSCHED_API_TOKEN"]},
    "scheduler-improvement-research": {
        "pattern": "instance-auditor", "draft_drop_grants": ["RSCHED_API_TOKEN"],
        "draft_drop_read_roots": ["~/.config/routine-scheduler"]},
    "self-audit": {
        "pattern": "instance-builder",
        "draft_drop_grants": ["DISCORD_BOT_TOKEN", "DISCORD_SOURCES", "FAU_USER",
                              "FAU_PASSWORD", "CLAUDE_CODE_OAUTH_TOKEN", "RSCHED_API_TOKEN"],
        "draft_drop_write_roots": ["/home/mark/conversations"]},
    "sprind": {
        "pattern": "project-steward", "improve": False,
        "drop_write_roots": ["/home/mark/signal-sessions"]},
    "steward-hub-maintainer": {"pattern": "code-maintainer"},
    "suedlink-wlf": {
        "pattern": "project-steward", **_fau_store(), "drop_triggers": ["t-f81ee4b9"],
        "gate": _gate({"id": "phase", "kind": "state", "file": "state/phase.json",
                       "key": "phase", "idle_values": ["waiting", "maintenance", "wind-down"]},
                      _feedback("suedlink-wlf"),
                      {"id": "external-mail", "kind": "mail", **FAU_IMAP, "mode": "new",
                       "folders": ["INBOX"], "from_domains_not": INSTITUTIONAL},
                      _quiet(7)),
        "draft_budgets": {"max_turns": 150}},
    "token-lab": {"pattern": "instance-auditor", "draft_drop_grants": ["OPENROUTER_API_KEY"]},
    "train-seat-finder-scheduler": {
        "pattern": "watcher", "improve": False, "add_perms": ["scheduling"],
        "add_actions": ["schedule_run"], "drop_perms": ["steward-publishing"],
        "add_grants": ["GMAIL_APP_PASSWORD", "GMAIL_SOURCES"],
        "models": {"main": "Sonnet", "tool_call": "Sonnet"},
        "gate": _gate({"id": "db-mail", "kind": "mail", **GMAIL_IMAP, "mode": "new",
                       "folders": ["INBOX"], "from_any": ["deutschebahn.com", "bahn.de"]},
                      {"id": "departures", "kind": "dates",
                       "file": "state/scheduled_connections.json",
                       "key": "upcoming.*.departure", "within_days": 3},
                      _quiet(7), timeout=30),
        "draft_drop_grants": ["RSCHED_API_TOKEN"]},
    "tv-show-tracker-seedbox-manager": {
        "pattern": "daily-operator", "add_perms": ["shell"], "add_actions": ["shell"],
        "draft_drop_grants": ["GMAIL_APP_PASSWORD", "GMAIL_SOURCES", "BROWSER_CDP_TOKEN"]},
    "uncensored-model-radar": {
        "pattern": "watcher", "drop_perms": ["steward-publishing"],
        "add_perms": ["messaging-signal"], "add_utils": ["signal:send"],
        "delete_files": ["scripts/gate.py"],
        "gate": _gate(*({"id": name, "kind": "url_changed", "url": url,
                         "select": "json:data.*.id"}
                        for name, url in (("featherless", "https://api.featherless.ai/v1/models"),
                                          ("nano-gpt", "https://nano-gpt.com/api/v1/models"),
                                          ("openrouter", "https://openrouter.ai/api/v1/models"))),
                      {"id": "abliterated", "kind": "url_changed",
                       "url": "https://abliterated.cloud/blog/posts.json", "select": "body"},
                      {"id": "untested", "kind": "state",
                       "file": "state/discovered_models.json",
                       "missing_field": "empirical_verdict"},
                      _quiet(7), timeout=60),
        "draft_drop_grants": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
                              "GOOGLE_OAUTH_SCOPES"]},
    "voice-model-trainer": {"pattern": "model-trainer", "add_write_roots": [LABS_STORE]},
    "website-maintenance-consultancy": {
        "pattern": "code-maintainer", "add_perms": ["browser-sessions"],
        "add_utils": ["browser-session"]},
    "weightloss": {
        "pattern": "daily-operator", "add_perms": ["steward-publishing"],
        "add_rules": ["interface-craft"],
        "draft_drop_grants": ["IMMICH_EMAIL", "IMMICH_PASSWORD", "OPENROUTER_API_KEY",
                              "OPENROUTER_VISION_KEY", "SFTP_SOURCES"]},
}
