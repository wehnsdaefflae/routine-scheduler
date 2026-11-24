// The Basic Catalog's DISPLAY and LAYOUT components — D310 step 3, twelve of the eighteen.
//
// Text · Image · Icon · Video · AudioPlayer · Divider — display, each a leaf.
// Row · Column · List · Card · Tabs · Modal          — layout, each holding children.
//
// They share a module because they share the ONE property that matters to this layer: they are
// not interactive. None of them writes to the data model, none has `@checks`, none dispatches an
// action; a container's whole job is to put its children somewhere. The six interactive
// components are a separate module for exactly that reason — they are where two-way binding,
// validation and user activation live, and those are read and changed together.
//
// Every widget is `(component, api) => Element`. A widget may ONLY use `api`: it never sees the
// store, the data model or a sibling, so `weight`, `@accessibility` and the error node are the
// core's business and a widget cannot forget them.
//
// WHAT IS NEW TO THIS CONSOLE, which is the point of the operator's `a-rich-plus` answer — the
// console today has no Tabs, no Modal, no Slider, no ChoicePicker, no DateTimeInput, no media
// components at all. Tabs and Modal are here, and both are built as the catalog declares them
// rather than as the console's existing cards would suggest: a Tab is a label and a child, and a
// Modal is a TRIGGER plus CONTENT, not a flag on a card.

import { registerCatalog, BASIC_CATALOG_ID } from "./render.js";
import { callBasicFunction } from "./functions.js";
import { asText } from "./surface.js";

//: `justify` and `align` as the catalog spells them → the flex values. A value the catalog does
//: not declare is left off rather than passed through: an arbitrary string in a style attribute
//: is how a malformed surface silently restyles a page.
const JUSTIFY = {
  start: "flex-start", center: "center", end: "flex-end",
  spaceBetween: "space-between", spaceAround: "space-around", spaceEvenly: "space-evenly",
};
const ALIGN = { start: "flex-start", center: "center", end: "flex-end", stretch: "stretch" };

//: A flex container: Row, Column and List all are one, differing only in direction.
function flex(component, api, direction, className) {
  const node = api.el("div", className);
  node.style.display = "flex";
  node.style.flexDirection = direction;
  if (JUSTIFY[component.justify]) node.style.justifyContent = JUSTIFY[component.justify];
  if (ALIGN[component.align]) node.style.alignItems = ALIGN[component.align];
  node.append(...api.children());
  return node;
}

export const LAYOUT_WIDGETS = {
  //: A run of text. `variant` carries the catalog's own type scale, which this console already
  //: has as tokens — so a heading is a class, never a hard-coded font size.
  Text: (component, api) => {
    const node = api.el("div", `a2-text a2-text-${component.variant ?? "body"}`);
    node.textContent = api.text("text");
    return node;
  },

  //: An image. `description` is the alt text and is REQUIRED to be rendered as one even when
  //: absent — an empty alt is a deliberate "decorative", a missing alt is a bug.
  Image: (component, api) => {
    const node = api.el("img", `a2-image a2-fit-${component.fit ?? "contain"}`);
    node.src = api.text("url");
    node.alt = api.text("description");
    if (component.variant) node.classList.add(`a2-image-${component.variant}`);
    return node;
  },

  //: An icon by NAME, from the catalog's own name set. Drawn as a glyph span rather than an
  //: `<img>`: the name is a token in a font the console already ships, so an icon costs no
  //: request and inherits the text colour it sits in.
  Icon: (component, api) => {
    const node = api.el("span", "a2-icon");
    node.dataset.icon = String(component.name ?? "");
    node.setAttribute("aria-hidden", "true");
    return node;
  },

  //: A video, with its poster. `controls` always: an agent-declared surface may not take the
  //: user's ability to pause away, which is an accessibility floor rather than a preference.
  Video: (component, api) => {
    const node = api.el("video", "a2-video");
    node.src = api.text("url");
    const poster = api.text("posterUrl");
    if (poster) node.poster = poster;
    node.controls = true;
    return node;
  },

  //: Audio, likewise with controls. The description becomes the accessible name, because an
  //: audio player with no label is unusable to a screen reader and the catalog offers no other
  //: place to put one.
  AudioPlayer: (component, api) => {
    const node = api.el("audio", "a2-audio");
    node.src = api.text("url");
    node.controls = true;
    const described = api.text("description");
    if (described) node.setAttribute("aria-label", described);
    return node;
  },

  //: A rule. `axis` chooses which way it runs, so a Row can be divided as well as a Column.
  Divider: (component, api) => api.el(
    "div", `a2-divider a2-divider-${component.axis === "vertical" ? "vertical" : "horizontal"}`),

  Row: (component, api) => flex(component, api, "row", "a2-row"),
  Column: (component, api) => flex(component, api, "column", "a2-column"),

  //: A List is a Row or a Column by its `direction`, and it is the container a ChildList TEMPLATE
  //: is usually bound to — which is why it carries no special code here: the core's walk already
  //: hands each child the scope of the array item it was instantiated for.
  List: (component, api) => flex(
    component, api, component.direction === "horizontal" ? "row" : "column", "a2-list"),

  //: A card around ONE child. The catalog is explicit that it is one child and not a list, which
  //: is the typing rule that keeps a validator able to check the tree.
  Card: (component, api) => {
    const node = api.el("div", "a2-card");
    const child = api.child("child");
    if (child) node.append(child);
    return node;
  },

  //: TABS — new to this console. Each tab is `{title, child}`; the selection is renderer-local
  //: state, because the catalog gives it no data path, and switching tabs must not need an
  //: agent round trip. Keyboard behaviour is the WAI-ARIA tabs pattern: arrows move, and the
  //: panel is labelled by its tab.
  Tabs: (component, api) => {
    const node = api.el("div", "a2-tabs");
    const strip = api.el("div", "a2-tabstrip");
    strip.setAttribute("role", "tablist");
    const panel = api.el("div", "a2-tabpanel");
    panel.setAttribute("role", "tabpanel");
    const tabs = Array.isArray(component.tabs) ? component.tabs : [];
    let selected = Number(node.dataset.selected ?? 0);

    const show = (index) => {
      selected = Math.max(0, Math.min(index, tabs.length - 1));
      node.dataset.selected = String(selected);
      panel.textContent = "";
      for (const [i, button] of [...strip.children].entries()) {
        const current = i === selected;
        button.setAttribute("aria-selected", current ? "true" : "false");
        button.tabIndex = current ? 0 : -1;
        button.classList.toggle("a2-tab-on", current);
      }
      const tab = tabs[selected];
      if (!tab) return;
      panel.setAttribute("aria-label", asText(api.resolve(tab.title)));
      // A tab's child is named by id INSIDE the tab object, so it is drawn through the core's
      // id-addressed renderer rather than through `api.child`, which reads a property name.
      if (tab.child !== undefined) panel.append(api.drawChild(String(tab.child)));
    };

    for (const [index, tab] of tabs.entries()) {
      const button = api.el("button", "a2-tab");
      button.type = "button";
      button.setAttribute("role", "tab");
      button.textContent = asText(api.resolve(tab.title));
      button.addEventListener("click", () => show(index));
      button.addEventListener("keydown", (event) => {
        if (event.key === "ArrowRight") show(selected + 1);
        if (event.key === "ArrowLeft") show(selected - 1);
      });
      strip.append(button);
    }
    node.append(strip, panel);
    show(selected);
    return node;
  },

  //: A MODAL — also new here. The catalog's shape is a TRIGGER and CONTENT, so the trigger is
  //: always on the page and the content appears in a `<dialog>`: that is what makes it a modal
  //: rather than a card that happens to be hidden, and `showModal` gives the focus trap and the
  //: Escape key for free instead of hand-rolling both.
  Modal: (component, api) => {
    const node = api.el("div", "a2-modal");
    const dialog = api.el("dialog", "a2-dialog");
    const content = api.child("content");
    if (content) dialog.append(content);
    const close = api.el("button", "a2-dialog-close");
    close.type = "button";
    close.textContent = "Close";
    close.addEventListener("click", () => dialog.close());
    dialog.append(close);
    const trigger = api.child("trigger");
    if (trigger) {
      const opener = api.el("span", "a2-modal-trigger");
      opener.append(trigger);
      opener.addEventListener("click", () => dialog.showModal());
      node.append(opener);
    }
    node.append(dialog);
    return node;
  },
};

//: Registering the display and layout half of the Basic Catalog, plus the function table every
//: component of this catalog resolves `{"@call": …}` against. The interactive half registers
//: itself from its own module, and `registerCatalog` merges — so neither module has to know the
//: other exists.
registerCatalog(BASIC_CATALOG_ID, { ...LAYOUT_WIDGETS, "@functions": callBasicFunction });
