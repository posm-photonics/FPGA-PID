// gui/client/js/panels/system_panel.js
//
// Global enable, outputs enable, soft reset, ADC/DAC test patterns.
// This panel is the direct successor to scripts/Posm_Dashboard.html
// (the pre-install sanity-check dashboard) and carries the same two
// safety interlocks that dashboard had -- but not as confirm()
// dialogs any more.
//
// Why the change: a confirm() is a text wall you dismiss by muscle
// memory within a week, it can't show the live preconditions that
// actually decide whether enabling outputs is safe right now, and the
// checkbox visibly flips before the dialog resolves, so a cancelled
// action still flickers as if it happened.
//
// So outputs_enable is now a two-action interlock, the way a real
// lockbox does it: ARM turns the well amber and lists the three
// preconditions read from live state, ENGAGE is the only click that
// writes, and the arm lapses after 5 s. The DAC test pattern and the
// soft reset keep a modal, but a styled one with the same wording.
// The write path (ctx.ws.set) is untouched.

import { el, checkbox, readout, numberField, groupLabel, collapsible } from "../fields.js";

const ARM_TIMEOUT_S = 5;

// A themed modal, replacing native confirm(). Resolves through the
// callback exactly where confirm()'s truthy branch used to run.
function showDialog({ title, body, confirmLabel, confirmClass = "btn btn--danger", onConfirm }) {
  const cancel = el("button", { class: "btn btn--ghost", text: "Cancel" });
  const confirmBtn = el("button", { class: confirmClass, text: confirmLabel });
  const dialog = el("div", { class: "dialog", role: "alertdialog", "aria-modal": "true" }, [
    el("h3", { class: "dialog-title" }, [title]),
    el("p", { class: "dialog-body" }, [body]),
    el("div", { class: "dialog-actions" }, [cancel, confirmBtn]),
  ]);
  const backdrop = el("div", { class: "dialog-backdrop" }, [dialog]);

  const close = () => {
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  cancel.addEventListener("click", close);
  confirmBtn.addEventListener("click", () => { close(); onConfirm(); });
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(backdrop);
  confirmBtn.focus();
}

export function mount(container, ctx) {
  const version = readout({ param: "version", label: "Core version" });
  const globalEnable = checkbox(ctx, { param: "global_enable", label: "Global enable" });

  // ---- outputs interlock -------------------------------------------
  let armed = false;
  let armLeft = 0;
  let armTimer = null;

  const armState = el("span", { class: "arm__state" }, ["disabled"]);
  const checkRows = [
    ["No sticky faults", (s) => !s.status_fault_active],
    ["DAC test pattern off", (s) => !s.dac_test_pattern_en],
    ["Global enable on", (s) => !!s.global_enable],
  ].map(([label, test]) => ({
    test,
    el: el("div", { class: "arm__check" }, [el("span", {}, ["\u2013"]), label]),
  }));

  const armBtn = el("button", { class: "btn", text: "Arm outputs" });
  const engageBtn = el("button", { class: "btn btn--warn", text: "Engage outputs" });
  const cancelBtn = el("button", { class: "btn btn--ghost", text: "Cancel" });
  const disableBtn = el("button", { class: "btn btn--warn", text: "Disable outputs" });

  const well = el("div", { class: "arm", "data-armed": "false", "data-live": "false" }, [
    el("div", { class: "arm__head" }, ["Outputs", armState]),
    el("div", { class: "arm__checks" }, checkRows.map((c) => c.el)),
    el("div", { class: "arm__actions" }, [armBtn, engageBtn, cancelBtn, disableBtn]),
  ]);

  function stopArm() {
    armed = false;
    clearInterval(armTimer);
    armTimer = null;
    render(ctx.state());
  }

  armBtn.addEventListener("click", () => {
    armed = true;
    armLeft = ARM_TIMEOUT_S;
    clearInterval(armTimer);
    armTimer = setInterval(() => {
      armLeft -= 1;
      if (armLeft <= 0) stopArm();
      else render(ctx.state());
    }, 1000);
    render(ctx.state());
  });
  cancelBtn.addEventListener("click", stopArm);
  engageBtn.addEventListener("click", () => {
    ctx.ws.set("outputs_enable", true);
    stopArm();
  });
  disableBtn.addEventListener("click", () => {
    ctx.ws.set("outputs_enable", false);
    stopArm();
  });

  // ---- test patterns ------------------------------------------------
  const adcTest = checkbox(ctx, { param: "adc_test_pattern_en", label: "ADC test pattern" });

  const dacToggle = el("input", { type: "checkbox" });
  dacToggle.addEventListener("change", () => {
    if (dacToggle.checked) {
      dacToggle.checked = false; // never flip before the operator confirms
      showDialog({
        title: "Enable DAC test pattern",
        body: "Only enable this with the DAC output disconnected from any real actuator " +
              "(piezo, laser current driver, etc.), or feeding a scope / dummy load.",
        confirmLabel: "Enable test pattern",
        confirmClass: "btn btn--warn",
        onConfirm: () => ctx.ws.set("dac_test_pattern_en", true),
      });
      return;
    }
    ctx.ws.set("dac_test_pattern_en", false);
  });
  const dacRow = el("label", { class: "checkbox-row" }, [dacToggle, "DAC test pattern"]);

  const softResetBtn = el("button", {
    class: "btn btn--danger", text: "Soft reset",
    onclick: () => showDialog({
      title: "Soft reset lock core",
      body: "This drops the loop, clears the integrator and returns lock_fsm to IDLE. " +
            "Outputs stay wired -- disable them first if the actuator can't take a step.",
      confirmLabel: "Reset core",
      onConfirm: () => ctx.ws.set("soft_reset", true),
    }),
  });

  const modeField = numberField(ctx, { param: "mode", label: "Mode word", min: 0, max: 255, step: 1 });
  const faultEnableField = numberField(ctx, { param: "fault_enable_mask", label: "Fault enable mask", min: 0, max: 4095, step: 1 });
  const advanced = collapsible("Register overrides", [modeField.el, faultEnableField.el]);

  container.appendChild(el("div", { class: "panel panel--system" }, [
    el("h2", { class: "panel__title" }, ["System"]),
    version.el,
    globalEnable.el,
    well,
    groupLabel("Test patterns"),
    adcTest.el,
    dacRow,
    advanced.el,
    el("div", { class: "btn-row" }, [softResetBtn]),
  ]));

  function render(state) {
    const live = !!state.outputs_enable;
    const isControl = ctx.role() === "control";

    well.dataset.live = String(live);
    well.dataset.armed = String(armed && !live);

    let failing = 0;
    for (const c of checkRows) {
      const ok = !!c.test(state);
      if (!ok) failing += 1;
      c.el.dataset.ok = String(ok);
      c.el.firstChild.textContent = ok ? "\u2713" : "\u2715";
    }

    if (live) armState.textContent = "live \u2014 driving the actuator";
    else if (armed) armState.textContent = `armed \u2014 ${armLeft}s`;
    else armState.textContent = "disabled";

    armBtn.style.display = !live && !armed ? "" : "none";
    engageBtn.style.display = !live && armed ? "" : "none";
    cancelBtn.style.display = !live && armed ? "" : "none";
    disableBtn.style.display = live ? "" : "none";

    // A failing precondition doesn't lock you out -- this is lab gear
    // and there are legitimate overrides -- but the button says so.
    engageBtn.textContent = failing ? "Engage anyway" : "Engage outputs";
    engageBtn.className = failing ? "btn btn--danger" : "btn btn--warn";

    armBtn.disabled = !isControl;
    engageBtn.disabled = !isControl;
    disableBtn.disabled = !isControl;
    dacToggle.disabled = !isControl;
    softResetBtn.disabled = !isControl;

    // presentational: drives the amber rule under the topbar
    document.body.classList.toggle("outputs-live", live);
  }

  render(ctx.state()); // before the first snapshot, show the disabled resting state

  const bound = [globalEnable, adcTest, modeField, faultEnableField];
  return {
    update(state) {
      version.update(state);
      for (const f of bound) f.update(state);
      if (typeof state.dac_test_pattern_en === "boolean") dacToggle.checked = state.dac_test_pattern_en;
      advanced.setSummary(
        typeof state.mode === "number" ? `mode ${state.mode} \u00B7 mask ${state.fault_enable_mask ?? "\u2014"}` : "\u2014"
      );
      render(state);
    },
  };
}
