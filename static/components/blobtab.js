// Opening a fetched file in a NEW TAB without handing it this console's origin.
//
// Everything the console shows from a run's files is fetched with the bearer token and handed to
// the browser as a blob: URL (api.js apiBlobUrl — an <iframe src> or a link cannot carry the
// header). A blob URL carries the ORIGIN OF THE PAGE THAT MADE IT, so a page a routine wrote,
// opened top-level from one, runs as this console: localStorage with the operator token in it,
// and every API route, are the page's to use. The artifact panel frames an html artifact in a
// sandboxed iframe for exactly that reason; its "open" link and the run file card's "open in a
// new tab" handed the same bytes over unframed, so one click on a page a prompt-injected run
// had written gave that page the console.
//
// So a new tab gets the bytes as themselves only when their type cannot carry script — a raster
// image, audio, video, a PDF, plain text, or an untyped download — and everything else (html,
// svg, xml, and every type this list does not know) inside the panel's own sandbox: a wrapper
// page holding one `sandbox="allow-scripts"` frame, so the file's scripts still run while its
// origin is opaque. A PDF is on the passive list rather than wrapped because the browser refuses
// to render one in a sandboxed frame at all; `application/octet-stream` (the server's type for
// an extension it does not know) because a tab given one downloads it, which a sandboxed frame
// would block, and the browser never sniffs it — or text/plain — into a page.

const PASSIVE = new RegExp("^\\s*(?:image/(?!svg\\b)[\\w.+-]+|audio/[\\w.+-]+|video/[\\w.+-]+"
  + "|application/pdf|application/octet-stream|text/plain)\\s*(?:;|$)", "i");

/** Where a new tab should open `url` — a blob URL holding a file of MIME `type` — titled `name`.
 *  `href` is `url` itself for a passive type and a sandboxing wrapper page otherwise; `revoke()`
 *  frees the wrapper, so the caller revokes the pair together and owns both lifetimes, as it
 *  owns every object URL apiBlobUrl hands it. */
export function newTabHref(url, type, name) {
  if (PASSIVE.test(type || "")) return { href: url, revoke() {} };
  // Built as a DOCUMENT and serialized, never assembled as an HTML string: the file name is the
  // one value a routine chose, and the serializer escapes it as the title's text.
  const doc = document.implementation.createHTMLDocument(name || "file");
  const style = doc.createElement("style");
  style.textContent = "html,body{margin:0;height:100%}"
    + "iframe{display:block;border:0;width:100%;height:100%}";
  const frame = doc.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("src", url);
  doc.head.append(style);
  doc.body.append(frame);
  const page = new Blob([`<!doctype html>${doc.documentElement.outerHTML}`],
    { type: "text/html;charset=utf-8" });
  const href = URL.createObjectURL(page);
  return { href, revoke: () => URL.revokeObjectURL(href) };
}
