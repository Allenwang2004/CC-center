/**
 * Everything talks to the local server. No outside requests, ever -- Supabase
 * is reached by the server on this page's behalf, never from here.
 *
 * Two ways of reaching it, chosen once at load. In a browser the page holds
 * the token the server injected and calls it over HTTP. Inside the desktop
 * app (`desktop/`) the page has no token: every call goes through the Tauri
 * `api` command, which the Rust shell forwards to the server, and the event
 * stream arrives as Tauri events instead of an EventSource. Nothing outside
 * this file knows which of the two it is.
 */
const TOKEN = window.CC_TOKEN;
const tauri = window.__TAURI__;
/** True inside the desktop app. */
export const inApp = Boolean(tauri);
/**
 * Fired on `window` when the server answers 401: the session is gone (signed
 * out elsewhere, refresh token revoked). Whoever owns the page swaps in the
 * sign-in screen; nothing here retries.
 */
export const SIGNED_OUT = "cc:signed-out";
function failed(status, text) {
    let detail = {};
    try {
        detail = JSON.parse(text);
    }
    catch {
        /* not JSON: the status is all we know */
    }
    if (status === 401)
        window.dispatchEvent(new CustomEvent(SIGNED_OUT, { detail: detail.auth ?? null }));
    return new Error(detail.error || `Request failed (${status})`);
}
async function call(path, body, asText = false) {
    const method = body === undefined ? "GET" : "POST";
    if (tauri) {
        const res = await tauri.core.invoke("api", {
            method, path, body: body === undefined ? null : JSON.stringify(body),
        });
        if (res.status >= 400)
            throw failed(res.status, res.body);
        return (asText ? res.body : JSON.parse(res.body));
    }
    const res = await fetch(path, {
        method,
        headers: {
            "X-CC-Token": TOKEN,
            ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok)
        throw failed(res.status, await res.text().catch(() => ""));
    return (asText ? res.text() : res.json());
}
export const api = {
    /** Before sign-in the server answers with only `auth`; check `signed_in` first. */
    state: () => call("/api/state"),
    auth: {
        /** Ask Supabase to email a six-digit code. */
        code: (email) => call("/api/auth/code", { email }),
        verify: (email, code) => call("/api/auth/verify", { email, code }),
        signout: () => call("/api/auth/signout", {}),
    },
    /** Pull from Supabase now; the result arrives as an `update` event. */
    sync: () => call("/api/sync", {}),
    /**
     * Reach into a session: INT is Ctrl+C (stop this turn, keep the conversation),
     * TERM ends the claude process (it can be resumed later).
     */
    signalSession: (input) => call("/api/session/signal", input),
    report: () => call("/api/report"),
    settings: (patch) => call("/api/settings", patch),
    refresh: (hosts) => call("/api/refresh", hosts ? { hosts } : {}),
    /** Every CLAUDE.md the server can reach, read fresh from disk (ssh for remote ones). */
    claudeFiles: () => call("/api/claudemd"),
    saveClaudeFile: (input) => call("/api/claudemd", input),
    saveEntry: (input) => call("/api/entry", input),
    deleteNote: (input) => call("/api/entry", {
        kind: "note",
        delete: true,
        ...input,
    }),
    /**
     * A picture for a journal entry. Bytes go up as base64 and come back the
     * same way: the server keeps them in the account's private bucket, and the
     * entry refers to them as cc://image/<id>.
     */
    uploadImage: (data) => call("/api/image", { data }),
    image: (id) => call(`/api/image?id=${encodeURIComponent(id)}`),
    /** Ask Claude to summarise everything since the last journal entry. */
    reveal: (path) => call("/api/reveal", { path }),
    /**
     * The machine list. The server owns it (~/.cc-center-hosts) and answers
     * with the list as it now stands, so the store adopts that rather than
     * guessing: add and remove name one host, reorder sends the whole order.
     */
    addHost: (host) => call("/api/hosts", { op: "add", host }),
    removeHost: (host) => call("/api/hosts", { op: "remove", host }),
    reorderHosts: (hosts) => call("/api/hosts", { op: "reorder", hosts }),
    /** In a browser this stops the server; in the app it quits the app. */
    quit: () => (tauri ? tauri.core.invoke("quit") : call("/api/quit", {})),
};
function dispatch(handlers, event, data) {
    if (event === "update")
        handlers.update(data?.reason || "");
    else if (event === "hosts")
        handlers.hosts();
    else if (event === "attention")
        handlers.attention();
    else if (event === "log")
        handlers.log(data);
    else if (event === "settings")
        handlers.settings(data);
}
/** Server-sent events: the page never polls for data it can be told about. */
export function listen(handlers) {
    if (tauri) {
        // The Rust shell holds the one connection and re-emits every event.
        const stops = [];
        let closed = false;
        const keep = (p) => void p.then((stop) => (closed ? stop() : stops.push(stop)));
        keep(tauri.event.listen("cc:sse", (e) => dispatch(handlers, e.payload.event, e.payload.data)));
        keep(tauri.event.listen("cc:stream", (e) => e.payload.open ? handlers.open() : handlers.closed()));
        keep(tauri.event.listen("cc:focus-session", (e) => handlers.focus?.(e.payload.session_id)));
        void tauri.core.invoke("stream_state")
            .then((open) => (open ? handlers.open() : handlers.closed()));
        return {
            close: () => {
                closed = true;
                stops.splice(0).forEach((stop) => stop());
            },
        };
    }
    const src = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
    src.onopen = handlers.open;
    src.onerror = handlers.closed;
    for (const name of ["update", "hosts", "attention", "log", "settings"])
        src.addEventListener(name, (e) => dispatch(handlers, name, JSON.parse(e.data)));
    return { close: () => src.close() };
}
//# sourceMappingURL=api.js.map