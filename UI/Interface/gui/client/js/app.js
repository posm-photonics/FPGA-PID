// gui/client/js/app.js
//
// Entry point (docs/10_gui_implementation_plan.md, section 3): boots
// ws_client, mounts each panel. Panels never talk to the WebSocket
// directly -- they get a `ctx` object (ws client, current role, and
// their own `update(state)` call whenever a fresh snapshot arrives).

import { WsClient } from "./ws_client.js";
import { el } from "./fields.js";

import * as StatusPanel from "./panels/status_panel.js";
import * as PiPanel from "./panels/pi_panel.js";
import * as ScanPanel from "./panels/scan_panel.js";
import * as AutolockPanel from "./panels/autolock_panel.js";
import * as ScopePanel from "./panels/scope_panel.js";
import * as ConfigPanel from "./panels/config_panel.js";
import * as EventLogPanel from "./panels/event_log_panel.js";
import * as SystemPanel from "./panels/system_panel.js";

const proto = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WsClient(`${proto}//${location.host}/ws`);

const connBadge = document.getElementById("badge-connection");
const roleBadge = document.getElementById("badge-role");
const simBadge = document.getElementById("badge-sim");
const errorToast = document.getElementById("error-toast");

let currentState = {};

const ctx = {
  ws,
  role: () => ws.role,
  state: () => currentState,
};

function applyToAllPanels(state) {
  currentState = { ...currentState, ...state };
  for (const p of mountedPanels) p.update(currentState);
}

const mountedPanels = [];

function mountPanel(mod, containerId) {
  const container = document.getElementById(containerId);
  const instance = mod.mount(container, ctx);
  mountedPanels.push(instance);
}

// ---- layout: three columns, panels grouped by what a lab session
// actually touches together (status/config on the left, the scope in
// the middle since it's what people stare at, tuning controls on the
// right). ----
mountPanel(StatusPanel, "col-left");
mountPanel(SystemPanel, "col-left");
mountPanel(EventLogPanel, "col-left");

mountPanel(ScopePanel, "col-center");
mountPanel(ScanPanel, "col-center");

mountPanel(PiPanel, "col-right");
mountPanel(AutolockPanel, "col-right");
mountPanel(ConfigPanel, "col-right");

ws.onHello((info) => {
  roleBadge.textContent = info.role === "control" ? "control" : "viewer (read-only)";
  roleBadge.className = "badge " + (info.role === "control" ? "badge--role-control" : "badge--role-viewer");
  simBadge.style.display = info.sim_mode ? "inline-flex" : "none";
  applyToAllPanels(currentState); // refresh disabled states now that role is known
});

ws.onConnect(() => {
  connBadge.textContent = "connected";
  connBadge.className = "badge badge--connected";
  ws.subscribe("status");
  ws.getAll();
});

ws.onDisconnect(() => {
  connBadge.textContent = "disconnected -- reconnecting";
  connBadge.className = "badge badge--disconnected";
});

ws.onSnapshot((values) => applyToAllPanels(values));
ws.onValue((msg) => applyToAllPanels({ [msg.param]: msg.value }));

ws.onError((msg) => {
  errorToast.textContent = msg.detail;
  errorToast.style.display = "block";
  clearTimeout(errorToast._hideTimer);
  errorToast._hideTimer = setTimeout(() => { errorToast.style.display = "none"; }, 4000);
});

ws.connect();
