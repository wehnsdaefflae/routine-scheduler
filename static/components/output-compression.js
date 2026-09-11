// Shared compression control for routines and conversations; settings apply at next boot.
import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";

export function outputCompression(value, endpoint) {
  let saved = value || "headroom";
  const select = el("select", { "aria-label": "Output compression", "data-output-compression": "" },
    el("option", { value: "off" }, "Off"),
    el("option", { value: "measure" }, "Measure only"),
    el("option", { value: "headroom" }, "Headroom (experimental)"));
  select.value = saved;
  select.onchange = async () => {
    select.disabled = true;
    try {
      await api(endpoint, { method: "PATCH", body: { output_compression: select.value } });
      saved = select.value;
      toast("Output compression saved — applies from the next run or reply");
    } catch (err) {
      select.value = saved;
      toast(err.message, 4000, { error: true });
    } finally { select.disabled = false; }
  };
  return el("div", { class: "mt" }, el("label", {}, "Output compression ", select),
    el("div", { class: "muted small mt" },
      "Optional Headroom installation required for measurement or compression. Measure only leaves "
      + "the model's input unchanged. Headroom compacts large JSON and logs; originals stay readable. "
      + "Measurements and fallback reasons appear with each command's output."));
}
