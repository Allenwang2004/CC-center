/**
 * Everything talks to the local server. No outside requests, ever -- Supabase
 * is reached by the server on this page's behalf, never from here.
 */

import type { AppState, Auth, LockedState, Report, Settings } from "./types.js";

declare global {
  interface Window {
    CC_TOKEN: string;
  }
}

const TOKEN = window.CC_TOKEN;

/**
 * Fired on `window` when the server answers 401: the session is gone (signed
 * out elsewhere, refresh token revoked). Whoever owns the page swaps in the
 * sign-in screen; nothing here retries.
 */
export const SIGNED_OUT = "cc:signed-out";

async function call<T>(path: string, body?: unknown, asText = false): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-CC-Token": TOKEN,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { error?: string; auth?: Auth };
    if (res.status === 401)
      window.dispatchEvent(new CustomEvent(SIGNED_OUT, { detail: detail.auth ?? null }));
    throw new Error(detail.error || `Request failed (${res.status})`);
  }
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
  report: () => call<Report>("/api/report"),
  settings: (patch: Partial<Settings>) =>
    call<{ settings: Settings }>("/api/settings", patch),
  refresh: (hosts?: string[]) => call<unknown>("/api/refresh", hosts ? { hosts } : {}),
  markdown: (view: "day" | "project", prompts: number, tokens: boolean) =>
    call<string>(
      `/api/markdown?view=${view}&prompts=${prompts}&tokens=${tokens ? 1 : 0}`,
      undefined,
      true,
    ),
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
  /** Ask Claude to summarise everything since the last journal entry. */
  reveal: (path: string) => call<unknown>("/api/reveal", { path }),
  exportFile: (kind: string, text?: string) =>
    call<{ path: string }>("/api/export", { kind, text }),
  hosts: (hosts: string[]) => call<unknown>("/api/hosts", { hosts }),
  quit: () => call<unknown>("/api/quit", {}),
};

/** Server-sent events: the page never polls for data it can be told about. */
export function listen(handlers: {
  update: (reason: string) => void;
  hosts: () => void;
  attention: () => void;
  log: (line: unknown) => void;
  settings: (s: Settings) => void;
  open: () => void;
  closed: () => void;
}): EventSource {
  const src = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  src.onopen = handlers.open;
  src.onerror = handlers.closed;
  src.addEventListener("update", (e) =>
    handlers.update((JSON.parse((e as MessageEvent).data) as { reason?: string }).reason || ""));
  src.addEventListener("hosts", () => handlers.hosts());
  src.addEventListener("attention", () => handlers.attention());
  src.addEventListener("log", (e) => handlers.log(JSON.parse((e as MessageEvent).data)));
  src.addEventListener("settings", (e) =>
    handlers.settings(JSON.parse((e as MessageEvent).data) as Settings));
  return src;
}
