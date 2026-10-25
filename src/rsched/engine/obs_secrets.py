"""The observation tails for a call that RAN without all of its declared secrets.

Split out of `observations.py` when R2622 added the second one and the file crossed the
house bar. They belong together because they read as ONE thing to the author — a credential
the util declares did not arrive, and here is why — and apart from the rest of the renderer
because they are the only place the sandbox's *narrowings* of the declared-only rule are put
into words.

Two axes narrow it, and both are said out loud for the same reason: from inside the util a
withheld secret is indistinguishable from one nobody provisioned, and the reasonable next
move on that reading is provisioning a credential that already exists.

- CONSENT (F290/D51, `withheld_optional`) — an OPTIONAL secret the routine may not see. The
  call runs prompt-free instead of blocking on an exposure ask. Undecided names are
  requestable, so they are named; a denied one stays a count, because a denial enumerates
  nothing (R17).
- CONSENT, the required half (F756, `withheld_declined`) — a REQUIRED secret the user
  DECLINED. A decline protects the credential, not the util: the name is withheld and the
  call proceeds, where until F756 one declined name refused every call of that util —
  including calls needing none of it. A count only, for the same R17 reason.
- VERB (R2622, `withheld_verb_scoped`) — a secret the util's `secrets:` line binds to a verb
  this call did not run. A call that names no verb at all (the util's default path,
  `--selftest`) gets none of the bound names, and is told so: that is how such a call learns
  a credential exists for a verb it did not invoke.
"""

from __future__ import annotations


def withheld_notes(obs: dict) -> str:
    """Both tails, in order, for one observation — empty when the call got everything it
    declared, which is almost every call.
    """
    out = ""
    if wo := obs.get("withheld_optional"):
        bits = []
        if wo.get("undecided"):
            bits.append(f"{', '.join(wo['undecided'])} (not yet granted — if this call "
                        "actually needed it, request exposure via ask_user with "
                        f"request 'secret:{wo['undecided'][0]}')")
        if wo.get("denied"):
            bits.append(f"{wo['denied']} declined by the user")
        out += "\n[note] optional secret(s) withheld from this call: " + "; ".join(bits)
    if n := obs.get("withheld_declined"):
        # F756 — CONSENT, the required half: the user DECLINED these, so they were withheld
        # and the call ran without them (before this one declined name refused the whole
        # util, even a call needing none of it). A count only: a denial enumerates nothing
        # (R17). The repair route is named because an auth failure inside the util would
        # otherwise read as a credential nobody provisioned (R2621).
        out += (f"\n[note] {n} required secret{'s' if n != 1 else ''} this call declares "
                f"{'were' if n != 1 else 'was'} withheld: you declined exposing "
                f"{'them' if n != 1 else 'it'} to this routine. The call ran WITHOUT "
                f"{'them' if n != 1 else 'it'} — if it failed for want of a credential, that "
                "is why; the mapping is editable on the routine page (secret exposure), or "
                "file a deferred ask_user explaining why this routine needs it.")
    if vs := obs.get("withheld_verb_scoped"):
        ran = f"the verb '{vs['verb']}'" if vs.get("verb") else "no verb (the default path)"
        out += (f"\n[note] verb-scoped secret(s) not injected: {', '.join(vs['secrets'])} — "
                f"this call ran {ran}; the util's `secrets:` line binds them to other verbs")
    return out
