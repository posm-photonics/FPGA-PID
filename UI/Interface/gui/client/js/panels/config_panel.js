// gui/client/js/panels/config_panel.js
//
// Save/load named configurations, see docs/10_gui_implementation_plan.md
// section 4c. A visible diff-before-apply is the safety net that
// section calls out as worth having once there's more that can be set
// wrong -- this panel always fetches a diff before a load actually
// applies anything.

import { el, button } from "../fields.js";

export function mount(container, ctx) {
  const nameInput = el("input", { type: "text", placeholder: "config name" });
  const saveBtn = button("Save current", () => {
    const name = nameInput.value.trim();
    if (name) { ctx.ws.saveConfig(name); ctx.ws.listConfigs(); }
  }, "btn btn--primary");

  const list = el("ul", { class: "config-list" });
  const diffBox = el("div", { style: "margin-top: 10px;" });

  function renderList(names) {
    list.innerHTML = "";
    if (names.length === 0) {
      list.appendChild(el("li", {}, [el("span", { class: "empty-state" }, ["no saved configs yet"])]));
      return;
    }
    for (const name of names) {
      const previewBtn = el("button", { text: "diff", onclick: () => ctx.ws.diffConfig(name) });
      const loadBtn = el("button", { text: "load", onclick: () => {
        if (confirm(`Load config "${name}"? This writes every saved parameter to the running system.`)) {
          ctx.ws.loadConfig(name);
        }
      } });
      list.appendChild(el("li", {}, [
        name,
        el("span", { class: "config-list__actions" }, [previewBtn, loadBtn]),
      ]));
    }
  }

  function renderDiff(payload) {
    diffBox.innerHTML = "";
    const changes = Object.entries(payload.changes || {});
    diffBox.appendChild(el("div", { class: "group-label" }, [`diff \u2014 ${payload.name}`]));
    if (changes.length === 0) {
      diffBox.appendChild(el("div", { class: "diff-empty" }, ["identical to current values"]));
      return;
    }
    for (const [name, vals] of changes) {
      diffBox.appendChild(el("div", { class: "diff-row" }, [
        el("span", { class: "diff-row__name" }, [name]),
        el("span", { class: "diff-row__vals" }, [`${vals.current} \u2192 ${vals.saved}`]),
      ]));
    }
  }

  ctx.ws.onConfigList((msg) => renderList(msg.names || []));
  ctx.ws.onConfigDiff((msg) => renderDiff(msg));
  ctx.ws.onConfigLoaded(() => ctx.ws.getAll());
  ctx.ws.onConnect(() => ctx.ws.listConfigs());

  container.appendChild(el("div", { class: "panel panel--config" }, [
    el("h2", { class: "panel__title" }, ["Configurations"]),
    el("div", { class: "field field--full" }, [nameInput]),
    el("div", { class: "btn-row" }, [saveBtn]),
    list,
    diffBox,
  ]));

  return {
    update(state) {
      saveBtn.disabled = ctx.role() !== "control";
      nameInput.disabled = ctx.role() !== "control";
    },
  };
}
