// Uncovered capability cards share the panel's live state.
import { el } from "/static/util.js";
import { KIND_LABEL } from "/static/components/abilities-data.js";

export function createOrphanCard({surface, caps, savedCaps, held, docs, needs, baseName, stackRow, render}) {
  /** The live mapping an entity class comes off. `util:` and `action:` are the two classes a
   *  capability row carries — the two this panel switches; anything else the surface reports is
   *  somebody else's to change. */
  const capSet = (cls) => (cls === "util" ? caps.utils : cls === "action" ? caps.actions : null);

  /** Would a doc the panel holds RIGHT NOW put this capability straight back? The server raises
   *  the mapping to cover every held doc's `requires:` before it floors it, so dropping what a
   *  held doc asks for changes nothing. The surface answered this against the SAVED docs; asked
   *  again here it catches a covering doc ticked on since — the row stops offering a drop the
   *  moment the checkbox above became the better way out. */
  function coveredNow(cls, name) {
    return [...held].some((slug) => {
      const r = needs(docs.find((d) => d.slug === slug) || {});
      return cls === "action" ? (r.actions || []).includes(name)
        : (r.utils || []).some((u) => baseName(u) === baseName(name));
    });
  }

  /** One capability row of the orphan card, read once: which mapping owns it, who switched it
   *  on, and where it stands between the saved mapping and the staged one. */
  function orphanInfo(node) {
    const [cls, ...rest] = node.id.split(":");
    const name = rest.join(":");
    const fix = node.fix || {};
    const set = capSet(cls);
    const saved = savedCaps[cls] || new Set();
    return { node, cls, name, set,
             // Provenance rides the fix, the one place it is a fact rather than a guess: a
             // capability the DOMAIN's shared block names survives this routine's save (the
             // floor counts inherited permissions, because that one is the domain's to drop).
             // `fix.domain` alone: an install_util fix also carries `name`, which is the
             // UTIL, so preferring it would name the util as the domain that supplied it.
             domain: fix.owner === "domain" ? (fix.domain || "") : "",
             covered: coveredNow(cls, name),
             gone: !saved.has(name),
             staged: saved.has(name) && !!set && !set.has(name) };
  }

  /** What one row hands the reader, which depends entirely on whose capability it is.
   *
   *  OWN: the drop — staged like every other change here, committed by the same save, and the
   *  landing site the surface's `cover_or_drop` / `install_util` offers aim at.
   *  DOMAIN: nothing to press. A drop here is a no-op the next load undoes, so the row names
   *  the domain that switched it on and travels to the editor that owns it.
   *  COVERED SINCE: nothing to press either — saving now settles the row the other way.
   */
  function orphanOffer(info) {
    const { node, name } = info;
    const why = node.state === "uncovered" ? node.why
      : [node.why, node.effect].filter(Boolean).join(" — ");
    if (info.covered) {
      return { note: `${why}. A doc ticked on above requires it, so saving covers this row `
                 + "instead: the capability stays, with the conduct prose behind it." };
    }
    const id = el("span", { class: "ent-id" }, name);
    if (info.domain) {
      return { control: el("div", { class: "row" }, id,
                 el("a", { class: "btn small ghost", href: "#/routines",
                           title: `the domain “${info.domain}” switched this on; its shared `
                             + "config is edited on the Routines page" },
                   "the domain’s config ↗")),
               note: `${why}. A capability the domain supplies survives a save here, so it `
                 + "comes off in that domain's shared config. Ticking on a doc above that "
                 + "requires it settles the row in this panel instead." };
    }
    if (info.staged) {
      return { control: el("div", { class: "row" }, el("s", { class: "ent-id" }, name),
                 el("button", { type: "button", class: "btn small ghost", "data-drop": node.id,
                                title: `put ${name} back — nothing is saved yet`,
                                onclick: () => { info.set.add(name); render(); } }, "keep")),
               note: "staged: the save below switches it off, after which the routine cannot "
                 + "do this at all." };
    }
    return { control: el("div", { class: "row" }, id,
               el("button", { type: "button", class: "btn small ghost", "data-drop": node.id,
                              title: `switch ${name} off — the routine loses it entirely. `
                                + "Ticking on a doc above that requires it is the other way to "
                                + "settle this row.",
                              onclick: () => { info.set.delete(name); render(); } }, "drop")),
             note: why };
  }

  /** Capabilities switched on that no held doc requires — the domain-inheritance blind spot
   *  and the one place a capability comes off. Dropping one was reachable only as a side effect
   *  of pressing save: the server floors away whatever no held doc requires, so the routine lost
   *  it with nothing on screen saying so.
   *
   *  The reserved util MISSING from the library is here too, because it is the same act. Only a
   *  run writes a util, so the half a person performs is dropping the name — out of the mapping
   *  this card edits. One that a held doc still requires is not in this card: dropping it would
   *  be undone by the raise — the doc holding it is the thing to untick.
   */
  function orphanCard() {
    if (!surface) return null;
    const rows = surface.filter((n) => n.state === "uncovered" || n.state === "absent")
      .map(orphanInfo)
      // `gone` is a capability the last save already settled, or one held through a util TAG,
      // which this card does not switch. Either way the mapping in front of the reader no
      // longer holds it, so there is nothing here to act on.
      .filter((i) => i.set && !i.gone && !(i.node.state === "absent" && i.covered));
    if (!rows.length) return null;
    return el("div", { class: "ability orphan", "data-ability": "(uncovered)" },
      el("div", { class: "ability-head" },
        el("span", {}, "⚑"),
        el("div", {},
          el("div", { class: "ability-name" }, "Switched on by nothing here"),
          el("div", { class: "muted small prose" },
            "The routine may use these, but no conduct doc above asked for them — so it acts "
            + "without the prose that normally comes with them. Two ways out of a row: tick on "
            + "a doc above that requires it, which brings that prose with it, or drop the "
            + "capability, after which the routine can no longer do the thing at all.")),
        el("span", { class: "pill soft" }, `${rows.length}`)),
      el("ul", { class: "ability-stack" }, ...rows.map((info) => {
        const li = stackRow({ state: info.node.severity, entity: info.name,
                              kind: KIND_LABEL[info.cls] || info.cls, ...orphanOffer(info) });
        li.setAttribute("data-orphan", info.node.id);
        return li;
      })));
  }

  return orphanCard;
}
