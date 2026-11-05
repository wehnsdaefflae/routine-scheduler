"""Is the redirect target one the provider will refuse before it ever asks for consent?

An OAuth provider sees only the `redirect_uri` we send it. Google refuses an authorization
request whose redirect target resolves to a private address, with its own error page —
"Access blocked: Authorization error / device_id and device_name are required for private IP" —
which names our OAuth app and the LAN address and nothing the operator can act on. That page
replaces the consent screen, so a re-authorization simply cannot happen and the console that
sent him there said nothing at all.

We know the target before we send it (`web/settings/oauth.py` `_redirect_uri`), so the flow
states the cause itself. The verdict is a CAVEAT, not a refusal: whether a provider accepts a
target is the provider's call, resolution here need not match resolution at the provider, and an
operator on a tunnel may know better than this inference — so the caller still gets its
authorize URL and the user decides. An unresolvable name says nothing: a guess is not a cause.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def resolve_host(host: str) -> list[str]:
    """Every address `host` resolves to on this machine. Module-level (and reached through the
    module, never a from-import) so a test can replace it without touching DNS.
    """
    # getaddrinfo's sockaddr is a union (AF_INET vs AF_INET6), so element 0 is typed str | int;
    # for every address family we care about it is the address string.
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})


def _unroutable(addr: str) -> bool:
    """RFC1918 10/8, 172.16/12, 192.168/16, loopback 127/8, link-local 169.254/16, and their
    IPv6 counterparts ::1 and fc00::/7 — every range `is_private` covers, plus the two it
    classifies separately. Anything unparseable is not a finding.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def private_host_caveat(redirect_uri: str) -> str:
    """One sentence naming the host, the cause and the remedy — or "" when there is nothing to
    say (a routable target, an unparseable one, a name this machine cannot resolve).
    """
    host = urlparse(redirect_uri).hostname or ""
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            addrs = resolve_host(host)
        except OSError:
            return ""
    bad = [a for a in addrs if _unroutable(a)]
    if not bad:
        return ""
    where = host if bad == [host] else f"{host} ({', '.join(bad)})"
    return (f"The redirect target {where} is a private address (LAN, loopback or link-local). "
            "Google refuses an authorization request whose redirect_uri is a private IP before "
            'it shows any consent screen — its error page reads "device_id and device_name are '
            'required for private IP" — so this consent will almost certainly fail, and no '
            "token is at risk either way. Remedy: set the instance public_url to the external "
            "https base a browser reaches this instance at (a tunnel or reverse-proxy "
            "hostname), and register that exact callback in the provider's OAuth app.")
