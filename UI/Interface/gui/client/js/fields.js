// gui/client/js/fields.js
//
// Small DOM-building helpers shared by every panel. Not a framework --
// just enough to avoid repeating the same twelve lines of slider/
// checkbox wiring in every panel file. Every helper returns an object
// with `.el` (the DOM node to append) and `.update(state)` (called by
// app.js whenever a fresh snapshot arrives).
//
// Presentation notes (2026 redesign): a parameter row now carries the
// register name as a dim sub-label, its unit as a suffix, the range
// printed at the two ends of the track, and a pending state -- the
// value shows in the interface accent while you drag and settles to
// text colour once the FPGA echoes it back. All of that is markup and
// CSS; the commit / drag / disable behaviour below is unchanged.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

function fmt(value, digits) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  return digits === 0 ? String(Math.round(value)) : value.toFixed(digits);
}

// Thin-space thousands grouping, so 32768 reads as 32 768 in a
// monospace column without a comma pretending to be a decimal point.
function group(text) {
  return String(text).replace(/\B(?=(\d{3})+(?!\d))/g, "\u2009");
}

// A label line with the friendly name plus the register name people
// cross-reference against docs/05_register_map.md.
function labelEl(label, param) {
  return el("label", { class: "field__label" }, [
    label,
    el("span", { class: "field__reg" }, [param]),
  ]);
}

function endsEl(min, max, digits, unit) {
  return el("div", { class: "field__ends" }, [
    el("span", {}, [group(fmt(min, digits))]),
    el("span", {}, [unit || ""]),
    el("span", {}, [group(fmt(max, digits))]),
  ]);
}

// A labeled slider bound to a single writable parameter. `digits`
// controls display precision; `writeOnInput` sends `set` messages
// live while dragging (nice for gains you want to hear/see react
// immediately) vs. only on release (nicer for anything that jumps the
// scan or DAC output).
export function slider(ctx, { param, label, unit = "", min, max, step, digits = 2, writeOnInput = true }) {
  const numEl = el("span", { class: "field__num" }, ["--"]);
  const unitEl = el("span", { class: "field__unit" }, [unit]);
  const valueEl = el("span", { class: "field__value mono" }, unit ? [numEl, unitEl] : [numEl]);
  const input = el("input", {
    type: "range", min: String(min), max: String(max), step: String(step),
  });
  let dragging = false;

  const paint = (v) => {
    numEl.textContent = group(fmt(v, digits));
    const pct = max === min ? 0 : ((v - min) / (max - min)) * 100;
    input.style.setProperty("--fill", pct.toFixed(2) + "%");
  };

  const commit = () => {
    const v = Number(input.value);
    paint(v);
    ctx.ws.set(param, v);
  };

  input.addEventListener("input", () => {
    dragging = true;
    valueEl.classList.add("is-pending");
    const v = Number(input.value);
    paint(v);
    if (writeOnInput) ctx.ws.set(param, v);
  });
  input.addEventListener("change", () => {
    dragging = false;
    if (!writeOnInput) commit();
  });

  const wrap = el("div", { class: "field" }, [
    labelEl(label, param),
    valueEl,
    input,
    endsEl(min, max, digits, ""),
  ]);

  return {
    el: wrap,
    update(state) {
      const v = state[param];
      input.disabled = ctx.role() !== "control";
      if (typeof v !== "number") return;
      if (!dragging) {
        input.value = String(v);
        paint(v);
        valueEl.classList.remove("is-pending");
      }
    },
  };
}

// A plain numeric text field, for parameters where a slider's range
// would be awkward (e.g. an autolock window bound spanning the full
// DAC range) but a slider "feel" isn't needed.
export function numberField(ctx, { param, label, unit = "", min, max, step = 1, digits = 0 }) {
  const input = el("input", {
    type: "number", min: String(min), max: String(max), step: String(step),
    inputmode: "decimal",
  });
  let focused = false;
  input.addEventListener("focus", () => { focused = true; });
  input.addEventListener("blur", () => {
    focused = false;
    if (input.value !== "") ctx.ws.set(param, Number(input.value));
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
  });

  const head = el("div", { class: "field__head" }, [
    labelEl(label, param),
    el("span", { class: "field__unit" }, [unit ? unit : ""]),
  ]);
  head.style.display = "flex";
  head.style.justifyContent = "space-between";
  head.style.alignItems = "baseline";
  head.style.gap = "10px";

  const wrap = el("div", { class: "field field--full" }, [head, input]);

  return {
    el: wrap,
    update(state) {
      input.disabled = ctx.role() !== "control";
      const v = state[param];
      if (!focused && typeof v === "number") input.value = fmt(v, digits);
    },
  };
}

export function checkbox(ctx, { param, label }) {
  const input = el("input", { type: "checkbox" });
  input.addEventListener("change", () => ctx.ws.set(param, input.checked));
  const wrap = el("label", { class: "checkbox-row" }, [input, label]);
  return {
    el: wrap,
    update(state) {
      input.disabled = ctx.role() !== "control";
      const v = state[param];
      if (typeof v === "boolean") input.checked = v;
    },
  };
}

export function button(label, onClick, cls = "btn") {
  return el("button", { class: cls, onclick: onClick, text: label });
}

// A silkscreen-style group heading: the label sits in a hairline rule,
// with an optional summary of what the group currently holds.
export function groupLabel(text, summary) {
  return el("div", { class: "group-label" }, summary
    ? [text, el("span", { class: "group-label__summary" }, [summary])]
    : [text]);
}

// A collapsible group for fields you set once per session. The summary
// line stays visible when collapsed, so folding the group away never
// hides what it is set to.
export function collapsible(text, children, { open = false } = {}) {
  const summaryEl = el("span", { class: "group-label__summary" }, [""]);
  const details = el("details", { class: "group" }, [
    el("summary", {}, [text, summaryEl]),
    el("div", { class: "group-body" }, children),
  ]);
  if (open) details.setAttribute("open", "");
  return {
    el: details,
    setSummary(s) { summaryEl.textContent = s; },
  };
}

// Read-only numeric readout, for status values the panel shows but
// never writes.
export function readout({ param, label, unit = "", digits = 0, tone }) {
  const numEl = el("span", {}, ["--"]);
  const valueEl = el("span", { class: "field__value mono" }, unit
    ? [numEl, el("span", { class: "field__unit" }, [unit])]
    : [numEl]);
  const wrap = el("div", { class: "field" }, [
    el("label", { class: "field__label" }, [label]),
    valueEl,
  ]);
  return {
    el: wrap,
    update(state) {
      const v = state[param];
      numEl.textContent = typeof v === "number" ? group(fmt(v, digits)) : String(v ?? "--");
      if (tone) valueEl.style.color = tone(v);
    },
  };
}

export { fmt };
