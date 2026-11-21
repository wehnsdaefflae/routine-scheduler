"""The observation tails for a call that RAN without all of its declared secrets.

The one place the sandbox's *narrowings* of the declared-only rule are put into words: from
inside the util a withheld secret is indistinguishable from one nobody provisioned.

- CONSENT (`withheld_optional`) — an OPTIONAL secret the routine may not see; the call runs
  prompt-free. Undecided names are requestable, so named; a denied one stays a count (a denial
  enumerates nothing).
- CONSENT, required half (`withheld_declined`) — a REQUIRED secret the user DECLINED: withheld,
  and the call proceeds. A count only.
- VERB (`withheld_verb_scoped`) — a secret the util's `secrets:` line binds to a verb this call
  did not run (a call naming no verb gets none of the bound names).
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
        # A count only (a denial enumerates nothing); the repair route is named because an
        # auth failure would otherwise read as a credential nobody provisioned.
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
