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

import type { AppState, Auth, ClaudeFile, LockedState, Report, Settings } from "./types.js";

/** The subset of Tauri's global the page uses (withGlobalTauri in tauri.conf.json). */
interface TauriGlobal {
  core: { invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> };
  event: {
    listen<T>(name: string, handler: (e: { payload: T }) => void): Promise<() => void>;
  };
}

declare global {
  interface Window {
    CC_TOKEN: string;
    __TAURI__?: TauriGlobal;
  }
}

const TOKEN = window.CC_TOKEN;
const tauri: TauriGlobal | undefined = window.__TAURI__;

/** True inside the desktop app. */
export const inApp = Boolean(tauri);

/**
 * Fired on `window` when the server answers 401: the session is gone (signed
 * out elsewhere, refresh token revoked). Whoever owns the page swaps in the
 * sign-in screen; nothing here retries.
 */
export const SIGNED_OUT = "cc:signed-out";

function failed(status: number, text: string): Error {
  let detail: { error?: string; auth?: Auth } = {};
  try {
    detail = JSON.parse(text) as typeof detail;
  } catch {
    /* not JSON: the status is all we know */
  }
  if (status === 401)
    window.dispatchEvent(new CustomEvent(SIGNED_OUT, { detail: detail.auth ?? null }));
  return new Error(detail.error || `Request failed (${status})`);
}

async function call<T>(path: string, body?: unknown, asText = false): Promise<T> {
  const method = body === undefined ? "GET" : "POST";
  if (tauri) {
    const res = await tauri.core.invoke<{ status: number; body: string }>("api", {
      method, path, body: body === undefined ? null : JSON.stringify(body),
    });
    if (res.status >= 400) throw failed(res.status, res.body);
    return (asText ? res.body : JSON.parse(res.body)) as T;
  }
  const res = await fetch(path, {
    method,
    headers: {
      "X-CC-Token": TOKEN,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw failed(res.status, await res.text().catch(() => ""));
  return (asText ? res.text() : res.json()) as Promise<T>;
}

export const api = {
  /** Before sign-in the server answers with only `auth`; check `signed_in` first. */
  state: () => call<AppState | LockedState>("/api/state"),
  auth: {
    /** Ask Supabase to email a six-digit code. */
    code: (email: string) => call<{ ok: true; email: string }>("/api/auth/code", { email }),
    verify: (email: string, code: string) =>
      call<{ ok: true; auth: Auth }>("/api/auth/verify", { email, code }),
    signout: () => call<{ ok: true; auth: Auth }>("/api/auth/signout", {}),
  },
  /** Pull from Supabase now; the result arrives as an `update` event. */
  sync: () => call<{ ok: true }>("/api/sync", {}),
  /**
   * Reach into a session: INT is Ctrl+C (stop this turn, keep the conversation),
   * TERM ends the claude process (it can be resumed later).
   */
  signalSession: (input: { host: string; session_id: string; signal: "INT" | "TERM" }) =>
    call<{ ok: true; pid: number; signal: string; host: string }>("/api/session/signal", input),
  report: () => call<Report>("/api/report"),
  settings: (patch: Partial<Settings>) =>
    call<{ settings: Settings }>("/api/settings", patch),
  refresh: (hosts?: string[]) => call<unknown>("/api/refresh", hosts ? { hosts } : {}),
  /** Every CLAUDE.md the server can reach, read fresh from disk (ssh for remote ones). */
  claudeFiles: () => call<{ files: ClaudeFile[]; local_host: string }>("/api/claudemd"),
  saveClaudeFile: (input: { host: string; cwd: string | null; text: string }) =>
    call<{ ok: true; path: string }>("/api/claudemd", input),
  saveEntry: (input: {
    kind: "journal" | "note";
    cwd: string;
    id: string;
    host: string;
    text: string;
    /** Omit to leave the stored title alone; "" clears it. */
    title?: string;
  }) => call<{
    ok: true; ref: string; path: string | null; warn: string | null; title: string;
  }>(
    "/api/entry",
    input,
  ),
  deleteNote: (input: { cwd: string; id: string; host: string }) =>
    call<{ ok: true; deleted: string; warn: string | null }>("/api/entry", {
      kind: "note",
      delete: true,
      ...input,
    }),
  /**
   * A picture for a journal entry. Bytes go up as base64 and come back the
   * same way: the server keeps them in the account's private bucket, and the
   * entry refers to them as cc://image/<id>.
   */
  uploadImage: (data: string) =>
    call<{ ok: true; id: string; url: string }>("/api/image", { data }),
  image: (id: string) =>
    call<{ ok: true; type: string; data: string }>(`/api/image?id=${encodeURIComponent(id)}`),
  /** Ask Claude to summarise everything since the last journal entry. */
  reveal: (path: string) => call<unknown>("/api/reveal", { path }),
  /**
   * The machine list. The server owns it (~/.cc-center-hosts) and answers
   * with the list as it now stands, so the store adopts that rather than
   * guessing: add and remove name one host, reorder sends the whole order.
   */
  addHost: (host: string) =>
    call<{ hosts: string[] }>("/api/hosts", { op: "add", host }),
  removeHost: (host: string) =>
    call<{ hosts: string[] }>("/api/hosts", { op: "remove", host }),
  reorderHosts: (hosts: string[]) =>
    call<{ hosts: string[] }>("/api/hosts", { op: "reorder", hosts }),
  /** In a browser this stops the server; in the app it quits the app. */
  quit: () => (tauri ? tauri.core.invoke<unknown>("quit") : call<unknown>("/api/quit", {})),
};

export interface Handlers {
  update: (reason: string) => void;
  hosts: () => void;
  attention: () => void;
  log: (line: unknown) => void;
  settings: (s: Settings) => void;
  open: () => void;
  closed: () => void;
  /** App only: the menu bar or a notification asked for one session. */
  focus?: (sessionId: string) => void;
}

/** A live subscription; `close()` ends it. */
export interface Stream {
  close(): void;
}

function dispatch(handlers: Handlers, event: string, data: unknown): void {
  if (event === "update") handlers.update((data as { reason?: string })?.reason || "");
  else if (event === "hosts") handlers.hosts();
  else if (event === "attention") handlers.attention();
  else if (event === "log") handlers.log(data);
  else if (event === "settings") handlers.settings(data as Settings);
}

/** Server-sent events: the page never polls for data it can be told about. */
export function listen(handlers: Handlers): Stream {
  if (tauri) {
    // The Rust shell holds the one connection and re-emits every event.
    const stops: Array<() => void> = [];
    let closed = false;
    const keep = (p: Promise<() => void>) =>
      void p.then((stop) => (closed ? stop() : stops.push(stop)));
    keep(tauri.event.listen<{ event: string; data: unknown }>("cc:sse", (e) =>
      dispatch(handlers, e.payload.event, e.payload.data)));
    keep(tauri.event.listen<{ open: boolean }>("cc:stream", (e) =>
      e.payload.open ? handlers.open() : handlers.closed()));
    keep(tauri.event.listen<{ session_id: string }>("cc:focus-session", (e) =>
      handlers.focus?.(e.payload.session_id)));
    void tauri.core.invoke<boolean>("stream_state")
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
    src.addEventListener(name, (e) =>
      dispatch(handlers, name, JSON.parse((e as MessageEvent).data)));
  return { close: () => src.close() };
}
