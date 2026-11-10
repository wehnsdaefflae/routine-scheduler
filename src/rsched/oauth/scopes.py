"""A scoped provider's consent scopes as a LIST — read, normalized, judged (2026-11-09).

`<PROVIDER>_OAUTH_SCOPES` in the Secrets store is the one source of what a consent asks for
(`providers.authorize_scopes`), and editing it meant retyping a write-only 48-entry secret before
a reconnect on another card. The Connections editor reads and writes it through here instead.

Judgement is in two strengths. An ERROR refuses the save: a scope a provider may not carry
(`scope_prefixes` — the Health connection takes `googlehealth.*` alone, because the Health API
refuses a token carrying anything else), or more than `max_scopes` (Google's consent hangs past
50 — observed, undocumented). A WARNING saves and says so: a `googlehealth.*` scope on the
general Google connection is one the Health API will refuse to honour on that token.

No catalogue of scopes lives here — Google publishes the list (`DOCS_URL`), and a copy would be
a frozen guess. A scope is grouped by the API its own URL names.
"""

from __future__ import annotations

from urllib.parse import urlencode

from . import providers

DOCS_URL = "https://developers.google.com/identity/protocols/oauth2/scopes"
CONSOLE = "https://console.cloud.google.com"
#: Scope groups that belong to sign-in itself, not to an API a project enables.
_NO_API = frozenset({"openid", "userinfo"})
_BARE = frozenset({"openid", "email", "profile"})
_HEALTH = "googlehealth."


def split(raw: str) -> list[str]:
    """The scopes in a stored value — space / newline / comma separated, order kept, deduped."""
    return list(dict.fromkeys(raw.replace(",", " ").split()))


def current(provider_id: str) -> list[str]:
    """What a consent for `provider_id` asks for today, as a list."""
    return split(providers.authorize_scopes(provider_id))


def normalize(provider_id: str, scope: str) -> str:
    """A short name (`drive.readonly`) as the provider's full scope; a full URL or a bare OpenID
    scope as given.
    """
    scope = scope.strip()
    prov = providers.get_provider(provider_id)
    if (prov is None or not prov.scope_base or "://" in scope or scope in _BARE
            or not scope):
        return scope
    return prov.scope_base + scope


def api_of(scope: str) -> str:
    """The API a scope belongs to, read off its own URL (`…/auth/gmail.modify` → `gmail`)."""
    tail = scope.rstrip("/").rsplit("/", 1)[-1]
    return "openid" if scope in _BARE else tail.split(".", 1)[0]


def judge(provider_id: str, scopes: list[str]) -> tuple[list[str], list[str]]:
    """(errors, warnings) for saving `scopes` as `provider_id`'s consent."""
    prov = providers.get_provider(provider_id)
    if prov is None or not prov.scoped:
        return [f"{provider_id} takes no scopes from this instance"], []
    errors, warnings = [], []
    if not scopes:
        errors.append("a consent needs at least one scope")
    if prov.scope_prefixes:
        outside = [s for s in scopes if not s.startswith(prov.scope_prefixes)]
        if outside:
            errors.append(f"{prov.name} may only carry {', '.join(prov.scope_prefixes)}… — the "
                          f"API refuses a token holding anything else: {', '.join(outside)}")
    if prov.max_scopes and len(scopes) > prov.max_scopes:
        errors.append(f"{len(scopes)} scopes — {prov.name}'s consent hangs past "
                      f"{prov.max_scopes}; drop {len(scopes) - prov.max_scopes}")
    health = [s for s in scopes if _HEALTH in s]
    if health and not prov.scope_prefixes and "google_health" in providers.PROVIDERS \
            and provider_id == "google":
        warnings.append(f"{len(health)} googlehealth.* scope(s) here are refused by the Health "
                        "API on this token — they belong on the Google Health connection")
    return errors, warnings


def google_project(provider_id: str) -> str:
    """The Google Cloud project NUMBER a provider's OAuth client belongs to, read off the client
    id itself (`746057774400-….apps.googleusercontent.com`) — or "" when it is not one.
    """
    creds = providers.client_creds(provider_id)
    head = creds.client_id.split("-", 1)[0] if creds else ""
    return head if head.isdigit() else ""


def console_links(provider_id: str, groups: set[str]) -> dict:
    """Where in the Cloud console this provider's project is managed (2026-11-10: a scope is
    useless until its API is ENABLED in the project — Docs answered SERVICE_DISABLED with the
    scope granted). Empty unless the provider is a Google one with a readable project.

    An API row links to the project's API library SEARCHED for the group's name rather than to
    one API's page: a scope's URL does not name the API's service (`documents` scopes belong to
    docs.googleapis.com, `calendar` to calendar-json.googleapis.com), and a mapping kept here would
    be a copy of Google's catalogue that goes stale.
    """
    prov = providers.get_provider(provider_id)
    project = google_project(provider_id) if prov and "googleapis" in prov.scope_base else ""
    if not project:
        return {}
    api = {g: f"{CONSOLE}/apis/library/browse?"
              + urlencode({"project": project, "q": g.removeprefix("google") or g})
           for g in sorted(groups - _NO_API)}
    return {"project": project,
            "enabled_apis": f"{CONSOLE}/apis/dashboard?project={project}",
            "consent": f"{CONSOLE}/auth/overview?project={project}",
            "api": api}
