// The clipboard BRIDGE between this machine and a relayed noVNC screen (F831).
//
// The operator, 2026-11-02: "the host to client clipboard sync for the sidecar browser does not
// work". D221 had made the Browser page's switch the iframe's `allow="clipboard-read;
// clipboard-write"`, on the reading that the embedded noVNC page would reach the host clipboard
// once permitted. It never does: neither the noVNC the browser sidecar serves (Debian bookworm's
// 1.3.0) nor the desktops' 1.6.0 calls `navigator.clipboard` anywhere. Their clipboard is a
// MANUAL side panel — a textarea `#noVNC_clipboard_text` whose `change` event sends its text to
// the guest (`UI.clipboardSend` → `rfb.clipboardPasteFrom`) and whose value noVNC overwrites
// when the guest copies (`UI.clipboardReceive`, a plain `.value =` assignment). A permission
// grant only removes a block; something still has to make the call, and nothing did.
//
// This module makes the calls, from the embedder, through that same panel — the one contract
// both noVNC versions share. The screen is relayed through the console's own origin
// (components/screen.js), so the frame's document is reachable from here:
//
//   • host → guest: ON REQUEST ONLY — the Browser page's "paste my clipboard into the screen"
//     button calls `send()`, which reads the clipboard and, if it is new, puts it in the panel
//     and fires `change`: noVNC's own handler sends it, and x11vnc sets the X CLIPBOARD
//     selection, so Ctrl-V in the guest pastes what was copied here. It is NEVER read on a click
//     or focus of the screen: 0.499.0 did, and Chrome answers a clipboard read with its own
//     "Paste"/"Einfügen" prompt, which swallowed every click on the screen — the operator could
//     not use the browser at all (2026-11-05). A read happens only behind a button the operator
//     pressed for exactly that, where the prompt is the expected answer;
//   • guest → host: the panel's `value` setter is wrapped on that one element, so the moment
//     noVNC writes what the guest copied, it is written to this machine's clipboard too.
//
// THE BRIDGE IS THE CHANNEL, so attaching it is what the Browser page's switch does; the frame
// keeps the `allow="clipboard-read; clipboard-write"` grant beside it, so the calls — made on
// the FRAME's own `navigator.clipboard` — are permitted explicitly rather than by whatever a
// browser's default policy for a same-origin frame happens to be. Do not read the grant as the
// gate: a same-origin frame may be allowed the clipboard without it, which is exactly why D221's
// grant-only switch looked like control while nothing used the channel. A refusal (the document
// not focused, the operator declining the browser's prompt) is a sync that does not happen,
// never an error painted at the reader.
//
// `last` is the one text both sides last agreed on: it keeps the bridge from echoing a value
// back to the side it came from, and from re-sending an unchanged clipboard on every click.

const PANEL = "noVNC_clipboard_text";

/** Bridge `frame`'s noVNC clipboard panel to this machine's clipboard. Re-attaches on every
 *  load of the frame (a reconnect replaces its document). Returns a dispose function carrying
 *  `send()`, the on-request host → guest push (resolves true when something was sent). */
export function bridgeClipboard(frame) {
  let last = null;
  let detach = () => {};
  let push = async () => false;

  const attach = () => {
    detach();
    let win, doc;
    try { win = frame.contentWindow; doc = frame.contentDocument; } catch { return; }
    const panel = doc?.getElementById(PANEL);
    const clip = win?.navigator?.clipboard;
    const native = win && Object.getOwnPropertyDescriptor(win.HTMLTextAreaElement.prototype, "value");
    if (!panel || !clip || !native) return;   // not a noVNC page (yet): nothing to bridge

    Object.defineProperty(panel, "value", {
      configurable: true,
      get() { return native.get.call(this); },
      set(v) {
        native.set.call(this, v);
        const text = String(v ?? "");
        if (!text || text === last) return;   // a clear, or the text this bridge just sent
        last = text;
        clip.writeText(text).catch(() => { last = null; });
      },
    });

    push = async () => {
      const text = await navigator.clipboard.readText();   // the operator pressed for this
      if (!text) return false;
      last = text;
      panel.value = text;                        // through the setter: `last` already matches
      panel.dispatchEvent(new win.Event("change"));
      return true;
    };
    detach = () => {
      push = async () => false;
      delete panel.value;                        // back to the prototype's accessor
    };
  };

  frame.addEventListener("load", attach);
  const dispose = () => { frame.removeEventListener("load", attach); detach(); detach = () => {}; };
  dispose.send = () => push();
  return dispose;
}
