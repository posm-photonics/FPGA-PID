// gui/client/js/panels/autolock_panel.js
//
// Trigger autolock, show its current state, maps to robust_autolock
// (rtl/autolock/robust_autolock.py). The feature descriptor fields
// here (window/expected-position/amplitude/width) are the same ones
// docs/00_project_brief.md's PC-side feature-selection step would
// compute automatically from a click-drag on the scope plot -- until
// that PC-side fit exists, this panel lets you enter them by hand.
//
// Grouping (presentational): the supervisor toggle and retry limit are
// the session controls; the nine descriptor fields are a setup task,
// so they fold away behind a one-line summary of the descriptor.

import { el, numberField, checkbox, groupLabel, collapsible, fmt } from "../fields.js";

export function mount(container, ctx) {
  const enableRow = checkbox(ctx, { param: "autolock_enable", label: "Autolock supervisor enabled" });
  const retry = numberField(ctx, { param: "autolock_retry_limit", label: "Retry limit", min: 0, max: 255, step: 1 });

  const descriptor = [
    numberField(ctx, { param: "autolock_window_min", label: "Search window min", unit: "code", min: 0, max: 65535, step: 1 }),
    numberField(ctx, { param: "autolock_window_max", label: "Search window max", unit: "code", min: 0, max: 65535, step: 1 }),
    numberField(ctx, { param: "autolock_expected_min_x", label: "Expected position, low", unit: "code", min: 0, max: 65535, step: 1 }),
    numberField(ctx, { param: "autolock_expected_max_x", label: "Expected position, high", unit: "code", min: 0, max: 65535, step: 1 }),
    numberField(ctx, { param: "autolock_lock_x", label: "Lock handoff position", unit: "code", min: 0, max: 65535, step: 1 }),
    numberField(ctx, { param: "autolock_amp_min", label: "Min feature amplitude", min: -8388608, max: 8388607, step: 1 }),
    numberField(ctx, { param: "autolock_width_min", label: "Min feature width", unit: "code", min: 0, max: 65535, step: 1 }),
    numberField(ctx, { param: "autolock_width_max", label: "Max feature width", unit: "code", min: 0, max: 65535, step: 1 }),
  ];

  const slopeRow = checkbox(ctx, { param: "autolock_slope_sign", label: "Expected slope sign positive" });

  const descGroup = collapsible("Feature descriptor", [
    ...descriptor.map((f) => f.el),
    slopeRow.el,
  ]);

  container.appendChild(el("div", { class: "panel panel--autolock" }, [
    el("h2", { class: "panel__title" }, ["Autolock"]),
    groupLabel("Supervisor"),
    enableRow.el,
    retry.el,
    descGroup.el,
  ]));

  const all = [enableRow, retry, ...descriptor, slopeRow];
  return {
    update(state) {
      for (const f of all) f.update(state);
      const wmin = state.autolock_window_min, wmax = state.autolock_window_max, lx = state.autolock_lock_x;
      descGroup.setSummary(typeof wmin === "number" && typeof wmax === "number"
        ? `win ${fmt(wmin, 0)}\u2013${fmt(wmax, 0)}${typeof lx === "number" ? ` \u00B7 x* ${fmt(lx, 0)}` : ""}`
        : "\u2014");
    },
  };
}
