# POSM GUI

Browser-based control/monitoring UI for the FPGA MTS lock core, per
`docs/10_gui_implementation_plan.md`. No install on the client --
open a URL. One Python process (`gui/server/posm_server.py`) serves
both the WebSocket API and the static browser client.

## Quick start (simulation, no hardware, works right now)

```bash
pip install amaranth --break-system-packages   # only needed for --mock
python3 run_gui.py --mock --port 8000
```

Open `http://localhost:8000/` in a browser. You'll see a `SIMULATED`
badge in the top bar. This mode runs the **actual `LockCoreTop` RTL**
(`top/lock_core_top.py`) inside Amaranth's simulator, closed-loop
against `sim/models/fake_laser_plant.py` -- not a hand-rolled
approximation. Every panel is driving the same PICore / RampScan /
RobustAutoLock / LockFSM / FaultGate logic that would run on the FPGA.

To actually get something scanning, set (in the GUI, or you'll want to
save this as a named config once you've got numbers you like):
`global_enable`, `lock_enable_request`, `autolock_enable`,
`slow_recenter_config_enable`, reasonable `ramp_min`/`ramp_max`/
`ramp_step`/`ramp_tick_div`, and `fault_enable_mask = 0` while you're
first poking at it (leave the real fault mask on before trusting it
near real hardware).

## Real hardware

```bash
sudo python3 run_gui.py --base 0x40600000 --port 8000
```

`--base` must match wherever `red_pitaya_lock_core` is actually wired
onto the Red Pitaya's sys-bus, same requirement `build/posm_reg_server.py`
already documented -- there's no safe default to guess.

### Read this before pointing the GUI at real hardware

Three parameter groups -- **PI gains (`p_gain`/`i_gain`), scan bounds
(`ramp_*`), and autolock feature-detection (`autolock_*`)** -- point at
register addresses (`0x020`-`0x070`) that **did not exist in the
register map until this GUI project added them**. Before this change,
`register_bank.py` declared the underlying Amaranth `Signal`s and
`lock_core_top.py` already wired them into the datapath, but nothing
ever decoded a bus address onto them -- they were frozen at their
reset values forever, and no software (not even the old
`Posm_Dashboard.html` sanity dashboard) could move them. That gap is
now closed in `rtl/bus/register_defs.py` / `rtl/bus/register_bank.py`,
verified against the Amaranth simulator, but **any board you've
already built needs its bitstream regenerated and reflashed** before
those particular GUI panels do anything on real silicon. Until then,
writes to those addresses on old hardware are silently ignored (reads
back whatever the RTL's `reset=` value was).

The global control/status block (`0x000`-`0x01C`), slow-recenter
(`0x100`-`0x124`), and trace-capture (`0x180`-`0x1A0`) blocks were
already real, addressable hardware registers before this project and
need no bitstream change.

### A second bug fixed while building the scope panel

`trace_capture.sample_valid` was wired to `ramp_scan.cycle_done` in
`lock_core_top.py`, which only pulses once per **complete scan sweep**
-- a 128-sample trace buffer would've taken 128 full sweeps to fill.
Added a `step_tick` output to `ramp_scan.py` (fires once per actual
ramp step) and rewired `trace_capture.sample_valid` to use it instead.
Also needs a bitstream rebuild on any hardware already built from the
old RTL.

## Layout

```
gui/
  server/
    parameters.py      # name -> register address/scale/range -- the only
                        # place that knows what a raw register means
    protocol.py         # WebSocket message schema (documented, read this
                        # before adding a message type)
    posm_server.py       # the daemon: WebSocket + static file server
    mock_backend.py      # Amaranth-simulated register backend (--mock)
    hw_backend.py         # real /dev/mem register backend
    scope_streamer.py     # polls trace_capture, backend-agnostic
    configs/              # saved named configs (created on first save)
  client/
    index.html
    css/style.css
    js/
      ws_client.js         # WebSocket wrapper, reconnect, request/response
      fields.js             # slider/checkbox/numberField/readout builders
      app.js                 # boots everything, owns shared state
      panels/
        status_panel.js       # lock_fsm / fault_gate / lock_watch
        system_panel.js        # global/outputs enable, test patterns
                                # (successor to scripts/Posm_Dashboard.html)
        pi_panel.js              # pi_controller
        scan_panel.js             # ramp_scan
        autolock_panel.js          # robust_autolock
        scope_panel.js              # trace_capture, live canvas plot
        config_panel.js              # named save/load/diff
        event_log_panel.js            # fault/lock timeline
```

## What's deliberately not here yet

- **`pdh_panel.js`** -- the build order in
  `docs/10_gui_implementation_plan.md` section 5 says not to build this
  until the demodulation plan (`docs/09_demodulation_pdh_plan.md`) is
  actually implemented. That doc doesn't exist in this repo yet (docs
  01-07 are stubs), so there's nothing to wire the panel to.
- **Multi-client role UI polish** -- the server enforces `control` vs
  `viewer` (only one `control` connection at a time by default, `--multi-control`
  to allow more), and the client greys out inputs it can't use, but
  there's no UI for a `control` holder to explicitly hand off control.
- **Real per-client auth** -- roles are assigned on a first-come basis,
  not authenticated. Fine on a trusted lab network; don't expose this
  server past it.

## A note on how this was verified

The server/backend/RTL path was tested live end-to-end (register
read/write round trips, config save/load/diff, and a real trace
capture producing an actual waveform) via a raw WebSocket client
against `--mock`. The browser client (`gui/client/`) was verified by
static analysis only -- every JS file passes a syntax check and was
traced by hand against `ws_client.js`'s actual method names -- but was
not exercised in a real browser (none was available in the environment
this was built in). Do a quick look in an actual browser before
trusting it blind, particularly the scope canvas rendering.
