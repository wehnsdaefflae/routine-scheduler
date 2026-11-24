// The A2UI RENDERER CORE — D310 step 3, the layer that turns a surface into DOM.
//
// `surface.js` owns the protocol's state and knows no component type; the catalogs say what a
// component IS; this module is the one that walks the adjacency list and puts nodes on the page.
// It deliberately draws NOTHING itself: every component type is a function in a REGISTRY, and
// the registries live in `widgets-*.js` beside this file, one per family. That is the seam that
// keeps an 18-component catalog from becoming an 18-component file, and it is also what makes
// "mixable catalogs" real rather than aspirational — a catalog is a registry, resolution is by
// `@catalogId`, and the scheduler's own components register exactly like the basic ones.
//
// WHAT THIS LAYER OWNS, and nothing else:
//   - RESOLUTION: which catalog a component belongs to (`@catalogId`, else the surface default,
//     else a refusal — the spec forbids falling back to "the only catalog we have");
//   - the WALK: children in order, with each child's collection SCOPE, and the `root` id as the
//     child of the implicit Surface container;
//   - the PROPERTY READER each widget is handed, so a widget never touches the data model: it
//     asks for a property by name and gets the resolved value, in its own scope;
//   - ACTIONS: an agent event dispatched outward, a local function call run with user activation;
//   - `@checks` evaluation and the one place a validation message is drawn, so every input looks
//     the same when it is wrong;
//   - ACCESSIBILITY: `@accessibility` applied as ARIA on whatever node the widget returned;
//   - REPAINT: one subscription to the store, and a surface redrawn when its own id changes.
//
// WHY A FULL REPAINT PER MESSAGE. The adjacency list is small (a decision surface is tens of
// components, not thousands) and the protocol streams corrections as freely as additions, so a
// diffing renderer would spend its complexity on a problem this console does not have. The
// focus-preserving exception is where it matters and is handled explicitly: an input that holds
// the caret keeps it across a repaint, because two-way binding writes on every keystroke.

import { evaluateChecks } from "./functions.js";
import { asBool, asText, resolveDynamic } from "./surface.js";

//: The Basic Catalog's own id, as the spec publishes it. A component naming no catalog on a
//: surface whose default is this one resolves here.
export const BASIC_CATALOG_ID =
  "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json";

//: This console's extension catalog — the components A2UI has no equivalent for. Its id is this
//: instance's own, never a2ui.org's: a catalog id is an identifier, and claiming the protocol's
//: namespace for components it never declared is how two renderers come to disagree.
export const SCHEDULER_CATALOG_ID =
  "https://routine-scheduler.local/a2ui/v1_0/catalogs/scheduler/catalog.json";

//: Thrown when a component cannot be drawn. The spec names the codes; the renderer reports them
//: in place of the component rather than failing the whole surface, which is what progressive
//: rendering requires — half a streamed surface is the normal case, not an error.
export class A2uiRenderError extends Error {
  constructor(message, code = "RENDER_ERROR") {
    super(message);
    this.name = "A2uiRenderError";
    this.code = code;
  }
}

//: One registry per catalog: `catalogId` → { componentName → widget }. A widget is
//: `(node, api) => Element`, and `api` is the only thing it may use to reach the surface.
const REGISTRIES = new Map();

//: Register (or extend) a catalog's widgets. Called once per widget module at import time, so a
//: catalog this renderer supports is exactly a catalog some module registered — there is no list
//: to keep in step.
export function registerCatalog(catalogId, widgets) {
  const existing = REGISTRIES.get(catalogId) ?? {};
  REGISTRIES.set(catalogId, { ...existing, ...widgets });
}

//: Every catalog id this renderer can draw — the `supportedCatalogIds` of its capabilities.
export function supportedCatalogIds() {
  return [...REGISTRIES.keys()];
}

//: The catalog a component resolves against: its own `@catalogId`, else the surface's default.
//: NO fallback to "the only catalog registered", which the spec forbids in those words — a
//: component that resolves by luck on a single-catalog renderer breaks the moment a second
//: catalog is added, and that is precisely the step this campaign is taking.
function resolveCatalog(component, surface) {
  const id = component["@catalogId"] || surface.catalogId;
  if (!id) {
    throw new A2uiRenderError(
      `component "${component.id}" names no @catalogId and its surface has no default`,
      "CATALOG_ERROR");
  }
  const registry = REGISTRIES.get(id);
  if (!registry) throw new A2uiRenderError(`no catalog registered for "${id}"`, "CATALOG_ERROR");
  return registry;
}

//: An element carrying the renderer's own error, in place of the component that could not be
//: drawn. Visible on purpose: a surface silently missing a component is the failure mode that
//: makes a protocol impossible to debug from the outside.
function errorNode(code, message) {
  const node = document.createElement("div");
  node.className = "a2-error";
  node.dataset.code = code;
  node.textContent = message;
  return node;
}

//: `@accessibility` applied to whatever the widget returned. The spec puts the burden on the
//: renderer, and a catalog carries the INTENT (a label, a role, a description) rather than ARIA
//: attribute names, so the mapping belongs here — once, for every component of every catalog.
function applyAccessibility(element, component, api) {
  const a11y = component["@accessibility"];
  if (!a11y || typeof a11y !== "object") return;
  const label = api.resolve(a11y.label);
  if (label !== undefined && label !== null && label !== "") {
    element.setAttribute("aria-label", asText(label));
  }
  if (a11y.role) element.setAttribute("role", String(a11y.role));
  const described = api.resolve(a11y.description);
  if (described) element.setAttribute("aria-description", asText(described));
  if (a11y.hidden !== undefined && asBool(api.resolve(a11y.hidden))) {
    element.setAttribute("aria-hidden", "true");
  }
}

//: `weight` is the one property the CATALOG defines but no widget owns: it is only meaningful on
//: a direct child of a Row or Column, and it means flex-grow. Applied by the parent's walk, so a
//: widget never has to know whether its parent is a flex container.
function applyWeight(element, component) {
  const weight = component.weight;
  if (typeof weight === "number" && Number.isFinite(weight)) {
    element.style.flexGrow = String(weight);
    element.style.flexBasis = "0";
  }
}

//: ONE rendered surface: the store, the surface id, the root element, and the outward channel an
//: agent event travels on. Everything a widget may do goes through the `api` this builds.
export class SurfaceRenderer {
  constructor(store, surfaceId, { onEvent = null, onError = null } = {}) {
    this.store = store;
    this.surfaceId = String(surfaceId);
    this.onEvent = onEvent;
    this.onError = onError;
    this.element = document.createElement("div");
    this.element.className = "a2-surface";
    this.element.dataset.surface = this.surfaceId;
    this.unsubscribe = store.subscribe((id) => {
      if (String(id) === this.surfaceId) this.paint();
    });
  }

  //: The surface being drawn, or undefined once it has been deleted.
  get surface() {
    return this.store.get(this.surfaceId);
  }

  //: Stop listening. A view that drops this renderer without calling it leaks a listener per
  //: surface, which is the kind of thing that only shows up after an hour in one tab.
  destroy() {
    this.unsubscribe();
    this.element.remove();
  }

  //: Redraw the whole surface. Focus and caret survive it: two-way binding writes on every
  //: keystroke, so a repaint lands in the middle of typing as a matter of course.
  paint() {
    const surface = this.surface;
    const active = document.activeElement;
    // The id is on the node the WIDGET returned, which for every input of this catalog is the
    // `<label>` wrapper holding the control and its validation message — not the control itself.
    // So the key is the nearest ANCESTOR carrying one: reading `active.dataset.a2id` finds
    // nothing on an `<input>` and silently never restores focus, which makes a bound text field
    // take one keystroke at a time (measured 2026-11-24, caught by the caret test).
    const owner = active && this.element.contains(active)
      ? active.closest("[data-a2id]") : null;
    const focusKey = owner ? owner.dataset.a2id : null;
    const caret = focusKey && "selectionStart" in active ? active.selectionStart : null;
    this.element.textContent = "";
    if (!surface) return;
    const root = surface.root;
    if (!root) return;                      // streamed but not yet complete: draw nothing, wait
    this.element.append(this.renderComponent(root.id, ""));
    if (focusKey) {
      const again = this.element.querySelector(`[data-a2id="${CSS.escape(focusKey)}"]`);
      // And the focus goes back to the CONTROL, not to the wrapper that carries the id: a
      // `<label>` is not focusable, so focusing it would leave the caret nowhere.
      const control = again?.matches("input, textarea, select, button")
        ? again : again?.querySelector("input, textarea, select, button");
      if (control) {
        control.focus();
        if (caret !== null && "setSelectionRange" in control) {
          control.setSelectionRange(caret, caret);
        }
      }
    }
  }

  //: One component, in one scope, as an element — its widget's work, wrapped in everything this
  //: layer owns: the id attribute a repaint restores focus by, weight, accessibility, and the
  //: error node that replaces a component which cannot be drawn.
  renderComponent(id, scope) {
    const surface = this.surface;
    const component = surface?.components.get(String(id));
    if (!component) return errorNode("MISSING_COMPONENT", `no component "${id}"`);
    try {
      const registry = resolveCatalog(component, surface);
      const widget = registry[component.component];
      if (!widget) {
        throw new A2uiRenderError(
          `catalog has no component "${component.component}"`, "UNKNOWN_COMPONENT");
      }
      const api = this.api(component, scope);
      const element = widget(component, api);
      element.dataset.a2id = `${component.id}@${scope}`;
      element.classList.add("a2-node");
      applyWeight(element, component);
      applyAccessibility(element, component, api);
      return element;
    } catch (error) {
      const code = error.code ?? "RENDER_ERROR";
      if (this.onError) this.onError({ component: component.id, code, message: error.message });
      return errorNode(code, `${component.component ?? "component"}: ${error.message}`);
    }
  }

  //: The ONLY surface a widget sees. Everything it needs — properties resolved in its scope, its
  //: children drawn, a value written back, its checks evaluated, its action dispatched — and
  //: nothing it does not: no store, no data model, no sibling.
  api(component, scope) {
    const surface = this.surface;
    const call = (name, args, { activated = false } = {}) => {
      const registry = REGISTRIES.get(component["@catalogId"] || surface.catalogId) ?? {};
      const fn = registry["@functions"];
      if (!fn) throw new A2uiRenderError("this catalog declares no functions", "CATALOG_ERROR");
      return fn(name, args, { model: surface.dataModel, scope, activated });
    };
    const resolve = (value) => resolveDynamic(value, {
      model: surface.dataModel, scope, callFunction: (n, a) => call(n, a),
    });
    return {
      scope,
      //: One of this component's own properties, resolved in its scope.
      prop: (name) => resolve(component[name]),
      //: A `Dynamic*` value from anywhere in the component, resolved the same way. Used for
      //: nested shapes a property reader cannot reach, such as a Tab's label or an option's.
      resolve,
      text: (name) => asText(resolve(component[name])),
      bool: (name) => asBool(resolve(component[name])),
      //: This component's `children` ChildList, drawn — each child in the scope the list gave
      //: it, which is what makes a relative binding inside a template mean the item it iterates.
      children: () => surface.childrenOf(component.id, scope)
        .map((kid) => this.renderComponent(kid.id, kid.scope)),
      //: ONE child named by a `Child` property (`Card.child`, `Modal.trigger`), drawn.
      child: (property) => (component[property] === undefined
        ? null
        : this.renderComponent(String(component[property]), scope)),
      //: ONE child named by its ID rather than by a property — the shape `Tabs` uses, where
      //: each tab object carries its own `child`. Same scope, same error handling.
      drawChild: (id) => this.renderComponent(String(id), scope),
      //: Write a user's input back into the model. LOCAL, as the spec requires: a keystroke
      //: sends nothing to the agent — the value travels only when an action carries it.
      write: (pointer, value) => {
        surface.write(pointer, value, scope);
        this.paint();
      },
      //: The path a `Dynamic*` property is BOUND to, so an input knows where to write. A literal
      //: property has none, and an input over a literal is read-only rather than broken.
      pathOf: (name) => {
        const raw = component[name];
        return raw && typeof raw === "object" && raw["@path"] ? String(raw["@path"]) : null;
      },
      //: This component's `@checks`, as one answer: the first failing rule.
      checks: () => evaluateChecks(component["@checks"], (condition) => resolve(condition)),
      //: What a user's gesture does: an agent event sent outward, or a local function run WITH
      //: user activation (which is what lets `openUrl` run at all).
      act: () => {
        const action = component.action;
        if (!action || typeof action !== "object") return;
        if (action.functionCall) {
          call(action.functionCall["@call"], Object.fromEntries(
            Object.entries(action.functionCall.args ?? {}).map(([k, v]) => [k, resolve(v)])),
          { activated: true });
          return;
        }
        const event = action.event;
        if (!event || !this.onEvent) return;
        this.onEvent({
          surfaceId: this.surfaceId,
          name: event.name,
          userMessage: event.userMessage === undefined ? undefined : asText(resolve(event.userMessage)),
          context: Object.fromEntries(
            Object.entries(event.context ?? {}).map(([k, v]) => [k, resolve(v)])),
        });
      },
      //: The validation message element every input shows when it is invalid — here, so a wrong
      //: field looks the same whichever catalog drew it.
      checkMessage: () => {
        const result = evaluateChecks(component["@checks"], (c) => resolve(c));
        if (result.valid) return null;
        const node = document.createElement("div");
        node.className = "a2-invalid";
        node.textContent = result.message;
        return node;
      },
      //: A node with a class, for a widget that needs one without importing a helper.
      el: (tag, className = "") => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        return node;
      },
    };
  }
}

//: Mount a surface into `host` and return its renderer. The one call a view makes.
export function mountSurface(host, store, surfaceId, options = {}) {
  const renderer = new SurfaceRenderer(store, surfaceId, options);
  host.append(renderer.element);
  renderer.paint();
  return renderer;
}
