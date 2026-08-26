// gui/client/js/panels/event_log_panel.js
//
// A running timeline of fault_gate trips, relock attempts, and lock/
// unlock transitions over time (docs/10_gui_implementation_plan.md,
// section 4d) -- "why did it drop lock at 3am" type debugging that a
// live-status-only view can't answer.

import { el } from "../fields.js";

const MAX_ROWS = 200;

function timeLabel(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour12: false });
}

export function mount(container, ctx) {
  const log = el("div", { class: "event-log" });
  log.appendChild(el("div", { class: "event-log__empty" }, ["waiting for events"]));

  container.appendChild(el("div", { class: "panel panel--events" }, [
    el("h2", { class: "panel__title" }, ["Event log"]),
    log,
  ]));

  ctx.ws.onEvent((evt) => {
    const empty = log.querySelector(".event-log__empty");
    if (empty) empty.remove();
    const row = el("div", { class: "event-log__row", "data-kind": evt.kind }, [
      el("span", { class: "event-log__time" }, [timeLabel(evt.ts)]),
      el("span", { class: "event-log__detail" }, [`${evt.kind}: ${evt.detail}`]),
    ]);
    log.insertBefore(row, log.firstChild);
    while (log.children.length > MAX_ROWS) log.removeChild(log.lastChild);
  });

  ctx.ws.onConnect(() => ctx.ws.subscribe("events"));

  return { update() {} };
}
