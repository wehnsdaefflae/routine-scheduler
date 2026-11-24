// The Basic Catalog's INTERACTIVE components — D310 step 3, the other six of the eighteen.
//
// Button · TextField · CheckBox · ChoicePicker · Slider · DateTimeInput
//
// These are the components the catalog marks `Checkable`, and they are a module of their own
// because they are where three things happen that happen nowhere else: a user's input is WRITTEN
// BACK into the data model, `@checks` are evaluated and shown, and a gesture carries USER
// ACTIVATION (which is the only state in which `openUrl` may run). The twelve display and layout
// components do none of those, so they are read and changed separately.
//
// FIVE OF THESE SIX ARE NEW INTERACTION ELEMENTS FOR THIS CONSOLE, which is the operator's point
// in choosing `a-rich-plus`: today's decision cards offer buttons and nothing else. A ChoicePicker
// with chips, a Slider, a date-and-time field and a labelled checkbox are element types the
// Decisions page cannot currently express at all.
//
// TWO-WAY BINDING IS LOCAL, and that is the spec's own rule rather than an optimisation: a
// keystroke writes the model and sends NOTHING to the agent. The value reaches the agent only
// when an action carries it in its context. So every input here writes through `api.write` and
// no input dispatches anything.
//
// AN INPUT OVER A LITERAL IS READ-ONLY. `api.pathOf` answers where to write, and a property the
// agent wrote as a literal has no path — so the field shows the value and refuses to edit it,
// rather than accepting keystrokes that vanish on the next repaint.

import { registerCatalog, BASIC_CATALOG_ID } from "./render.js";
import { callBasicFunction } from "./functions.js";
import { asText } from "./surface.js";

//: A labelled field: the label element, the control, and the validation message, in the order a
//: screen reader reads them. Returned as one node so every input of this catalog has the same
//: shape — which is what makes a wrong field look the same whichever component drew it.
function field(api, control, { label = "" } = {}) {
  const node = api.el("label", "a2-field");
  if (label) {
    const caption = api.el("span", "a2-label");
    caption.textContent = label;
    node.append(caption);
  }
  node.append(control);
  const invalid = api.checkMessage();
  if (invalid) {
    node.append(invalid);
    control.setAttribute("aria-invalid", "true");
  }
  return node;
}

//: The HTML input type a TextField variant means. `obscured` is a password field, `longText` is
//: a textarea (handled separately), and anything the catalog does not declare falls to text.
const TEXT_TYPES = { shortText: "text", number: "number", obscured: "password" };

export const INPUT_WIDGETS = {
  //: A button around ONE child, so its label can be any component — a Text, a Row of an Icon and
  //: a Text. `variant` is the catalog's own three, as classes over this console's tokens.
  Button: (component, api) => {
    const node = api.el("button", `a2-button a2-button-${component.variant ?? "default"}`);
    node.type = "button";
    const child = api.child("child");
    if (child) node.append(child);
    // The gesture IS the user activation: this is the one path on which `openUrl` may run, and
    // the core's `api.act` is what carries that flag into the function call.
    node.addEventListener("click", () => api.act());
    return node;
  },

  //: A text field, in its four variants. Writes on every keystroke, because `@checks` are
  //: evaluated against the model and a field that validated only on blur would show a stale
  //: verdict — and the core preserves focus and caret across the repaint that follows.
  TextField: (component, api) => {
    const path = api.pathOf("value");
    const long = component.variant === "longText";
    const control = api.el(long ? "textarea" : "input", "a2-input");
    if (!long) control.type = TEXT_TYPES[component.variant] ?? "text";
    control.value = api.text("value");
    const placeholder = api.text("placeholder");
    if (placeholder) control.placeholder = placeholder;
    if (!path) control.readOnly = true;
    else control.addEventListener("input", () => api.write(path, control.value));
    return field(api, control, { label: api.text("label") });
  },

  //: A checkbox with its own label. `DynamicBoolean` coercion is what reads the value, so a
  //: check rule bound to the same path works without a second property type.
  CheckBox: (component, api) => {
    const path = api.pathOf("value");
    const control = api.el("input", "a2-checkbox");
    control.type = "checkbox";
    control.checked = api.bool("value");
    if (!path) control.disabled = true;
    else control.addEventListener("change", () => api.write(path, control.checked));
    return field(api, control, { label: api.text("label") });
  },

  //: A CHOICE PICKER — one or many, as checkboxes or as chips, optionally filterable. The
  //: catalog's `value` is a DynamicStringList even in the single-selection case, so the model
  //: always holds an array and an agent reads one shape whichever variant it asked for.
  ChoicePicker: (component, api) => {
    const path = api.pathOf("value");
    const multiple = component.variant !== "mutuallyExclusive";
    const chips = component.displayStyle === "chips";
    const current = new Set(
      [api.prop("value")].flat().filter((v) => v !== undefined && v !== null).map(String));
    const node = api.el("div", `a2-choices ${chips ? "a2-chips" : "a2-checks"}`);
    node.setAttribute("role", multiple ? "group" : "radiogroup");

    const options = (Array.isArray(component.options) ? component.options : [])
      .map((option) => ({
        value: String(option.value ?? ""),
        label: asText(api.resolve(option.label ?? option.value)),
      }));

    const paint = (filter = "") => {
      node.textContent = "";
      for (const option of options) {
        if (filter && !option.label.toLowerCase().includes(filter.toLowerCase())) continue;
        const on = current.has(option.value);
        const control = api.el(chips ? "button" : "input", chips ? "a2-chip" : "a2-checkbox");
        if (chips) {
          control.type = "button";
          control.textContent = option.label;
          control.setAttribute("aria-pressed", on ? "true" : "false");
          control.classList.toggle("a2-chip-on", on);
        } else {
          control.type = multiple ? "checkbox" : "radio";
          control.name = `a2-${component.id}`;
          control.checked = on;
        }
        const choose = () => {
          if (!path) return;
          if (!multiple) current.clear();
          if (current.has(option.value)) current.delete(option.value);
          else current.add(option.value);
          api.write(path, [...current]);
        };
        control.addEventListener(chips ? "click" : "change", choose);
        if (chips) node.append(control);
        else {
          const wrap = api.el("label", "a2-choice");
          const caption = api.el("span", "a2-label");
          caption.textContent = option.label;
          wrap.append(control, caption);
          node.append(wrap);
        }
      }
    };

    const outer = api.el("div", "a2-choicepicker");
    const label = api.text("label");
    if (label) {
      const caption = api.el("div", "a2-label");
      caption.textContent = label;
      outer.append(caption);
    }
    if (component.filterable) {
      const search = api.el("input", "a2-input a2-filter");
      search.type = "search";
      search.placeholder = "Filter";
      // The filter is renderer-local and deliberately NOT written to the model: it is a way of
      // looking at the options, not an answer, and writing it would make every keystroke part of
      // the decision the agent is waiting on.
      search.addEventListener("input", () => paint(search.value));
      outer.append(search);
    }
    paint();
    outer.append(node);
    const invalid = api.checkMessage();
    if (invalid) outer.append(invalid);
    return outer;
  },

  //: A SLIDER over a numeric range, with its value shown — a range input alone tells a user
  //: nothing about what it is set to, and the catalog gives no separate place to show it.
  Slider: (component, api) => {
    const path = api.pathOf("value");
    const control = api.el("input", "a2-slider");
    control.type = "range";
    control.min = String(component.min ?? 0);
    control.max = String(component.max ?? 100);
    if (component.steps) {
      const span = Number(control.max) - Number(control.min);
      control.step = String(span / Number(component.steps));
    }
    const value = api.prop("value");
    control.value = String(value ?? control.min);
    const readout = api.el("output", "a2-readout");
    readout.textContent = control.value;
    if (!path) control.disabled = true;
    else {
      control.addEventListener("input", () => {
        readout.textContent = control.value;
        api.write(path, Number(control.value));
      });
    }
    const row = api.el("div", "a2-sliderrow");
    row.append(control, readout);
    return field(api, row, { label: api.text("label") });
  },

  //: A DATE AND TIME input. The catalog's two flags choose which of the three HTML types this
  //: is, and the value is kept as the ISO-ish string the control itself produces — the format
  //: an agent gets back is therefore the format the browser guarantees, not one parsed by hand.
  DateTimeInput: (component, api) => {
    const path = api.pathOf("value");
    const date = component.enableDate !== false;
    const time = component.enableTime === true;
    const control = api.el("input", "a2-datetime");
    control.type = date && time ? "datetime-local" : (time ? "time" : "date");
    control.value = api.text("value");
    if (component.min) control.min = String(component.min);
    if (component.max) control.max = String(component.max);
    if (!path) control.readOnly = true;
    else control.addEventListener("change", () => api.write(path, control.value));
    return field(api, control, { label: api.text("label") });
  },
};

//: The interactive half of the Basic Catalog. `registerCatalog` merges, so this module and
//: `widgets-layout.js` each register their own half and neither knows the other exists.
registerCatalog(BASIC_CATALOG_ID, { ...INPUT_WIDGETS, "@functions": callBasicFunction });
