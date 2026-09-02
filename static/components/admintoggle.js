// The Admin toggle — one button that both conversation composers mount: the per-conversation
// one (D63) and the create composer (D66, because reply #1 fires on create, so admin has to be
// armable before the conversation exists). It holds the admin token for THIS browser session and
// sends it with each request, so the leg runs with capability gating lifted. The server
// re-validates the token on every request and never stores it; turning admin off forgets it. The
// button reads as `danger` while armed — the one unmistakable signal that this conversation can
// reach the full toolset.
//
// It lives here because the two composers have to AGREE on two literals whose other halves are
// elsewhere: the sessionStorage key (one arming covers both composers within a browser session —
// two spellings would silently split that into two independent switches) and the header name,
// which pairs with `ADMIN_HEADER` in engine/admin.py. The four strings around them differ by
// design and are passed in: the create composer talks about STARTING a conversation with the
// full toolset, the per-conversation one about the messages that follow.

import { promptDialog } from "/static/components/dialog.js";
import { el, toast } from "/static/util.js";

const ADMIN_KEY = "rsched_admin_token";

export function adminToggle({ title, prompt, onMsg, offMsg }) {
  let token = sessionStorage.getItem(ADMIN_KEY) || "";
  const node = el("button", { class: "btn small ghost", title }, "admin");
  const paint = () => {
    node.classList.toggle("danger", Boolean(token));
    node.classList.toggle("ghost", !token);
    node.textContent = token ? "admin: on" : "admin";
  };
  node.onclick = async () => {
    if (token) {
      token = ""; sessionStorage.removeItem(ADMIN_KEY); paint();
      toast(offMsg); return;
    }
    const t = await promptDialog(prompt, { placeholder: "paste the admin token" });
    if (!t) return;
    token = t; sessionStorage.setItem(ADMIN_KEY, t); paint();
    toast(onMsg);
  };
  paint();
  return { node, headers: () => (token ? { "x-admin-token": token } : {}) };
}
