// Shared compression control for routines and conversations; settings apply at next boot.
import { api } from "/static/api.js";
import { el, toast, toastError } from "/static/util.js";

export function outputCompression(value, endpoint) {
  let saved = value || "compress";
  const select = el("select", { "aria-label": "Output compression", "data-output-compression": "" },
    el("option", { value: "off" }, "Off"),
    el("option", { value: "measure" }, "Measure only"),
    el("option", { value: "compress" }, "Compress (experimental)"));
  select.value = saved;
  select.onchange = async () => {
    select.disabled = true;
    try {
      await api(endpoint, { method: "PATCH", body: { output_compression: select.value } });
      saved = select.value;
      toast("Output compression saved — applies from the next run or reply");
    } catch (err) {
      select.value = saved;
      toastError(err);
    } finally { select.disabled = false; }
  };
  return el("div", { class: "mt" }, el("label", {}, "Output compression ", select),
    el("div", { class: "muted small mt" },
      "Off leaves every command's output exactly as captured. Measure only records the comparison "
      + "for you while leaving the model's input unchanged. On minifies large JSON (whitespace only — "
      + "nothing is removed, and it needs no optional package) and, with the optional Headroom extra "
      + "installed, replaces recognisable logs with a labelled excerpt. Originals stay readable either "
      + "way, and measurements and fallback reasons appear with each command's output."));
}
