// gui/client/js/ws_client.js
//
// Wraps the raw WebSocket connection to posm_server.py (see
// gui/server/protocol.py for the message schema). Handles connecting,
// reconnecting with backoff if the link drops, and a simple
// request/response + subscribe pattern on top of the socket, so panel
// code (js/panels/*.js) never touches WebSocket frames directly.
//
// Usage:
//   const ws = new WsClient(`ws://${location.host}/ws`);
//   ws.onSnapshot(values => { ...update every panel... });
//   ws.onScopeFrame(frame => { ...update the scope canvas... });
//   ws.onEvent(evt => { ...append to the event log... });
//   ws.onHello(info => { ...show role / sim banner... });
//   ws.connect();
//   ws.set("p_gain", 0.8);

export class WsClient {
  constructor(url) {
    this.url = url;
    this.socket = null;
    this.role = "viewer";
    this.simMode = false;
    this.connected = false;
    this._reconnectDelay = 500;
    this._maxReconnectDelay = 8000;
    this._listeners = { snapshot: [], value: [], scope_frame: [], event: [],
                         hello: [], error: [], connect: [], disconnect: [],
                         config_diff: [], config_list: [], config_loaded: [] };
    this._desiredRole = "control";
    this._desiredName = "POSM GUI";
    this._subscriptions = new Set();
  }

  connect() {
    this.socket = new WebSocket(this.url);
    this.socket.addEventListener("open", () => {
      this.connected = true;
      this._reconnectDelay = 500;
      this._send({ type: "hello", role: this._desiredRole, name: this._desiredName });
      for (const stream of this._subscriptions) {
        this._send({ type: "subscribe", stream });
      }
      this._emit("connect", {});
    });
    this.socket.addEventListener("message", (ev) => this._onMessage(ev));
    this.socket.addEventListener("close", () => {
      this.connected = false;
      this._emit("disconnect", {});
      this._scheduleReconnect();
    });
    this.socket.addEventListener("error", () => {
      try { this.socket.close(); } catch (e) { /* ignore */ }
    });
  }

  _scheduleReconnect() {
    setTimeout(() => this.connect(), this._reconnectDelay);
    this._reconnectDelay = Math.min(this._reconnectDelay * 1.6, this._maxReconnectDelay);
  }

  _send(obj) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  _onMessage(ev) {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    switch (msg.type) {
      case "hello_ack":
        this.role = msg.role;
        this.simMode = !!msg.sim_mode;
        this._emit("hello", msg);
        break;
      case "snapshot":
        this._emit("snapshot", msg.values);
        break;
      case "value":
        this._emit("value", msg);
        break;
      case "scope_frame":
        this._emit("scope_frame", msg);
        break;
      case "event":
        this._emit("event", msg);
        break;
      case "config_diff":
        this._emit("config_diff", msg);
        break;
      case "config_list":
        this._emit("config_list", msg);
        break;
      case "config_loaded":
        this._emit("config_loaded", msg);
        break;
      case "error":
        this._emit("error", msg);
        break;
      default:
        break;
    }
  }

  _emit(type, payload) {
    for (const fn of this._listeners[type] || []) fn(payload);
  }

  // ---- subscription helpers ----
  onSnapshot(fn) { this._listeners.snapshot.push(fn); }
  onValue(fn) { this._listeners.value.push(fn); }
  onScopeFrame(fn) { this._listeners.scope_frame.push(fn); }
  onEvent(fn) { this._listeners.event.push(fn); }
  onHello(fn) { this._listeners.hello.push(fn); }
  onError(fn) { this._listeners.error.push(fn); }
  onConnect(fn) { this._listeners.connect.push(fn); }
  onDisconnect(fn) { this._listeners.disconnect.push(fn); }
  onConfigDiff(fn) { this._listeners.config_diff.push(fn); }
  onConfigList(fn) { this._listeners.config_list.push(fn); }
  onConfigLoaded(fn) { this._listeners.config_loaded.push(fn); }

  // ---- requests ----
  requestRole(role, name) {
    this._desiredRole = role;
    this._desiredName = name || this._desiredName;
    this._send({ type: "hello", role, name: this._desiredName });
  }

  get(param) { this._send({ type: "get", param }); }
  getAll() { this._send({ type: "get_all" }); }

  set(param, value) { this._send({ type: "set", param, value }); }

  subscribe(stream) {
    this._subscriptions.add(stream);
    this._send({ type: "subscribe", stream });
  }
  unsubscribe(stream) {
    this._subscriptions.delete(stream);
    this._send({ type: "unsubscribe", stream });
  }

  clearFaults() { this._send({ type: "clear_faults" }); }
  captureTrace() { this._send({ type: "trace_capture" }); }
  saveConfig(name) { this._send({ type: "save_config", name }); }
  loadConfig(name) { this._send({ type: "load_config", name }); }
  diffConfig(name) { this._send({ type: "diff_config", name }); }
  listConfigs() { this._send({ type: "list_configs" }); }
}
