/**
 * The sign-in screen: the whole page until the server has an account.
 *
 * Two steps on one form. An email goes to the server, which asks Supabase to
 * mail a six-digit code; the code comes back the same way and the server keeps
 * the session. The page holds no token of its own -- it only knows whether the
 * server is signed in -- so there is nothing here to leak.
 *
 * The markup is static in index.html; this module shows the right step, wires
 * the two submits, and hands control back once the server says yes.
 */

import { api } from "../core/api.js";
import type { Auth } from "../core/types.js";

const $ = <T extends HTMLElement = HTMLElement>(id: string): T | null =>
  document.getElementById(id) as T | null;

let email = "";
let busy = false;
let onSignedIn: (() => void) | null = null;
let bound = false;

function fail(message: string | null): void {
  const el = $("signin-error");
  if (!el) return;
  el.textContent = message ?? "";
  el.hidden = !message;
}

function step(which: "email" | "code"): void {
  const emailStep = $("signin-step-email");
  const codeStep = $("signin-step-code");
  if (emailStep) emailStep.hidden = which !== "email";
  if (codeStep) codeStep.hidden = which !== "code";
  fail(null);
  const focus = which === "email" ? $<HTMLInputElement>("signin-email")
                                  : $<HTMLInputElement>("signin-code");
  focus?.focus();
}

function setBusy(on: boolean): void {
  busy = on;
  for (const id of ["signin-send", "signin-verify"]) {
    const b = $<HTMLButtonElement>(id);
    if (b) b.disabled = on;
  }
}

async function sendCode(): Promise<void> {
  const input = $<HTMLInputElement>("signin-email");
  const value = (input?.value ?? "").trim();
  if (!value) {
    fail("Type the email address the account uses.");
    return;
  }
  setBusy(true);
  try {
    const res = await api.auth.code(value);
    email = res.email;
    const to = $("signin-sent-to");
    if (to) to.textContent = email;
    const code = $<HTMLInputElement>("signin-code");
    if (code) code.value = "";
    step("code");
  } catch (err) {
    fail(err instanceof Error ? err.message : "Could not send the code");
  } finally {
    setBusy(false);
  }
}

async function verify(): Promise<void> {
  const input = $<HTMLInputElement>("signin-code");
  const value = (input?.value ?? "").replace(/\D/g, "");
  if (!value) {
    fail("Type the code from the email.");
    return;
  }
  setBusy(true);
  try {
    await api.auth.verify(email, value);
    fail(null);
    onSignedIn?.();
  } catch (err) {
    fail(err instanceof Error ? err.message : "That code did not work");
  } finally {
    setBusy(false);
  }
}

/** Bind once; the form is static, so the handlers are too. */
export function bindSignin(signedIn: () => void): void {
  onSignedIn = signedIn;
  if (bound) return;
  bound = true;
  $("signin-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    if (busy) return;
    const onCode = !($("signin-step-code")?.hidden ?? true);
    void (onCode ? verify() : sendCode());
  });
  $("signin-back")?.addEventListener("click", () => step("email"));
}

/** Show the sign-in screen (and hide the shell) for this server's auth state. */
export function showSignin(auth: Auth | null): void {
  const screen = $("signin");
  const shell = $("shell");
  if (shell) shell.hidden = true;
  if (!screen) return;
  screen.hidden = false;

  const configured = auth?.configured ?? true;
  const unconfigured = $("signin-unconfigured");
  if (unconfigured) unconfigured.hidden = configured;
  const emailStep = $("signin-step-email");
  const codeStep = $("signin-step-code");
  if (!configured) {
    if (emailStep) emailStep.hidden = true;
    if (codeStep) codeStep.hidden = true;
  } else {
    // Whether signed out on purpose or by a vanished session, start from the
    // email again: a code that was sent earlier has long expired.
    email = "";
    const code = $<HTMLInputElement>("signin-code");
    if (code) code.value = "";
    step("email");
  }
  const project = $("signin-project");
  if (project) project.textContent = auth?.project ? `Supabase project ${auth.project}` : "";
  fail(auth?.error ?? null);
}

export function hideSignin(): void {
  const screen = $("signin");
  const shell = $("shell");
  if (screen) screen.hidden = true;
  if (shell) shell.hidden = false;
}
