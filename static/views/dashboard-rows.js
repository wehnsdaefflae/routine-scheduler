// How ONE routine is drawn on the Routines page — as a card in the grid, and as a row in the
// detail table (lane rows and their member reordering included). Same data, two registers.
//
// Split out of dashboard.js, which carried this, the domains section, the week-strip drag ops
// and the page's own state in one 850-line closure. The two renderers share five small
// controls — run-now, the enable toggle, the identity swatch, the real schedule text, and the
// lane/domain chips — which is exactly why they belong in one module and not two: a card and
// a row that disagreed about what "the schedule" means is the bug R313 was.
//
// `ctx` is the page's live state, read through getters rather than captured: this module is
// rebuilt on every repaint while the page's maps are replaced by each load(), so a captured
// reference would render the state before the refresh.

import { api } from "/static/api.js";
import { slugColor } from "/static/components/charts.js";
import { heartbeat } from "/static/components/heartbeat.js";
import { laneControls, laneProgress, openLaneEditor } from "/static/components/lanemanage.js";
import { summaryLine } from "/static/md.js";
import { chip, el, fmtCost, fmtDur, fmtNum, storage, tagChip, toast, toastError,
         when } from "/static/util.js";
import { WORKING as RUNNING } from "/static/states.js";

const LANES_OPEN_KEY = "rsched_dash_lanes_open";

// "Jul: 1.2M tok · $4.31 (Jun: 0.9M · $3.10)" — the durable monthly series, not last-run
function spendLine(spend) {
  if (!spend?.current) return null;
  const cell = (c) => c ? [fmtNum(c.tokens) + " tok", fmtCost({ cost: c.cost })].filter(Boolean).join(" · ") : "—";
  const monthName = (m) => new Date(m + "-01T00:00:00").toLocaleString("en", { month: "short" });
  let text = `${monthName(spend.month)}: ${cell(spend.current)}`;
  if (spend.prev) text += `  (${monthName(spend.prev_month)}: ${cell(spend.prev)})`;
  const growing = spend.prev && spend.current.tokens > spend.prev.tokens * 1.2;
  return el("div", { class: "muted small",
    title: "this month's spend from the durable usage stream (survives run retention)" },
    text, growing ? el("span", { class: "chip partial", style: "margin-left:6px" }, "↑ growing") : null);
}

function statsLine(run) {
  if (!run) return "";
  const parts = [];
  if (run.turns) parts.push(`${run.turns} turns`);
  if (run.elapsed_s != null) parts.push(fmtDur(run.elapsed_s));
  const tok = (run.usage?.in || 0) + (run.usage?.out || 0);
  if (tok) parts.push(`${fmtNum(tok)} tok`);
  const cost = fmtCost(run.usage);
  if (cost) parts.push(cost);
  return parts.join(" · ");
}

/** ctx: llmReady() · laneFor(slug) · domainFor(slug) · laneRecord(id) · lanesOrdered() ·
 *  laneData() · sortArrow(key) · onSort(key) · reload() · repaint() · revealDomain(id) */
export function routineRows(ctx) {
  // Table rows run ICON-ONLY controls (horizontal space, D72 follow-up); cards keep the
  // labelled versions. The resume glyph is the HOLLOW ▷ so it can never be mistaken for
  // the filled ▶ run-now sitting beside it — the action text lives in the hover title.
  function runNowBtn(c, cls = "btn small primary", icon = false) {
    return el("button", {
      class: cls,
      disabled: !ctx.llmReady(),
      title: ctx.llmReady() ? (icon ? "run now" : "") : "connect an LLM endpoint in Settings first",
      onclick: async (e) => {
        e.target.disabled = true;
        try {
          const r = await api(`/api/routines/${c.slug}/run`, { method: "POST" });
          location.hash = `#/run/${r.run_id}`;
        } catch (err) { toastError(err); e.target.disabled = false; }
      },
    }, icon ? "▶" : "▶ run now");
  }

  // D72: start/pause without the config page — one PATCH on `enabled`, from both views.
  // While a run is active the web layer refuses config edits (409 guard_not_active), so the
  // control disables itself instead of letting the click bounce into an error toast.
  function enableToggle(c, cls = "btn small ghost", icon = false) {
    const on = !!c.enabled;
    return el("button", {
      class: cls,
      disabled: !!c.active_run,
      title: c.active_run ? "a run is active — pausing waits until it ends"
        : on ? "pause this routine — schedule, triggers and one-shots stop firing (“▶ run now” still works)"
        : "resume this routine's schedule",
      onclick: async (e) => {
        e.target.disabled = true;
        try {
          await api(`/api/routines/${c.slug}`, { method: "PATCH", body: { enabled: !on } });
          toast(on ? `${c.name || c.slug} paused — nothing fires until resumed`
                   : `${c.name || c.slug} resumed`);
          await ctx.reload();
        } catch (err) { toastError(err); e.target.disabled = false; }
      },
    }, on ? (icon ? "⏸" : "⏸ pause") : (icon ? "▷" : "▷ resume"));
  }

  // The routine's identity color (charts.slugColor — the same hash the week strip's bars
  // use): with the strip's legend gone, the swatch on the row/card IS the color mapping.
  function swatch(slug) {
    return el("span", { class: "id-swatch", style: `background:${slugColor(slug)}`,
      title: "this routine's color in the week strip" });
  }

  // The schedule a routine will ACTUALLY fire on. A member of a SCHEDULED lane has its own
  // cron suppressed by the daemon — showing that vestigial cron here read as a lie (R313),
  // so a lane-driven row shows the lane's schedule instead. An unscheduled lane suppresses
  // nothing, so its members keep showing their own.
  function schedText(c) {
    const lane = ctx.laneFor(c.slug);
    if (!lane?.cron) return null;   // the caller renders the routine's own desc
    return `⛓ ${lane.name} — ${lane.paused ? "lane paused" : (lane.schedule_desc || "scheduled")}`;
  }

  // The two structures a routine sits in, as one chip row: its LANE (when it fires and with
  // whom) and its DOMAIN (what it shares). They are independent, so the row shows whichever a
  // routine has — side by side when it has both. Each chip goes where that structure is
  // edited: the lane chip opens the lane's editor (D80: this page is the lane-management
  // surface), the domain chip reveals its row in the section below. Both LOOK clickable, so
  // both must be — a chip beside a working one that does nothing teaches the wrong thing.
  // Membership itself is not on either: joining a domain is a save on the routine's own page.
  function structureChips(slug) {
    const lane = ctx.laneFor(slug);
    const dom = ctx.domainFor(slug);
    if (!lane && !dom) return null;
    return el("div", { class: "lanes-row" },
      lane ? el("button", { class: "chip lane-chip",
        title: `runs in lane “${lane.name}” — edit the lane`,
        onclick: (e) => { e.stopPropagation(); openLaneEditor(lane, ctx.laneData(), { reload: ctx.reload }); },
      }, `⛓ ${lane.name}`) : null,
      dom ? el("button", { class: "chip domain-chip",
        title: `shares config, secrets and a store with the “${dom.name}” domain — open it in `
             + "the Domains section; this routine's own page is where it joined",
        onclick: (e) => { e.stopPropagation(); ctx.revealDomain(dom.id); },
      }, `◈ ${dom.name}`) : null);
  }

  function card(c) {
    const stateChip = c.active_state ? chip(c.active_state, c.active_state)
      : c.retired ? chip("finished", "finished")
      : c.enabled ? chip("idle", "idle") : chip("disabled", "disabled");
    const last = c.last_run;
    const blocked = c.active_state === "waiting_user";
    const cls = ["card", RUNNING.has(c.active_state) ? "live" : "", blocked ? "attention" : ""]
      .filter(Boolean).join(" ");
    const stats = statsLine(last);
    return el("div", { class: cls },
      el("div", { class: "title" },
        swatch(c.slug),
        el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
        stateChip),
      (c.tags || []).length ? el("div", { class: "tags" }, c.tags.map((t) => tagChip(t))) : null,
      structureChips(c.slug),
      c.description ? el("div", { class: "desc" }, c.description) : null,
      blocked ? el("div", { class: "qflag" },
        el("span", {}, "waiting on your answer"),
        el("a", { class: "btn small primary", href: "#/questions", style: "margin-left:auto" }, "decide")) : null,
      el("div", { class: "meta" },
        el("span", schedText(c) ? { title: "lane-driven — the lane's schedule fires this routine; its own cron is suppressed" } : {},
          `⏱ ${schedText(c) || c.schedule_desc || "Manual"}`),
        c.next_fire ? el("span", { title: "next scheduled fire" }, "next ", when(c.next_fire, { mode: "rel" })) : null,
        c.open_questions ? el("a", { href: "#/questions", class: "chip blocking",
          title: "open questions waiting for you" }, `${c.open_questions} open question${c.open_questions > 1 ? "s" : ""}`) : null,
        c.decision_backlog ? el("a", { href: "#/questions", class: "chip failed",
          title: "this routine is starving on deferred decisions — answer some" }, "decision backlog") : null),
      spendLine(c.spend),
      // the past mirror of "next fire": the last runs at a glance — flaky ≠ green-today
      c.recent_runs?.length ? el("div", { class: "hb-row" }, heartbeat(c.recent_runs)) : null,
      last ? el("div", { class: "lastrun" },
          el("div", { class: "lr-line" }, when(last.ts), chip(last.state, last.state),
            stats ? el("span", { class: "muted small", title: "last run: turns · duration · tokens · cost" },
              stats) : null),
          el("div", { class: "lr-sum", title: last.summary || "" },
            summaryLine(last.summary, "(no summary)")))
        : el("div", { class: "lastrun" }, el("div", { class: "lr-sum faint" }, "never ran")),
      c.problems?.length ? el("div", { class: "problem" }, `⚠ ${c.problems[0]}`) : null,
      el("div", { class: "actions" },
        c.active_run
          ? el("a", { class: "btn small", href: `#/run/${c.active_run}` }, "◉ watch live")
          : runNowBtn(c),
        enableToggle(c),
        last ? el("a", { class: "btn small", href: `#/run/${last.run_id}` }, "last run") : null));
  }

  // ---- the detail table: same data, one row per routine, headers sort ------------------------
  // Compressed to five columns (operator ask — the twelve-column layout outgrew the screen):
  // state folds into the history strip (newest bar = last outcome, hover for detail; live/
  // attention row styling and the dimmed disabled row carry the rest), schedule+next stack in
  // one cell, the last run stacks its ts over the turns·duration·tokens·cost line, and open
  // questions ride the routine cell as a chip. Every dropped header's sort key stays
  // reachable in the filter bar's sort select.
  const COLS = [
    ["routine", "name"], ["history", null], ["schedule · next", "next"],
    ["last run", "activity"], ["", null],
  ];
  function table(shown) {
    const head = el("tr", {}, COLS.map(([label, key]) => el("th",
      key ? { style: "cursor:pointer", title: "sort by this column (click again to reverse)",
              onclick: () => ctx.onSort(key) }
          : {},
      label + ctx.sortArrow(key))));
    // U-order (user, 2026-08-13): inside an expanded lane the row order IS the fire
    // order, so the rows themselves are the reorder surface — drag one onto a sibling
    // (upper half = before it, lower half = after). The editor's ↑/↓ stays for precision.
    let dragFrom = null;
    const rowFor = (c, extraCls = "", lane = null) => {
      const last = c.last_run;
      const rowCls = [RUNNING.has(c.active_state) ? "live" : "",
        c.active_state === "waiting_user" ? "attention" : "",
        c.enabled && !c.retired ? "" : "disabled-row", extraCls]
        .filter(Boolean).join(" ");
      const stats = statsLine(last);
      const tr = el("tr", { class: rowCls },
        el("td", {}, swatch(c.slug), el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
          c.open_questions ? el("a", { href: "#/questions", class: "chip blocking",
            title: "open questions waiting for you" }, `${c.open_questions} open ?`) : null,
          c.description ? el("div", { class: "faint small", style: "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, c.description) : null,
          structureChips(c.slug)),
        el("td", { class: "hb-cell" }, c.recent_runs?.length
          ? heartbeat(c.recent_runs) : el("span", { class: "faint" }, "—")),
        el("td", { class: "muted small" },
          el("div", schedText(c)
            ? { title: "lane-driven — the lane's schedule fires this routine; its own cron is suppressed" }
            : {},
            // an always-visible marker: the row dim alone was too subtle. FINISHED and OFF are
            // different answers to "why is nothing happening" — one is the job being over.
            c.retired
              ? el("span", { class: "chip finished", style: "margin-right:6px",
                  title: "every final-goal condition is met — this routine is done and no "
                       + "longer fires. Reopen a goal condition to bring it back." }, "done")
              : c.enabled ? null : el("span", { class: "chip disabled", style: "margin-right:6px",
                  title: "paused — nothing fires until resumed" }, "off"),
            schedText(c) || c.schedule_desc || "manual"),
          c.next_fire ? el("div", { class: "faint" }, "next ", when(c.next_fire, { mode: "rel" })) : null),
        el("td", {}, last
          ? [el("a", { href: `#/run/${last.run_id}` }, when(last.ts)),
             stats ? el("div", { class: "faint small",
               title: "last run: turns · duration · tokens · cost" }, stats) : null]
          : el("span", { class: "faint" }, "never")),
        el("td", { class: "row-actions" },
          c.active_run
            ? el("a", { class: "btn small", href: `#/run/${c.active_run}`, title: "watch the live run" }, "◉")
            : runNowBtn(c, "btn small", true),
          enableToggle(c, "btn small ghost", true)));
      if (lane) {
        tr.draggable = true;
        tr.dataset.dragMember = c.slug;
        tr.ondragstart = (e) => {
          dragFrom = { lid: lane.id, slug: c.slug };
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", c.slug);
        };
        tr.ondragover = (e) => {
          if (!dragFrom || dragFrom.lid !== lane.id || dragFrom.slug === c.slug) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          tr.classList.add("drop-here");
        };
        tr.ondragleave = () => tr.classList.remove("drop-here");
        tr.ondragend = () => { dragFrom = null; };
        tr.ondrop = async (e) => {
          e.preventDefault();
          tr.classList.remove("drop-here");
          const from = dragFrom; dragFrom = null;
          if (!from || from.lid !== lane.id || from.slug === c.slug) return;
          const raw = ctx.laneRecord(lane.id);
          const rec = raw?.members?.find((m) => m.slug === from.slug);
          if (!rec) return;
          const list = raw.members.filter((m) => m.slug !== from.slug);
          const at = list.findIndex((m) => m.slug === c.slug);
          if (at < 0) return;
          const box = tr.getBoundingClientRect();
          const before = e.clientY < box.top + box.height / 2;
          list.splice(at + (before ? 0 : 1), 0, rec);
          try {
            await api(`/api/lanes/${lane.id}`, { method: "PATCH", body: { members: list } });
            toast(`“${lane.name}” fire order: ${list.map((m) => m.slug).join(" → ")}`);
          } catch (ex) { toastError(ex); }
          ctx.reload();
        };
      }
      return tr;
    };
    // D73 + F281: each lane is its own collapsible row — expanding lists its members right
    // beneath it, in the lane's FIRE order (not the table sort). A routine in a lane lives
    // ONLY under that lane's row (reviewer order 2026-08-06: the flat list used to repeat
    // every member, so the table double-listed them); the flat sorted list below carries just
    // the routines no lane claims. Expansion persists like the view mode, keyed by lane ID —
    // a rename must not silently collapse the row.
    const openLanes = new Set(JSON.parse(storage.get(LANES_OPEN_KEY) || "[]"));
    const bySlug = new Map(shown.map((c) => [c.slug, c]));
    const rows = [];
    for (const lane of ctx.lanesOrdered()) {
      const members = lane.members.map((s) => bySlug.get(s)).filter(Boolean);
      // A lane whose members are all filtered out loses its header too — that is the filter
      // working. An EMPTY lane keeps its row: this row is the only way back into its editor —
      // a lane created ahead of its members would otherwise vanish the moment it was made.
      if (lane.members.length && !members.length) continue;
      const open = openLanes.has(lane.id);
      const raw = ctx.laneRecord(lane.id);
      // D80: the lane row carries its management — run now / pause / edit (the buttons
      // stopPropagation so the row's expand toggle keeps working), plus how far an in-flight
      // chain has got: which member of how many is running.
      const controls = raw ? laneControls(raw, ctx.laneData(), { reload: ctx.reload }) : [];
      const progress = raw ? laneProgress(raw, ctx.laneData()) : null;
      rows.push(el("tr", { class: "lane-row", "data-lane-row": lane.id },
        el("td", { colSpan: COLS.length,
          title: open ? "collapse this lane's rows" : "expand this lane's member rows",
          onclick: () => {
            open ? openLanes.delete(lane.id) : openLanes.add(lane.id);
            storage.set(LANES_OPEN_KEY, JSON.stringify([...openLanes]));
            ctx.repaint();
          } },
          el("div", { class: "row", style: "justify-content:space-between;align-items:center;gap:8px" },
            el("span", {},
              el("span", { class: "tri" }, open ? "▾ " : "▸ "),
              `⛓ ${lane.name}`,
              lane.paused ? el("span", { class: "muted small", "data-lane-paused": "",
                style: "margin-left:8px" }, "⏸ paused") : null,
              el("span", { class: "faint small", style: "margin-left:8px" },
                `${members.length} routine${members.length === 1 ? "" : "s"} · fire order`
                + (lane.cron ? ` · ${lane.paused ? "paused" : lane.schedule_desc}` : "")),
              progress ? el("span", { style: "margin-left:8px" }, progress) : null),
            el("span", { class: "row", style: "gap:6px" }, ...controls)))));
      if (open) for (const m of members) rows.push(rowFor(m, "lane-member", lane));
    }
    const inLane = new Set(ctx.lanesOrdered().flatMap((l) => l.members));
    for (const c of shown) if (!inLane.has(c.slug)) rows.push(rowFor(c));
    // Five compressed columns fit the normal shell column — the D72 full-width breakout
    // existed for the old twelve-column layout and is retired with it.
    return el("div", { class: "panel", style: "padding:0" },
      el("div", { class: "tablewrap" },
        el("table", { class: "list stack" }, el("thead", {}, head), el("tbody", {}, rows))));
  }

  return { card, table };
}
