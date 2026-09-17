/**
 * The sidebar: the machines you are asking. The range used to sit here too;
 * it is drawn onto the two tabs it actually shapes now (see renderRange).
 */
import { api } from "../core/api.js";
import { h, mount } from "./dom.js";
import { ago, plural } from "../core/format.js";
import { store } from "../core/store.js";
export function renderMachines(save, refresh) {
    const host = document.getElementById("machines");
    if (!host || !store.report)
        return;
    const state = new Map(store.report.hosts.map((s) => [s.host, s]));
    const off = new Set(store.settings.disabled_hosts ?? []);
    const local = store.localHost;
    const localState = state.get(local);
    const row = (name, checked, onToggle, isLocal) => {
        const s = state.get(name);
        const cls = s?.busy ? "busy" : s?.status === "ok" ? "ok" : s?.status === "error" ? "bad" : "";
        const box = h("input", { type: "checkbox", checked });
        box.addEventListener("change", () => onToggle(box.checked));
        return h("div", { class: `machine-row ${checked ? "" : "off"}`, data: { host: name } }, 
        // The local row cannot be reordered, but it still needs the grip's width
        // or its checkbox sits a few pixels left of every other one.
        isLocal
            ? h("span", { class: "grip is-fixed", aria: { hidden: "true" } })
            : h("span", { class: "grip", role: "button", tabindex: "0" }, "∷"), box, h("span", { class: `pulse ${cls}` }), h("span", { class: "machine-name" }, name), isLocal ? h("span", { class: "tag" }, "here") : null, h("span", { class: "machine-count" }, s?.status === "ok" ? String(s.n ?? 0) : s?.status === "error" ? "!" : ""), isLocal ? null : h("button", { class: "linkish danger", type: "button",
            data: { removeHost: name } }, "Remove"));
    };
    mount(host, row(local || "this machine", store.settings.include_local, (on) => void save({ include_local: on }), true), store.hosts.map((name) => row(name, !off.has(name), (on) => {
        const next = new Set(off);
        if (on)
            next.delete(name);
        else
            next.add(name);
        void save({ disabled_hosts: [...next] });
    }, false)));
    void localState;
    void refresh;
}
/**
 * The range is drawn wherever a `.range` container asks for it -- the Sessions
 * and Activity tabs each carry one -- and every `.range-date` picker shows the
 * same one-day choice, so the two tabs never disagree about the window.
 */
export function renderRange(save) {
    const current = store.settings.date ? -1 : store.settings.days;
    for (const host of document.querySelectorAll(".range"))
        mount(host, [1, 3, 7, 30].map((days) => {
            const btn = h("button", { class: `chip ${current === days ? "on" : ""}`, type: "button" }, days === 1 ? "Today" : `${days} days`);
            btn.addEventListener("click", () => void save({ days, date: "" }));
            return btn;
        }));
    for (const picker of document.querySelectorAll(".range-date"))
        if (picker !== document.activeElement)
            picker.value = store.settings.date ?? "";
}
export function renderStatus(connected) {
    const el = document.getElementById("connection");
    if (!el)
        return;
    const last = store.report?.last_local
        ? ago(Date.now() / 1000 - store.report.last_local)
        : "not yet";
    mount(el, h("span", { class: `pulse ${connected ? "ok" : "bad"}` }), h("span", null, connected ? "Watching" : "Disconnected, retrying"), h("span", { class: "muted" }, `scanned ${last}`));
}
export function renderScope() {
    const el = document.getElementById("scope");
    if (!el || !store.report)
        return;
    const w = store.report.window;
    const days = w?.days ?? null;
    const label = days?.length
        ? days.length === 1 ? days[0] : `${days[0]} to ${days[days.length - 1]}`
        : "custom range";
    el.textContent = `${label} / ${plural(store.report.sessions.length, "session")}`;
}
export function renderRemoteToggle(save, refresh) {
    const btn = document.getElementById("remote-toggle");
    if (!btn)
        return;
    const on = store.settings.remote_enabled !== false;
    btn.textContent = on ? "Remote on" : "Remote off";
    btn.classList.toggle("off", !on);
    btn.title = on
        ? "Turn this off when the lab machines are unreachable and it will stop trying"
        : "Not reaching out to any remote machine";
    const note = document.getElementById("remote-note");
    if (note)
        note.hidden = on;
    btn.onclick = async () => {
        await save({ remote_enabled: !on });
        if (!on)
            refresh();
    };
}
/** Machines can be reordered by dragging the grip; the order is written to disk. */
export function bindMachineDrag() {
    const host = document.getElementById("machines");
    if (!host)
        return;
    let dragging = null;
    host.addEventListener("pointerdown", (e) => {
        const grip = e.target.closest(".grip");
        if (!grip)
            return;
        dragging = grip.closest(".machine-row");
        dragging?.classList.add("dragging");
        e.preventDefault();
    });
    host.addEventListener("pointermove", (e) => {
        if (!dragging)
            return;
        const rows = [...host.querySelectorAll(".machine-row")].filter((r) => r.dataset.host !== store.localHost && r !== dragging);
        for (const row of rows) {
            const box = row.getBoundingClientRect();
            if (e.clientY > box.top && e.clientY < box.bottom) {
                const before = e.clientY < box.top + box.height / 2;
                host.insertBefore(dragging, before ? row : row.nextSibling);
                break;
            }
        }
    });
    const drop = () => {
        if (!dragging)
            return;
        dragging.classList.remove("dragging");
        dragging = null;
        const order = [...host.querySelectorAll(".machine-row")]
            .map((r) => r.dataset.host)
            .filter((x) => Boolean(x) && x !== store.localHost);
        store.hosts = order;
        void api.reorderHosts(order).then((res) => (store.hosts = res.hosts));
    };
    host.addEventListener("pointerup", drop);
    host.addEventListener("pointercancel", drop);
}
//# sourceMappingURL=sidebar.js.map