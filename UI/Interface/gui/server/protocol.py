"""
gui/server/protocol.py

The message schema for the browser <-> posm_server.py WebSocket
connection. Kept small and boring on purpose (docs/10_gui_implementation_plan.md,
section 2): every message is a flat JSON object with a "type" field.
This docstring is the contract -- if you add or change a message type,
update it here first, then update js/ws_client.js and posm_server.py
to match. The GUI never sends or receives a raw register address, only
parameter names from gui/server/parameters.py.

--------------------------------------------------------------------
CLIENT -> SERVER
--------------------------------------------------------------------

hello
    First message a client should send after connecting.
    { "type": "hello", "role": "control" | "viewer", "name": "optional label" }
    Server replies with "hello_ack". If role is omitted, "viewer" is
    assumed. The server may downgrade "control" to "viewer" if another
    client already holds control and the server is configured
    single-control (see posm_server.py --single-control).

get
    Read a single parameter's current value.
    { "type": "get", "param": "p_gain" }
    -> "value" response.

get_all
    Read every known parameter at once (used on initial page load).
    { "type": "get_all" }
    -> "snapshot" response.

set
    Write a parameter. Only accepted from a "control" connection;
    "viewer" connections get an "error" response instead.
    { "type": "set", "param": "p_gain", "value": 0.8 }
    -> "value" response (echoes the new value actually read back).

subscribe / unsubscribe
    Subscribe to a push stream. Streams: "status" (fast poll of the
    system/status/fault group, a few Hz), "scope" (waveform frames
    whenever a new trace capture completes), "events" (fault/relock
    timeline entries as they happen).
    { "type": "subscribe", "stream": "scope" }
    { "type": "unsubscribe", "stream": "scope" }

trace_capture
    Convenience command: arm+start one trace capture and stream the
    result back as a single "scope_frame" once ready. Equivalent to
    setting trace_start then polling trace_status_ready, but saves the
    client a round trip.
    { "type": "trace_capture" }

save_config / load_config / diff_config / list_configs
    Named configuration management (docs/10_gui_implementation_plan.md
    section 4c). Configs are the full writable-parameter set, stored
    as JSON on the server (server-local disk on the real board; an
    in-memory dict for mock_backend / non-persistent setups).
    { "type": "save_config", "name": "nightly-mts" }
    { "type": "load_config", "name": "nightly-mts" }       -> "value" per changed param, then "config_loaded"
    { "type": "diff_config", "name": "nightly-mts" }        -> "config_diff"
    { "type": "list_configs" }                               -> "config_list"

clear_faults
    Convenience command, equivalent to writing clear_all_faults.
    { "type": "clear_faults" }

--------------------------------------------------------------------
SERVER -> CLIENT
--------------------------------------------------------------------

hello_ack
    { "type": "hello_ack", "role": "control" | "viewer", "sim_mode": true|false,
      "client_id": "c3", "server_version": "0.1.0" }

value
    Response to "get" or "set", and also broadcast to a param's
    subscribers if you're subscribed to "status".
    { "type": "value", "param": "p_gain", "value": 0.8 }

snapshot
    Response to "get_all". A flat {name: value} map of every parameter
    in gui/server/parameters.py.
    { "type": "snapshot", "values": { "p_gain": 0.8, "locked": false, ... } }

scope_frame
    Pushed to "scope" subscribers whenever a trace capture completes.
    x/y are parallel arrays already converted to engineering units.
    { "type": "scope_frame", "x": [...], "y": [...], "channel": "error" | "ch1" }

event
    Pushed to "events" subscribers. One entry per fault trip, relock
    attempt, or drift-correction event (docs/10_gui_implementation_plan.md
    section 4d).
    { "type": "event", "ts": 1234567.89, "kind": "fault_tripped",
      "detail": "adc_ch0_overrange" }

config_loaded
    { "type": "config_loaded", "name": "nightly-mts" }

config_diff
    Old/new per changed parameter only, so the client can render a
    diff-before-apply view (section 4c) without applying anything.
    { "type": "config_diff", "name": "nightly-mts",
      "changes": { "p_gain": {"current": 0.5, "saved": 0.8} } }

config_list
    { "type": "config_list", "names": ["nightly-mts", "daytime"] }

error
    { "type": "error", "detail": "human-readable message", "in_reply_to": "set" }

--------------------------------------------------------------------
"""

# Stream names valid for subscribe/unsubscribe.
STREAMS = ("status", "scope", "events")

# Roles a connection can hold.
ROLES = ("control", "viewer")

# How often the "status" stream pushes a fresh snapshot, in seconds.
STATUS_PUSH_INTERVAL_S = 0.25
