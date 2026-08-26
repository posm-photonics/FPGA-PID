// gui/client/js/panels/status_panel.js
//
// Lock state, fault flags, live readout -- maps to lock_fsm, fault_gate,
// lock_watch (docs/10_gui_implementation_plan.md, section 3).

import { el, button } from "../fields.js";

const LOCK_STATE_NAMES = [
  "IDLE", "WIDE_SCAN", "TRACE_READY", "USER_SELECT", "ZOOM_SCAN",
  "FEATURE_VERIFY", "ARM_LOCK", "LOCKED", "LOCK_WATCH", "RELOCK_SCAN", "FAULT",
];

const FAULT_PARAMS = [
  ["fault_adc_ch0_overrange", "ADC CH0 overrange"],
  ["fault_adc_ch1_overrange", "ADC CH1 overrange"],
  ["fault_adc_ch0_stuck", "ADC CH0 stuck"],
  ["fault_adc_ch1_stuck", "ADC CH1 stuck"],
  ["fault_adc_missing_valid", "ADC valid dropped out"],
  ["fault_lock_watch", "lock_watch fault"],
  ["fault_relock_requested", "relock requested"],
  ["fault_external_interlock", "external interlock"],
];

export function mount(container, ctx) {
  const stateEl = el("div", { class: "status-hero__state" }, ["--"]);
  const hero = el("div", { class: "status-hero" }, [
    stateEl,
    el("span", { class: "field__label" }, ["lock_fsm.state"]),
  ]);

  const rows = {};
  const gridRows = [
    ["status_locked", "locked"],
    ["status_scanning", "scanning"],
    ["status_saturation", "saturation"],
    ["status_trace_ready", "trace ready"],
    ["status_fault_active", "fault active", "fault"],
  ];
  const grid = el("div", { class: "status-grid" });
  for (const [param, label, tone] of gridRows) {
    const row = el("div", { class: "status-grid__row", "data-tone": tone || "" }, [
      el("span", { class: "led" }), label,
    ]);
    grid.appendChild(row);
    rows[param] = row;
  }

  const faultList = el("div", { class: "fault-list" });
  const clearBtn = button("Clear faults", () => ctx.ws.clearFaults(), "btn btn--danger");
  faultList.appendChild(el("div", { class: "btn-row" }, [clearBtn]));

  container.appendChild(el("div", { class: "panel panel--status" }, [
    el("h2", { class: "panel__title" }, ["Lock status"]),
    hero,
    grid,
    faultList,
  ]));

  return {
    update(state) {
      const s = state.lock_state;
      const name = LOCK_STATE_NAMES[s] ?? `#${s}`;
      stateEl.textContent = name;
      let tone = "idle";
      if (state.status_fault_active) tone = "fault";
      else if (state.status_locked) tone = "locked";
      else if (state.status_scanning) tone = "scanning";
      stateEl.dataset.tone = tone;

      for (const [param] of gridRows) {
        rows[param].dataset.active = String(!!state[param]);
      }

      clearBtn.disabled = ctx.role() !== "control";
      // presentational only: lets style.css treat a read-only session as a
      // state of the whole panel rather than greying each control out.
      document.body.classList.toggle("role-viewer", ctx.role() !== "control");

      const active = FAULT_PARAMS.filter(([p]) => state[p]);
      const existingItems = faultList.querySelectorAll(".fault-list__item, .fault-list__empty");
      existingItems.forEach((n) => n.remove());
      if (active.length === 0) {
        faultList.insertBefore(el("div", { class: "fault-list__empty" }, ["no sticky faults"]), faultList.firstChild);
      } else {
        for (const [, label] of active) {
          faultList.insertBefore(el("div", { class: "fault-list__item" }, [`\u25CF ${label}`]), faultList.firstChild);
        }
      }
    },
  };
}
