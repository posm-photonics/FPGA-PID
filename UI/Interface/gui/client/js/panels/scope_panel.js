// gui/client/js/panels/scope_panel.js
//
// Live waveform plot, subscribes to the "scope_frame" stream, maps to
// trace_capture (rtl/control/trace_capture.py). This is the panel
// people actually stare at (docs/10_gui_implementation_plan.md,
// section 5 build order, step 5) so it gets the one deliberate visual
// flourish in the app -- a phosphor-glow trace on a scanline
// background, styled after the CRT scopes this instrument sits next
// to on a real bench.

import { el, checkbox, button } from "../fields.js";

export function mount(container, ctx) {
  const canvas = el("canvas", { width: "640", height: "320" });
  const glass = el("div", { class: "scope-glass" }, [canvas]);
  const wrap = el("div", { class: "scope-wrap" }, [glass]);

  // Trace / graticule colours live in css/style.css so the palette has a
  // single home; read them once per draw rather than hard-coding hexes.
  const cssVar = (name, fallback) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const dpr = window.devicePixelRatio || 1;
  const cssHeight = 420;
  let cssWidth = 640;

  const chLabel = el("span", {}, ["--"]);
  const countLabel = el("span", {}, ["0 pts"]);
  const meta = el("div", { class: "scope-meta" }, [chLabel, countLabel]);

  const chSel = checkbox(ctx, { param: "trace_config_channel_sel", label: "Capture raw RF monitor (CH1) instead of error" });
  const captureBtn = button("Capture now", () => ctx.ws.captureTrace(), "btn btn--primary");

  container.appendChild(el("div", { class: "panel panel--scope" }, [
    el("h2", { class: "panel__title" }, [
      "Scope / trace",
      el("span", { class: "led" }),
    ]),
    wrap,
    meta,
    el("div", { class: "btn-row", style: "margin-top: 10px;" }, [captureBtn]),
    el("div", { style: "margin-top: 10px;" }, [chSel.el]),
  ]));

  function resizeCanvas() {
    cssWidth = glass.clientWidth || 640;
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    canvas.style.width = cssWidth + "px";
    canvas.style.height = cssHeight + "px";
    draw(lastFrame);
  }
  window.addEventListener("resize", resizeCanvas);

  let lastFrame = null;

  function draw(frame) {
    const c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, cssWidth, cssHeight);

    // baseline grid
    c.strokeStyle = cssVar("--scope-grid", "rgba(145,132,217,0.13)");
    c.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = (cssHeight / 4) * i;
      c.beginPath(); c.moveTo(0, y); c.lineTo(cssWidth, y); c.stroke();
    }
    for (let i = 1; i < 6; i++) {
      const x = (cssWidth / 6) * i;
      c.beginPath(); c.moveTo(x, 0); c.lineTo(x, cssHeight); c.stroke();
    }

    if (!frame || !frame.y || frame.y.length === 0) {
      c.fillStyle = cssVar("--scope-hint", "rgba(179,183,202,0.55)");
      c.font = '11px "JetBrains Mono", ui-monospace, monospace';
      c.textAlign = "center";
      c.fillText("NO TRACE \u2014 START A SCAN OR PRESS CAPTURE NOW", cssWidth / 2, cssHeight / 2);
      c.textAlign = "left";
      return;
    }

    const ys = frame.y;
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const pad = (yMax - yMin) * 0.1 || 1;
    const lo = yMin - pad, hi = yMax + pad;
    const color = frame.channel === "ch1"
      ? cssVar("--scope-trace-ch1", "#ffb454")
      : cssVar("--scope-trace-error", "#7ef0a0");

    c.beginPath();
    c.strokeStyle = color;
    c.lineWidth = 1.6;
    c.shadowColor = color;
    c.shadowBlur = 6;
    for (let i = 0; i < ys.length; i++) {
      const x = (i / (ys.length - 1)) * cssWidth;
      const t = (ys[i] - lo) / (hi - lo || 1);
      const y = cssHeight - t * cssHeight;
      if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
    }
    c.stroke();
    c.shadowBlur = 0;
  }

  ctx.ws.onScopeFrame((frame) => {
    lastFrame = frame;
    chLabel.textContent = frame.channel === "ch1" ? "CH1 \u2014 raw RF monitor" : "CH0 \u2014 error";
    chLabel.className = "channel--" + (frame.channel === "ch1" ? "ch1" : "error");
    countLabel.textContent = `${(frame.y || []).length} pts`;
    draw(frame);
  });

  ctx.ws.onConnect(() => ctx.ws.subscribe("scope"));

  requestAnimationFrame(resizeCanvas);

  return {
    update(state) {
      chSel.update(state);
      captureBtn.disabled = ctx.role() !== "control";
    },
  };
}
