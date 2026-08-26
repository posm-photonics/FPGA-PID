// gui/client/js/panels/scan_panel.js
//
// Ramp scan controls (range, rate, enable), maps to ramp_scan
// (rtl/control/ramp_scan.py).
//
// Grouping (presentational): the wide scan is the everyday control;
// the zoom pair only matters once a feature has been picked, so it
// folds away behind a summary of where the zoom currently sits.

import { el, slider, numberField, checkbox, groupLabel, collapsible, fmt } from "../fields.js";

export function mount(container, ctx) {
  const wide = [
    slider(ctx, { param: "ramp_min", label: "Wide scan min", unit: "code", min: -32768, max: 32767, step: 8, digits: 0, writeOnInput: false }),
    slider(ctx, { param: "ramp_max", label: "Wide scan max", unit: "code", min: -32768, max: 32767, step: 8, digits: 0, writeOnInput: false }),
    slider(ctx, { param: "ramp_step", label: "Step size", unit: "code/tick", min: 1, max: 2000, step: 1, digits: 0, writeOnInput: false }),
    slider(ctx, { param: "ramp_tick_div", label: "Scan rate", unit: "cyc/tick", min: 1, max: 4000, step: 1, digits: 0, writeOnInput: false }),
  ];

  const zoom = [
    numberField(ctx, { param: "ramp_center", label: "Zoom center", unit: "code", min: -32768, max: 32767, step: 1 }),
    numberField(ctx, { param: "ramp_width", label: "Zoom half-width", unit: "code", min: 1, max: 65535, step: 1 }),
  ];

  const zoomGroup = collapsible("Zoom scan", zoom.map((f) => f.el));

  const enableRow = checkbox(ctx, { param: "lock_enable_request", label: "Request lock acquisition" });

  container.appendChild(el("div", { class: "panel panel--scan" }, [
    el("h2", { class: "panel__title" }, ["Scan"]),
    groupLabel("Wide scan"),
    ...wide.map((f) => f.el),
    zoomGroup.el,
    groupLabel("Acquisition"),
    enableRow.el,
  ]));

  const all = [...wide, ...zoom, enableRow];
  return {
    update(state) {
      for (const f of all) f.update(state);
      const c = state.ramp_center, w = state.ramp_width;
      zoomGroup.setSummary(typeof c === "number" && typeof w === "number"
        ? `${fmt(c, 0)} \u00B1 ${fmt(w, 0)}`
        : "\u2014");
    },
  };
}
