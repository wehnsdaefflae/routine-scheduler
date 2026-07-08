// Fetch + SSE wrappers with bearer-token auth (token cached in localStorage).

const KEY = "rsched_token";

export function getToken() {
  let token = localStorage.getItem(KEY);
  if (!token) {
    token = window.prompt("routine-scheduler API token (see ~/.config/routine-scheduler/config.yaml):") || "";
    if (token) localStorage.setItem(KEY, token.trim());
  }
  return (token || "").trim();
}

export function clearToken() {
  localStorage.removeItem(KEY);
}

export async function api(path, { method = "GET", body } = {}) {
  const resp = await fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401) {
    clearToken();
    throw new Error("unauthorized — reload the page and enter the token");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `${resp.status} ${resp.statusText}`);
  return data;
}

export function sse(path, handlers) {
  const sep = path.includes("?") ? "&" : "?";
  const source = new EventSource(`${path}${sep}token=${encodeURIComponent(getToken())}`);
  for (const [event, fn] of Object.entries(handlers)) {
    if (event === "onerror") source.onerror = fn;
    else source.addEventListener(event, (e) => fn(JSON.parse(e.data)));
  }
  return source;
}
