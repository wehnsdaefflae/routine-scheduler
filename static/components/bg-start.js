// "⇥ background" — the operator starts a DETACHED TASK from a conversation's composer.
//
// D308 pt 3. `POST /api/conversations/{slug}/background` has existed since the detached-task
// machinery shipped, documented as being there "so a human (or a test) can launch one
// directly", and nothing in static/ ever called it: the only way to get a detached task was
// to ask the assistant to detach one, which spends a whole reply deciding to do what the
// person had already decided.
//
// The composer's text IS the task prompt. Its first line is offered as the label (a one-field
// dialog, so a prompt whose first line is a paragraph does not become a rail row nobody can
// read) and the task appears in the rail at once.
//
// THE CONVERSATION'S OWN REPLY IS NOT STARTED. A person delegating a job is not a person
// asking the assistant something — this control posts to `background` and clears the box, and
// never touches `message`.

import { apiUpload } from "/static/api.js";
import { promptDialog } from "/static/components/dialog.js";
import { el, toast, toastError } from "/static/util.js";

//: How long a rail row's label may be before it stops being a label. The dialog offers a
//: clipped first line; the operator can type whatever he likes over it.
const LABEL_MAX = 60;

/** The label offered for `text`: its first non-empty line, clipped. */
export function labelFor(text) {
  const first = String(text || "").split("\n").map((l) => l.trim()).find(Boolean) || "";
  return first.length > LABEL_MAX ? `${first.slice(0, LABEL_MAX - 1)}…` : first;
}

/** The composer's background control.
 *
 * `text()` reads the composer's current value, `clear()` empties it the way a send does (the
 * stored draft included — the text became the task, so it must not refill on reload), and
 * `onStarted()` is called once the server has the intent, for the rail to read itself again.
 */
export function backgroundButton({ slug, text, clear, onStarted }) {
  const btn = el("button", { class: "btn small",
    title: "start this as a DETACHED background task — it runs as its own process and hands "
      + "its result back here; no reply is started" }, "⇥ background");
  btn.onclick = async () => {
    const prompt = String(text() || "").trim();
    if (!prompt) { toast("type the task first — the message box is the task's prompt"); return; }
    const label = await promptDialog(
      "Start a detached background task. The message box is its prompt; this is the label the "
      + "rail shows it under.", { placeholder: "what this task is", value: labelFor(prompt) });
    if (label == null) return;                      // cancelled: the text stays in the box
    btn.disabled = true;
    try {
      // the route takes FORM fields (api_background.launch_background), not JSON
      const fd = new FormData();
      fd.append("prompt", prompt);
      fd.append("label", label || labelFor(prompt));
      const r = await apiUpload(`/api/conversations/${slug}/background`, fd);
      clear();
      toast(`background task started: ${r.taskid}`);
      onStarted?.(r.taskid);
    } catch (err) { toastError(err, 5000); }
    btn.disabled = false;
  };
  return btn;
}
