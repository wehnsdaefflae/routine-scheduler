"""Gate checks that ask a server: a mailbox, a web page or API, a Steward hub. Jail side, stdlib
only. Every one of them reads and never writes: IMAP folders are opened readonly and fetched with
BODY.PEEK, HTTP is GET, and nothing a run relies on (a read flag, a cursor) is moved. Both speak
verified TLS — the mailbox check sends a password.
"""

from __future__ import annotations

import base64
import datetime as _dt
import email.utils
import imaplib
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email import policy
from email.parser import HeaderParser
from pathlib import Path

from kit_common import NET_TIMEOUT_S, UnknownError, baseline, dig, digest, secret, since, web_login

_INTERNALDATE = re.compile(rb'INTERNALDATE "([^"]+)"')
#: The newest messages a folder's headers are read for. Past it, a folder where none of them
#: counts is one the check could not read whole — work, never "nothing waits".
FETCH_MAX = 500
#: The most of an answer `url_changed` compares. A longer one could change where it never looks.
MAX_BODY = 8 * 1024 * 1024
#: Headers as a mail reader shows them: folded lines unfolded, RFC 2047 encoded words decoded.
_HEADERS = HeaderParser(policy=policy.default)


# ------------------------------------------------------------------------------ mail


def mail(check: dict, ctx: dict) -> tuple[bool, str, str]:
    user, password = _login(check)
    senders, domains = _senders(check, ctx)
    mode = str(check.get("mode") or "unseen")
    cutoff = since(ctx) if mode == "new" else None
    socket.setdefaulttimeout(NET_TIMEOUT_S)
    try:
        # An explicit context: without one, IMAP4_SSL accepts ANY certificate (imaplib's
        # backwards-compatible default) and the login below would hand the password to
        # whoever answered. A certificate that fails verification is an OSError — work.
        conn = imaplib.IMAP4_SSL(str(check["host"]), int(check.get("port") or 993),
                                 ssl_context=ssl.create_default_context(),
                                 timeout=NET_TIMEOUT_S)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise UnknownError(f"could not reach {check['host']}: {exc}") from exc
    try:
        try:
            conn.login(user, password)
        except imaplib.IMAP4.error as exc:
            raise UnknownError(f"login refused: {exc}") from exc
        total = 0
        for folder in check.get("folders") or ["INBOX"]:
            total += _count(conn, str(folder), check, cutoff, senders, domains)
    finally:
        try:
            conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
    folders = ", ".join(check.get("folders") or ["INBOX"])
    what = "unread" if cutoff is None else "new since the last ok run"
    if total:
        return True, f"{total} matching message(s) {what} in {folders}", ""
    return False, f"no matching message {what} in {folders}", ""


def _count(conn, folder: str, check: dict, cutoff, senders, domains) -> int:
    typ, _ = conn.select(_quote(_resolve(conn, folder)), readonly=True)   # readonly: reads only
    if typ != "OK":
        raise UnknownError(f"could not open folder {folder!r}")
    criteria = ("UNSEEN" if cutoff is None else
                f'SINCE {(cutoff - _dt.timedelta(days=1)).strftime("%d-%b-%Y")}')
    typ, data = conn.search(None, criteria)
    if typ != "OK":
        raise UnknownError(f"search in {folder!r} failed")
    numbers = (data[0] or b"").split()
    if not numbers:
        return 0
    typ, fetched = conn.fetch(b",".join(numbers[-FETCH_MAX:]),
                              "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
    if typ != "OK":
        raise UnknownError(f"could not read the headers in {folder!r}")
    n = 0
    for item in fetched:
        if not (isinstance(item, tuple) and len(item) > 1):
            continue
        if cutoff is not None:
            m = _INTERNALDATE.search(bytes(item[0]))
            when = email.utils.parsedate_to_datetime(m[1].decode()) if m else None
            if when is None or when <= cutoff:
                continue
        if _matches(bytes(item[1]).decode("utf-8", "replace"), check, senders, domains):
            n += 1
    if not n and len(numbers) > FETCH_MAX:
        raise UnknownError(f"{len(numbers)} messages in {folder!r} and none of the newest "
                           f"{FETCH_MAX} counts — the older ones were not read")
    return n


def _matches(header: str, check: dict, senders: list[str], domains: list[str]) -> bool:
    """Does one message count? The POSITIVE filters — the sender list, `from_any`,
    `subject_any` — are a watch list: a message counts when it meets ANY of them (a known
    correspondent OR a subject that names the project), because a reply that happens to come
    from a new address is still work. `from_domains_not` only ever takes messages away.

    Each filter reads a header the way a mail reader shows it — unfolded, encoded words
    decoded — because a Subject folded onto a second line, an umlaut a client sent as
    `=?utf-8?b?…?=`, or an address folded under a long display name is the same header to the
    person who wrote the watch list.
    """
    parsed = _HEADERS.parsestr(header)
    sender = str(parsed.get("from") or "").lower()
    subject = str(parsed.get("subject") or "").lower()
    domain = email.utils.parseaddr(sender)[1].rpartition("@")[2]
    if any(domain == d or domain.endswith("." + d)
           for d in (str(x).lower() for x in check.get("from_domains_not") or [])):
        return False
    from_any = [str(s).lower() for s in check.get("from_any") or []]
    subject_any = [str(s).lower() for s in check.get("subject_any") or []]
    if not (senders or domains or from_any or subject_any):
        return True
    return (any(s in sender for s in [*senders, *domains, *from_any])
            or any(s in subject for s in subject_any))


_LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def _resolve(conn, folder: str) -> str:
    r"""A folder named by its SPECIAL-USE flag (`\\All`, `\\Sent`, `\\Junk`) → the name this
    server gives it, which differs by provider and by the account's language ("[Gmail]/All
    Mail", "[Google Mail]/Alle Nachrichten"). Any other name is used as written.
    """
    if not folder.startswith("\\"):
        return folder
    typ, lines = conn.list()
    if typ != "OK":
        raise UnknownError("could not list the mailbox's folders")
    for line in lines or []:
        m = _LIST_LINE.match(bytes(line or b"").strip())
        if m and folder.lower().encode() in m["flags"].lower().split():
            return m["name"].decode("utf-8", "replace").strip().strip('"')
    raise UnknownError(f"this mailbox has no folder flagged {folder}")


def _quote(folder: str) -> str:
    return folder if re.fullmatch(r"[A-Za-z0-9_./-]+", folder) else '"' + folder.replace(
        '"', '\\"') + '"'


def _login(check: dict) -> tuple[str, str]:
    if check.get("accounts_secret"):
        try:
            accounts = json.loads(secret(str(check["accounts_secret"])))
        except ValueError as exc:
            raise UnknownError(f"{check['accounts_secret']} is not a JSON account map") from exc
        if not isinstance(accounts, dict) or not accounts:
            raise UnknownError(f"{check['accounts_secret']} holds no accounts")
        name = str(check.get("account") or next(iter(accounts)))
        acct = accounts.get(name)
        if not isinstance(acct, dict) or not acct.get("email") or not acct.get("app_password"):
            raise UnknownError(f"account {name!r} is not in {check['accounts_secret']}")
        return str(acct["email"]), str(acct["app_password"])
    return secret(str(check["user_secret"])), secret(str(check["password_secret"]))


def _senders(check: dict, ctx: dict) -> tuple[list[str], list[str]]:
    rel = check.get("senders_file")
    if not rel:
        return [], []
    try:
        doc = json.loads((Path(ctx["routine_dir"]) / str(rel)).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnknownError(f"could not read the sender list {rel}: {exc}") from exc
    senders = [s.lower() for s in doc.get("senders") or [] if isinstance(s, str) and s.strip()]
    domains = [d.lower() for d in doc.get("sender_domains") or []
               if isinstance(d, str) and d.strip()]
    if not senders and not domains:
        # an empty filter would match nothing and skip every fire — refuse rather than guess
        raise UnknownError(f"the sender list {rel} is empty")
    return senders, domains


# ------------------------------------------------------------------------------ web


def _shown(url: str) -> str:
    """A URL as a reason may print it: without its query, which is where many APIs take a key
    — and every reason lands in gate.json and a skipped fire's result.md.
    """
    return url.split("?", maxsplit=1)[0]


def _get(url: str, headers: dict) -> bytes:
    # the address the operator configured; the jail it runs in reaches no local file it names
    req = urllib.request.Request(url, headers={"User-Agent": "rsched-gate/1",  # noqa: S310
                                               **headers})
    shown = _shown(url)
    try:
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT_S) as resp:   # noqa: S310
            body = resp.read(MAX_BODY + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UnknownError(f"could not fetch {shown}: {exc}") from exc
    if len(body) > MAX_BODY:
        raise UnknownError(f"{shown} answered more than {MAX_BODY // 2**20} MiB — a change past "
                           "that would go unseen")
    return body


def _basic(entry: dict) -> dict:
    token = base64.b64encode(f"{entry['user']}:{entry.get('pass', '')}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def url_changed(check: dict, ctx: dict) -> tuple[bool, str, str]:
    headers: dict = {}
    if check.get("token_secret"):
        headers["Authorization"] = f"Bearer {secret(str(check['token_secret']))}"
    if check.get("auth_source"):
        headers.update(_basic(web_login(str(check.get("auth_secret") or "WEB_AUTH_SOURCES"),
                                        str(check["auth_source"]))))
    body = _get(str(check["url"]), headers)
    fp = digest(_select(body, str(check.get("select") or "body")))
    try:
        before = baseline(ctx, str(check["id"]))
    except UnknownError as exc:
        return True, str(exc), fp
    shown = _shown(str(check["url"]))
    if fp != before:
        return True, f"{shown} answers differently than at the last ok run", fp
    return False, f"{shown} is unchanged since the last ok run", fp


def _select(body: bytes, select: str) -> object:
    if select == "body":
        return body
    if select == "feed":
        text = body.decode("utf-8", "replace")
        ids = re.findall(r"<(?:guid|id)[^>]*>\s*([^<\s][^<]*?)\s*</(?:guid|id)>", text)
        ids += re.findall(r"<link[^>]*href=\"([^\"]+)\"", text)
        ids += re.findall(r"<item\b.*?<link>\s*([^<\s]+)\s*</link>", text, flags=re.DOTALL)
        if not ids:
            raise UnknownError("the answer is not an RSS/Atom feed with item ids or links")
        return sorted(set(ids))
    try:
        value: object = json.loads(body)
    except ValueError as exc:
        raise UnknownError("the answer is not JSON") from exc
    return dig(value, select.removeprefix("json:"))


def hub_feedback(check: dict, ctx: dict) -> tuple[bool, str, str]:
    entry = web_login(str(check.get("auth_secret") or "WEB_AUTH_SOURCES"),
                      str(check.get("source") or "steward"))
    host = str(entry.get("host") or "").strip().rstrip("/")
    if not host:
        raise UnknownError("the hub login names no host")
    base = host if host.startswith("http") else f"https://{host}"
    token = _hub_token(ctx)
    url = (f"{base}/api.php?project={urllib.parse.quote(str(check['project']))}"
           f"&what=feedback&token={urllib.parse.quote(token)}")
    try:
        answer = json.loads(_get(url, _basic(entry)))
    except ValueError as exc:
        raise UnknownError("the hub's feedback answer is not JSON") from exc
    if not isinstance(answer, dict) or not answer.get("ok") or "count" not in answer:
        raise UnknownError(f"the hub refused the feedback read ({str(answer)[:120]})")
    count = int(answer["count"])
    if count:
        return True, f"{count} feedback entr{'y' if count == 1 else 'ies'} on the hub page", ""
    return False, "no unconsumed feedback on the hub page", ""


def _hub_token(ctx: dict) -> str:
    """The hub's namespace marker (not a credential — the kit says so), read from the
    library's copy of the kit, which is how every routine's pages get it.
    """
    store = Path(str(ctx.get("libraries_home") or "")) / "web" / "steward" / "store.php"
    try:
        m = re.search(r"const\s+TOKEN\s*=\s*'([^']+)'", store.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UnknownError(f"could not read the hub kit's namespace marker: {exc}") from exc
    if not m:
        raise UnknownError("the hub kit declares no namespace marker")
    return m[1]
