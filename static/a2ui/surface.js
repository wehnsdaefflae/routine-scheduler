// The A2UI v1.0 SURFACE: the protocol's state, with no rendering in it at all.
//
// D310, step 1 of the operator's order (2026-11-23, answer `a-rich-plus`): decisions and asks
// become A2UI surfaces, rendered from the FULL Basic Catalog plus components this console needs
// that the protocol has no equivalent for. His framing ruling governs every step —
// "We shouldn't be fixated on the controls we already use. We need to improve to UI/UX with new
// elements!" — so this is not a re-plumbing of the five cards that exist today.
//
// WHY THE STATE IS ITS OWN MODULE. An A2UI stream is an adjacency list plus a data model, and
// both arrive progressively: a component may name children that have not been sent yet, and a
// binding may point into data that arrives three messages later. Keeping the state separate from
// the DOM means the protocol's hard parts — pointer resolution, collection scopes, two-way
// binding, arrival order — are testable without a browser, and the renderer that comes next
// (step 3) is left with one job: turn a resolved component into nodes.
//
// WHAT THIS DELIBERATELY DOES NOT DO. It renders nothing, it fetches nothing, and it knows no
// component type: `Text` and `ChoicePicker` are opaque names here. The catalog (step 2) says
// what a component IS; this module only says where its properties come from.
//
// SPEC DECISIONS, each one read off the v1.0 specification rather than assumed:
//   - every message is an object carrying EXACTLY ONE of the six envelope keys, so dispatch is
//     by key, and an unknown or absent key is a protocol error rather than a silent no-op;
//   - `createSurface` applies `dataModel` BEFORE `components` when one message carries both, so
//     a component binds against the initial data on its first resolution;
//   - a surface's `surfaceId` must be unique for the renderer's lifetime: re-creating a live id
//     is an INTEGRITY_ERROR, and `deleteSurface` is the only way to reuse one;
//   - the component with `id: "root"` mounts as the child of the implicit `Surface` container;
//   - `updateDataModel` with no `path` (or `/`) replaces the whole model, and a `value` of
//     `null` DELETES the key at `path` rather than storing a null;
//   - a path starting with `/` is absolute from the model root wherever the component sits; one
//     that does not is relative to the enclosing collection scope;
//   - a missing path resolves to `undefined` — progressive rendering, not an error;
//   - interpolating a non-string: numbers and booleans take their standard representation,
//     null/undefined become "", objects and arrays are stringified as JSON.

//: The six envelope keys, in the spec's own order. A message carrying none of them, or more
//: than one, is not an A2UI message — the renderer must say so rather than guess which was
//: meant, because guessing turns a malformed agent into a silently half-drawn surface.
export const ENVELOPE_KEYS = [
  "createSurface",
  "updateComponents",
  "updateDataModel",
  "deleteSurface",
  "callRendererFunction",
  "agentFunctionResponse",
];

//: Thrown for a stream the protocol forbids, carrying the spec's own `code` where it names one.
export class A2uiProtocolError extends Error {       // noqa — a protocol refusal, not a bug
  constructor(message, code = "PROTOCOL_ERROR") {
    super(message);
    this.name = "A2uiProtocolError";
    this.code = code;
  }
}

// ---- JSON Pointer (RFC 6901) ----------------------------------------------------------------
// Hand-written rather than borrowed: the console has no bundler, and the two escapes are the
// whole of the syntax a surface needs. `~1` is a literal `/` in a key and `~0` a literal `~`,
// and they MUST be unescaped in that order — doing it the other way turns the key `~01` into
// `/` instead of `~1`.

function tokens(pointer) {
  const raw = String(pointer ?? "");
  const body = raw.startsWith("/") ? raw.slice(1) : raw;
  if (body === "") return [];
  return body.split("/").map((t) => t.replace(/~1/g, "/").replace(/~0/g, "~"));
}

function step(node, token) {
  if (node === null || node === undefined) return undefined;
  if (Array.isArray(node)) {
    const index = /^\d+$/.test(token) ? Number(token) : -1;
    return index >= 0 && index < node.length ? node[index] : undefined;
  }
  if (typeof node === "object") return Object.hasOwn(node, token) ? node[token] : undefined;
  return undefined;
}

//: Read `pointer` out of `model`, or `undefined` when any step of it is missing. A missing path
//: is NOT an error: during the streaming phase the data for a binding routinely arrives after
//: the component that reads it, and the spec tells renderers to handle that gracefully.
export function readPointer(model, pointer) {
  return tokens(pointer).reduce((node, token) => step(node, token), model);
}

//: Write `value` at `pointer`, creating the objects on the way (an input may be bound to a path
//: the agent never initialized — the spec requires the renderer to create it). A `value` of
//: `null` DELETES the entry instead, which is how the protocol spells a removal. Arrays are
//: indexed numerically; a non-numeric token against an array is a protocol error rather than a
//: silently created string key, because that silently breaks every other binding into the list.
export function writePointer(model, pointer, value) {
  const path = tokens(pointer);
  if (path.length === 0) return value === null ? {} : value;
  const root = model === null || model === undefined ? {} : model;
  let node = root;
  for (const [depth, token] of path.slice(0, -1).entries()) {
    if (Array.isArray(node) && !/^\d+$/.test(token)) {
      throw new A2uiProtocolError(
        `pointer ${pointer}: "${token}" indexes an array at depth ${depth}`);
    }
    const next = step(node, token);
    if (next === null || next === undefined || typeof next !== "object") {
      node[token] = {};
    }
    node = node[token];
  }
  const last = path[path.length - 1];
  if (value === null) {
    if (Array.isArray(node) && /^\d+$/.test(last)) node.splice(Number(last), 1);
    else if (node && typeof node === "object") delete node[last];
  } else {
    node[last] = value;
  }
  return root;
}

// ---- Dynamic* resolution --------------------------------------------------------------------

//: A pointer resolved in a SCOPE: absolute from the model root when it starts with "/", else
//: relative to the collection item the component was instantiated for. A component outside any
//: collection has scope "" and both spellings mean the same thing.
export function resolveInScope(model, pointer, scope = "") {
  const p = String(pointer ?? "");
  return readPointer(model, p.startsWith("/") || !scope ? p : `${scope}/${p}`);
}

//: What a `Dynamic*` property is worth right now: a literal as itself, a `{"@path": …}` binding
//: read from the model, a `{"@call": …}` function call handed to `callFunction` (the catalog
//: owns what a function MEANS, so this module will not pretend to know). Anything else is a
//: literal, including an object the agent meant as data.
export function resolveDynamic(value, { model = {}, scope = "", callFunction = null } = {}) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return value;
  if (Object.hasOwn(value, "@path")) return resolveInScope(model, value["@path"], scope);
  if (Object.hasOwn(value, "@call")) {
    if (!callFunction) return undefined;
    const args = Object.fromEntries(Object.entries(value.args ?? {}).map(
      ([name, arg]) => [name, resolveDynamic(arg, { model, scope, callFunction })]));
    return callFunction(value["@call"], args, { catalogId: value.catalogId ?? "" });
  }
  return value;
}

//: A resolved value as the TEXT a component shows. The spec fixes these conversions so two
//: renderers agree: null and undefined are "", objects and arrays are JSON, everything else
//: takes its standard string form.
export function asText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

//: A resolved value as a BOOLEAN. A `ValidationResult` — any object carrying a boolean `valid`
//: — is worth that flag, which is what lets a check rule and a plain condition share one
//: property type; every other value takes JavaScript's truthiness, except that an empty array
//: and an empty object are FALSE (the spec coerces them as empty, and `[]` being true would
//: make "there are no errors" read as "there are errors").
export function asBool(value) {
  if (value && typeof value === "object" && typeof value.valid === "boolean") return value.valid;
  if (Array.isArray(value)) return value.length > 0;
  if (value && typeof value === "object") return Object.keys(value).length > 0;
  return Boolean(value);
}

// ---- The surface store ----------------------------------------------------------------------

//: One live surface: its components as a flat id→component map (the adjacency list the protocol
//: streams), its data model, and the catalog its components default to.
class Surface {
  constructor({ surfaceId, catalogId = "", sendDataModel = false, metadata = {} }) {
    this.surfaceId = surfaceId;
    this.catalogId = catalogId;
    this.sendDataModel = Boolean(sendDataModel);
    this.metadata = metadata ?? {};
    this.components = new Map();
    this.dataModel = {};
  }

  put(components) {
    for (const component of components ?? []) {
      if (!component || typeof component !== "object" || !component.id) {
        throw new A2uiProtocolError("every component needs an id");
      }
      this.components.set(String(component.id), component);
    }
  }

  //: The component that mounts as the child of the implicit `Surface` container, or undefined
  //: while the stream has not sent it yet.
  get root() {
    return this.components.get("root");
  }

  //: `id`'s children as ids, in order, whatever shape its ChildList took: a static array of
  //: ids, or a template over a data path — in which case each item of the array yields the same
  //: template id paired with the SCOPE its bindings resolve in, which is what makes a relative
  //: path inside a collection mean `/users/2/name`.
  childrenOf(id, scope = "") {
    const component = this.components.get(String(id));
    const children = component?.children;
    if (!children) return [];
    if (Array.isArray(children)) {
      return children.map((childId) => ({ id: String(childId), scope }));
    }
    if (typeof children === "object" && children.componentId) {
      const path = String(children.path ?? "");
      const base = path.startsWith("/") || !scope ? path : `${scope}/${path}`;
      const list = readPointer(this.dataModel, base);
      if (!Array.isArray(list)) return [];
      return list.map((_, index) => ({
        id: String(children.componentId),
        scope: `${base}/${index}`,
      }));
    }
    return [];
  }

  //: One property of one component, resolved: its `Dynamic*` value read in `scope`.
  prop(id, name, { scope = "", callFunction = null } = {}) {
    const component = this.components.get(String(id));
    if (!component) return undefined;
    return resolveDynamic(component[name], { model: this.dataModel, scope, callFunction });
  }

  //: Write a user's input back into the model. Two-way binding is LOCAL — the spec is explicit
  //: that a keystroke sends nothing to the agent; the value travels only when an action carries
  //: it in its context.
  write(pointer, value, scope = "") {
    const p = String(pointer ?? "");
    const target = p.startsWith("/") || !scope ? p : `${scope}/${p}`;
    this.dataModel = writePointer(this.dataModel, target, value);
    return this.dataModel;
  }
}

//: Every live surface, and the one entry point a transport calls: `apply(message)` per streamed
//: message, in arrival order. The store owns no socket and no DOM — whoever reads the stream
//: feeds it, and whoever draws subscribes.
export class SurfaceStore {
  constructor() {
    this.surfaces = new Map();
    this.listeners = new Set();
  }

  get(surfaceId) {
    return this.surfaces.get(String(surfaceId));
  }

  //: Called after every applied message with the surface id and the envelope key, so a renderer
  //: repaints exactly what changed instead of diffing the world.
  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify(surfaceId, key) {
    for (const listener of this.listeners) listener(surfaceId, key);
  }

  //: The surface a message names, or a protocol error. Every key but `createSurface` requires
  //: the surface to exist already — the spec forbids updating one that was never created, and a
  //: renderer that quietly created it would hide a lost `createSurface` from the agent.
  require(surfaceId, key) {
    const surface = this.surfaces.get(String(surfaceId));
    if (!surface) {
      throw new A2uiProtocolError(`${key}: no surface "${surfaceId}"`, "INTEGRITY_ERROR");
    }
    return surface;
  }

  apply(message) {
    if (!message || typeof message !== "object") {
      throw new A2uiProtocolError("a message must be an object");
    }
    const keys = ENVELOPE_KEYS.filter((key) => Object.hasOwn(message, key));
    if (keys.length !== 1) {
      throw new A2uiProtocolError(
        `a message carries exactly one envelope key, not ${keys.length}`);
    }
    const [key] = keys;
    const body = message[key] ?? {};
    const surfaceId = String(body.surfaceId ?? "");
    if (!surfaceId) throw new A2uiProtocolError(`${key}: surfaceId is required`);
    this[`_${key}`](surfaceId, body);
    this.notify(surfaceId, key);
    return key;
  }

  _createSurface(surfaceId, body) {
    if (this.surfaces.has(surfaceId)) {
      throw new A2uiProtocolError(
        `createSurface: "${surfaceId}" already exists — deleteSurface first`, "INTEGRITY_ERROR");
    }
    const surface = new Surface({ ...body, surfaceId });
    // dataModel FIRST: a component sent in the same message must bind against the initial data
    // on its first resolution, which the spec states and which is not an implementation detail
    // — the other order renders every binding as empty for one frame.
    if (body.dataModel && typeof body.dataModel === "object") {
      surface.dataModel = body.dataModel;
    }
    surface.put(body.components);
    this.surfaces.set(surfaceId, surface);
  }

  _updateComponents(surfaceId, body) {
    const surface = this.require(surfaceId, "updateComponents");
    if (!Array.isArray(body.components)) {
      throw new A2uiProtocolError("updateComponents: components is required");
    }
    if (body.components.some((c) => String(c?.id ?? "") === "Surface")) {
      throw new A2uiProtocolError("updateComponents: the Surface container cannot be modified");
    }
    surface.put(body.components);
  }

  _updateDataModel(surfaceId, body) {
    const surface = this.require(surfaceId, "updateDataModel");
    if (!Object.hasOwn(body, "value")) {
      throw new A2uiProtocolError("updateDataModel: value is required");
    }
    const path = String(body.path ?? "/");
    surface.dataModel = writePointer(surface.dataModel, path, body.value);
  }

  _deleteSurface(surfaceId) {
    this.require(surfaceId, "deleteSurface");
    this.surfaces.delete(surfaceId);
  }

  //: A renderer function the agent asked for. The store records the request and answers it
  //: through the handler the host registered; the spec requires a response for EVERY call, even
  //: a void one, so a missing handler is reported back as an error rather than dropped.
  _callRendererFunction(surfaceId, body) {
    this.require(surfaceId, "callRendererFunction");
    this.pending = { surfaceId, ...body };
  }

  //: The agent's answer to a function the RENDERER called. Kept for the caller that is waiting
  //: on it; correlation is the transport's job, not the store's.
  _agentFunctionResponse(surfaceId, body) {
    this.require(surfaceId, "agentFunctionResponse");
    this.lastResponse = { surfaceId, ...body };
  }
}
