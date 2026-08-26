// gui/client/js/panels/pi_panel.js
//
// P and I gain controls, maps to pi_controller (rtl/dsp/pi_controller.py).
// Gains are Q3.14 fixed point on the wire; gui/server/parameters.py
// already converts to/from real numbers, so this panel just deals in
// plain floats.
//
// Grouping (presentational): gains on top because they are what you
// actually tune; the output limits are a once-per-install decision so
// they fold away behind a summary line; integrator actions last.

import { el, slider, checkbox, button, groupLabel, collapsible, fmt } from "../fields.js";

export function mount(container, ctx) {
  const kp = slider(ctx, { param: "p_gain", label: "P gain (Kp)", min: -4, max: 4, step: 0.001, digits: 3 });
  const ki = slider(ctx, { param: "i_gain", label: "I gain (Ki)", min: -4, max: 4, step: 0.001, digits: 3 });
  const outMin = slider(ctx, {
    param: "fast_out_min", label: "Fast out min", unit: "code",
    min: -32768, max: 0, step: 1, digits: 0, writeOnInput: false,
  });
  const outMax = slider(ctx, {
    param: "fast_out_max", label: "Fast out max", unit: "code",
    min: 0, max: 32767, step: 1, digits: 0, writeOnInput: false,
  });

  const holdRow = checkbox(ctx, { param: "hold_request", label: "Hold (freeze integrator)" });
  // integrator_reset is a level bit in register_bank.py, not a
  // self-clearing pulse like soft_reset/fault_clear_request -- pulse
  // it from the client side so it doesn't hold the integrator at zero
  // forever.
  const intResetBtn = button("Reset integrator", () => {
    ctx.ws.set("integrator_reset", true);
    setTimeout(() => ctx.ws.set("integrator_reset", false), 150);
  });

  const limits = collapsible("Output limits", [outMin.el, outMax.el]);

  const fields = [kp, ki, outMin, outMax, holdRow];

  container.appendChild(el("div", { class: "panel panel--pi" }, [
    el("h2", { class: "panel__title" }, ["Fast loop / PI"]),
    groupLabel("Gains"),
    kp.el,
    ki.el,
    limits.el,
    groupLabel("Integrator"),
    holdRow.el,
    el("div", { class: "btn-row" }, [intResetBtn]),
  ]));

  return {
    update(state) {
      for (const f of fields) f.update(state);
      intResetBtn.disabled = ctx.role() !== "control";
      const lo = state.fast_out_min, hi = state.fast_out_max;
      limits.setSummary(typeof lo === "number" && typeof hi === "number"
        ? `${fmt(lo, 0)} \u2026 ${fmt(hi, 0)} code`
        : "\u2014");
    },
  };
}
