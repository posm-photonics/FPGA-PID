# 05 Register map

Single source of truth for addresses: `rtl/bus/register_defs.py`. This
document is maintained by hand alongside it; if you change one, change
both. All addresses are byte offsets. All registers are 32 bits. Signed
values are two's complement.

> This file previously contained three `*(Existing documentation remains
> here...)*` placeholders where the global, slow and trace tables should
> have been. The pre-ship audit flagged it against packet
> acceptance-checklist item 3 ("Register map exists in both
> documentation and register_defs"). The tables are written out below.

---

## Divergence from the canonical map: read this first

`POSM_project_FPGALock.pdf` section 11 defines a canonical register map.
This repository does **not** match it for 21 registers, and the
divergence is not cosmetic.

| Register | Packet section 11 | This repo |
|---|---|---|
| `FAST_KP` / `FAST_KI` | 0x0C4 / 0x0C8 | 0x020 / 0x024 |
| `FAST_OUT_MIN` / `MAX` / `SAFE` | 0x0DC / 0x0E0 / 0x0E4 | 0x028 / 0x02C / 0x030 |
| `RAMP_MIN` … `RAMP_WIDTH` | 0x144 … 0x158 | 0x034 … 0x048 |
| `AUTOLOCK_WINDOW_MIN` … `RETRY_LIMIT` | 0x1C4 … 0x1EC | 0x04C … 0x070 |
| `LOCK_MAX_ERROR` | 0x22C | also mirrored at 0x078 as `LOCK_ERROR_MAX` |

The relocated block occupies **0x020–0x070**. Packet section 11.2
reserves **0x040–0x068** for ADC configuration, so eleven of those
addresses collide with a block the packet defines and this repo has not
implemented. That is why the ADC registers added during the audit are
parked at 0x0A0 instead of their canonical 0x040.

`rtl/bus/register_defs.py` claims in its own docstring to match section
11 and to be "the single source of truth so docs, register_bank,
trace_capture and slow_recenter never drift apart." That claim is false
for these 21 registers. `UI/Interface/gui/server/parameters.py` records
how it happened: the GUI project invented addresses in 0x020–0x070
rather than using the canonical map.

**This still needs a decision.** Either move the block back to canonical
addresses (which invalidates every existing bitstream and the GUI at
once) or amend packet section 11. Do not add further registers in
0x040–0x068 until it is resolved.

---

## 11.1 Global control / status (0x000–0x01C)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x000` | `VERSION` | R | Build/version ID. Currently `0x0003_0000`. |
| `0x004` | `CONTROL` | R/W | Global enable, reset, output enable, lock request, trace/autolock enable. |
| `0x008` | `STATUS` | R | State, locked, scanning, saturation, trace ready, fault active. |
| `0x00C` | `MODE` | R/W | Passthrough mode word. **Not consumed by any logic.** |
| `0x010` | `FAULT_STATUS` | R | Sticky fault flags. |
| `0x014` | `FAULT_ENABLE` | R/W | Mask deciding which faults latch. |
| `0x018` | `FAULT_CLEAR` | W | Write-one-to-clear selected sticky faults. |
| `0x01C` | `DEBUG_SELECT` | R/W | Select internal debug signal. **Not consumed by any logic.** |

### CONTROL bits

| Bit | Name | Wired? | Meaning |
|---|---|---|---|
| 0 | `global_enable` | yes | Enables non-fault operation. Returns the FSM to IDLE when cleared. |
| 1 | `soft_reset` | **no** | Self-clearing pulse bit. Nothing acts on it. |
| 2 | `outputs_enable` | yes | Allows DAC outputs to leave the safe code. |
| 3 | `lock_enable_request` | yes | Requests lock engagement. |
| 4 | `hold_request` | yes | Requests hold mode. |
| 5 | `fault_clear_request` | yes | Self-clearing pulse; clears the FAULT state. |
| 6 | `integrator_reset` | yes | Resets the PI integrator. |
| 7 | `integrator_load` | yes | Loads PI state for a smooth handoff. |
| 8 | `trace_capture_enable` | yes | Enables trace capture. |
| 9 | `autolock_enable` | yes | Enables robust autolock. |
| 10 | `slow_recenter_enable` | **no** | Use `SLOW_CTRL_CONFIG` bit 0 instead. |
| 11 | `adc_test_pattern_enable` | **no** | Not implemented. |
| 12 | `dac_test_pattern_enable` | **no** | Not implemented. |

Bits marked **no** are decoded by the register bank and connected to
nothing. Before the audit there were nine such bits, including
`outputs_enable`, the master output enable. Four remain: either
implement them or remove them from the map and the GUI. A control that
silently does nothing is worse than an absent one.

### STATUS bits

| Bits | Name |
|---|---|
| 3:0 | `state` (see `LockState` in `rtl/control/lock_fsm.py`) |
| 4 | `locked` (asserted in LOCKED **and** LOCK_WATCH) |
| 5 | `scanning` |
| 6 | `saturation` |
| 7 | `trace_ready` |
| 8 | `fault_active` |

`locked` covers both steady lock states deliberately. LOCKED is a
single-cycle state, so a status bit tied to it alone was high for
exactly one clock in the lifetime of a lock and no host could ever
observe it.

### FAULT_STATUS bits

| Bit | Source | Forces a fault? |
|---|---|---|
| 0 | ADC ch0 overrange | yes |
| 1 | ADC ch1 overrange | yes |
| 2 | ADC ch0 stuck | no, diagnostic only |
| 3 | ADC ch1 stuck | no, diagnostic only |
| 4 | Missing ADC valid | yes |
| 5 | Lock-watch fault request | yes |
| 6 | reserved | |
| 7 | External interlock | yes |
| 8 | Acquisition timeout | via the FSM |
| 9 | Relock attempts exhausted | via the FSM |
| 11:10 | reserved | |

The stuck flags are deliberately diagnostic-only. Stuck detection is a
heuristic, and a static ADC reading is only suspicious if the input is
supposed to be moving. Here it often is not: while the FSM waits between
scans the ramp parks and the error signal is legitimately constant.
Routing the heuristic into the fault path made the system fault during
normal acquisition with a perfectly healthy ADC.

---

## Fast loop / PI controller (0x020–0x030, plus 0x0CC)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x020` | `FAST_KP` | R/W | Signed Q3.14 proportional gain. |
| `0x024` | `FAST_KI` | R/W | Signed Q3.14 integral gain. |
| `0x028` | `FAST_OUT_MIN` | R/W | Signed lower clamp, default −3200. |
| `0x02C` | `FAST_OUT_MAX` | R/W | Signed upper clamp, default +3200. |
| `0x030` | `FAST_OUT_SAFE` | R/W | Safe output while faulted or disabled. |
| `0x0CC` | `FAST_INT_LEAK` | R/W | Leaky-integrator shift, 0 = no leak. |

`FAST_INT_LEAK` matters on this hardware: per packet 3.4 the CTL200 AC
modulation input cannot carry DC authority, so a pure accumulator winds
up against an actuator that physically cannot respond.

---

## Ramp / scan (0x034–0x048)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x034` | `RAMP_MIN` | R/W | Wide-scan lower bound, default −3200. |
| `0x038` | `RAMP_MAX` | R/W | Wide-scan upper bound, default +3200. |
| `0x03C` | `RAMP_STEP` | R/W | Step per tick. 0 is clamped to 1. |
| `0x040` | `RAMP_TICK_DIV` | R/W | Cycles between ramp steps. |
| `0x044` | `RAMP_CENTER` | R/W | Zoom-scan centre. |
| `0x048` | `RAMP_WIDTH` | R/W | Zoom-scan half-width. |

Both wide-scan defaults are symmetric. The earlier "conservative safe
default" was applied to `RAMP_MIN` only, leaving `RAMP_MAX` at full
positive scale, so an unconfigured scan still slammed the slow DAC to
+1 V on first enable.

Zoom bounds are computed with a guard bit and **clamped**, not
truncated. Truncating let `active_max` fall below `active_min`, which
turned the scan into a full-amplitude square wave on the piezo.

---

## Autolock descriptor (0x04C–0x070)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x04C` | `AUTOLOCK_WINDOW_MIN` | R/W | Signed scan-window lower bound. |
| `0x050` | `AUTOLOCK_WINDOW_MAX` | R/W | Signed scan-window upper bound. |
| `0x054` | `AUTOLOCK_EXPECTED_MIN_X` | R/W | **Not consulted.** See note. |
| `0x058` | `AUTOLOCK_EXPECTED_MAX_X` | R/W | **Not consulted.** See note. |
| `0x05C` | `AUTOLOCK_LOCK_X` | R/W | Expected zero-crossing position. |
| `0x060` | `AUTOLOCK_AMP_MIN` | R/W | Signed minimum feature amplitude. |
| `0x064` | `AUTOLOCK_WIDTH_MIN` | R/W | Minimum acceptable width. |
| `0x068` | `AUTOLOCK_WIDTH_MAX` | R/W | Maximum acceptable width. |
| `0x06C` | `AUTOLOCK_SLOPE_SIGN` | R/W | Expected slope sign. |
| `0x070` | `AUTOLOCK_RETRY_LIMIT` | R/W | Retries before failing. |

All scan-position fields are **signed**. They are compared against
`ramp_scan.ramp_out`, which is `signed(16)`; as unsigned they broke for
every negative scan position, and the default `ramp_min` is negative.

The lock position handed to the slow path is the zero crossing the FPGA
**measured**, not `AUTOLOCK_LOCK_X`. The measured crossing used to be
tracked and then discarded, which defeated the point of FPGA-side
verification.

`EXPECTED_MIN_X` / `EXPECTED_MAX_X` remain unused by
`robust_autolock.py`. Using them needs a position-tolerance field the
map does not define. Either add one per packet 11.8 or remove these two.

---

## Error calculation (0x074–0x078, 0x080–0x084)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x074` | `ERROR_CONFIG` | R/W | Bit 0: invert error (slope sign). |
| `0x078` | `LOCK_ERROR_MAX` | R/W | Legacy mirror of `LOCK_MAX_ERROR`. |
| `0x080` | `ERROR_SETPOINT` | R/W | Signed 20-bit desired lock error. |
| `0x084` | `ERROR_OFFSET` | R/W | Signed 20-bit DC/background offset. |

`ERROR_OFFSET` implements packet 4.3 Eq. 13,
`e[n] = p(x[n] − offset − setpoint)`. Both inputs were previously
hardwired to zero, so the zero crossing was not at zero error and the
servo held the wrong point.

---

## ADC configuration (0x0A0–0x0A4)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x0A0` | `ADC_CONFIG` | R/W | Bit 0: format mode. Bit 1: ADC faults may force a fault. |
| `0x0A4` | `ADC_GUARD_COUNT` | R/W | Unchanged samples before the stuck flag, default 4096. |

Parked at 0x0A0 rather than the canonical 0x040 because of the address
collision described at the top of this file.

---

## 11.5 Slow controller / recentering (0x100–0x124)

Owned by `rtl/control/slow_recenter.py`, decoded on the shared bus.

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x100` | `SLOW_CTRL_CONFIG` | R/W | See bit layout below. |
| `0x104` | `SLOW_BIAS` | R/W | DC bias / centre code. |
| `0x108` | `SLOW_KI` | R/W | Reserved; not consumed. |
| `0x10C` | `SLOW_RECENTER_TARGET` | R/W | Desired fast-DAC centre code. |
| `0x110` | `SLOW_RECENTER_GAIN` | R/W | Signed Q(12) gain `Ks`. |
| `0x114` | `SLOW_OUT_MIN` | R/W | Hard clamp low, default −3200. |
| `0x118` | `SLOW_OUT_MAX` | R/W | Hard clamp high, default +3200. |
| `0x11C` | `SLOW_OUT_SAFE` | R/W | Signed safe command while faulted. |
| `0x120` | `SLOW_SLEW_LIMIT` | R/W | Unsigned max delta per slow tick. |
| `0x124` | `SLOW_OUT_CURRENT` | R/W\* | Current slow command. |

`SLOW_CTRL_CONFIG` bits: 0 recenter enable, 1 hold, 2 accumulator reset,
3 accumulator load, 15:8 tick-divider shift (default 12, about 33 µs at
125 MHz). A shift of 0 makes the slow loop run at the full sample rate,
which packet 8.11 forbids; the default is deliberately slow.

`SLOW_OUT_SAFE` is in **signed controller units**, not DAC code space,
so 0 always means mid-scale regardless of the DAC encoding. Previously
it was an unsigned DAC code, which meant the same register value parked
the actuator at mid-scale in two's complement and at negative full scale
in offset binary.

`SLOW_SLEW_LIMIT` is an unsigned magnitude. It used to be reinterpreted
as signed internally, so any value at or above 32768 became negative and
inverted the sign of the slow correction, turning that loop into
positive feedback.

\* `SLOW_OUT_CURRENT` reads as the current command. Writing it loads the
accumulator, but **only** when `SLOW_CTRL_CONFIG` bit 3 is set.
Previously the write was unconditional on a register documented
read-only, so any read-modify-write sweep over the register block
silently clobbered the slow actuator state.

---

## 11.7 Trace capture (0x180–0x1A0)

Owned by `rtl/control/trace_capture.py`.

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x180` | `TRACE_CONFIG` | R/W | Bit 0 enable, bit 1 channel select. |
| `0x184` | `TRACE_START` | W | Write-any to arm and start a capture. |
| `0x188` | `TRACE_LENGTH` | R/W | Pairs to capture, clipped to the buffer depth. |
| `0x18C` | `TRACE_DECIM` | R/W | Capture every Nth valid sample. 0 is clamped to 1. |
| `0x190` | `TRACE_STATUS` | R | Bit 0 busy, bit 1 ready, bit 2 overflow. |
| `0x194` | `TRACE_WRITE_PTR` | R | Current write pointer. |
| `0x198` | `TRACE_READ_ADDR` | R/W | Host readback address. |
| `0x19C` | `TRACE_READ_DATA_X` | R | Scan code at `TRACE_READ_ADDR`. |
| `0x1A0` | `TRACE_READ_DATA_Y` | R | Error sample at `TRACE_READ_ADDR`. |

The capture strobe is the ramp tick, one sample per ramp step. It was
previously the completed-sweep pulse, so the buffer filled at one point
per entire scan: 11 samples in 3.2 ms, with `trace_ready` never
asserting and the FSM stuck in WIDE_SCAN.

A `TRACE_LENGTH` of 0 still reports ready after a single sample. Set a
real length before starting a capture.

---

## PDH subsystem (0x200–0x210)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x200` | `PDH_CONTROL` | R/W | Bit 0: 1 = PDH demodulation, 0 = direct ADC. |
| `0x204` | `PDH_MOD_FREQ` | R/W | 32-bit NCO phase increment per clock. |
| `0x208` | `PDH_MOD_AMP` | R/W | 16-bit Q2.14 modulation amplitude. Saturating. |
| `0x20C` | `PDH_DEMOD_PHASE` | R/W | 32-bit phase offset, applied to the mixer reference **only**. |
| `0x210` | `PDH_LPF_ALPHA` | R/W | 5-bit IIR bandwidth shift. |

`PDH_DEMOD_PHASE` previously fed the shared NCO that drove both the
modulation output and the mixer reference, so their relative phase was
pinned at zero and the register did nothing at all. It now rotates the
mixer reference alone, which is what packet 5.1 specifies (EOM drive at
phase 0, mixer LO at `phi_demod`) and what Linien does with its `delay`
CSR inside `Demodulate`.

Two open items on this block: the PDH path sits in the fast path, which
packet 7.2 forbids for the required v1 lock, and `o_dac_mod` has no
physical destination on a two-DAC board.

---

## Lock check / lock watch (0x224–0x254)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x224` | `LOCK_CHECK_DELAY` | R/W | Samples the quality condition must hold before lock is declared. |
| `0x22C` | `LOCK_MAX_ERROR` | R/W | Max abs error while locked, default 4096. |
| `0x234` | `LOCK_MAX_SAT_COUNT` | R/W | Saturation samples before a fault, default 1250000 (10 ms). |
| `0x240` | `LOCK_ADC_TIMEOUT` | R/W | Missing-valid samples before an ADC fault. |
| `0x244` | `LOCK_ERROR_TIMEOUT` | R/W | Excess-error samples before a relock. |
| `0x248` | `LOCK_JUMP_LIMIT` | R/W | Max fast-output change per jump window. |
| `0x24C` | `LOCK_JUMP_WINDOW` | R/W | log2 of the jump window, default 8 (256 samples). |
| `0x250` | `LOCK_STATE_TIMEOUT` | R/W | Cycles an acquisition state may wait. 0 disables. |
| `0x254` | `LOCK_RELOCK_LIMIT` | R/W | Relock attempts before a fault. 0 disables. |

Every one of these was a hardcoded constant before the audit. The old
saturation timeout was 100 cycles, 800 ns at 125 MHz, short enough that
ordinary acquisition transients tripped it into a fault that could not
then be cleared.

`LOCK_CHECK_DELAY` implements packet 9.2 step 8: "FPGA waits a
configured delay and checks error/output metrics." Without it the lock
check was a single combinational sample, so one noisy sample dropped the
lock.

`LOCK_JUMP_WINDOW` exists because the jump detector previously compared
the fast output against its value on the immediately preceding clock. At
125 MHz a servo responding to a real disturbance moves further than any
sane limit in one cycle, so it fired constantly.

---

## DAC configuration (0x260)

| Address | Name | R/W | Description |
|---|---|---|---|
| `0x260` | `DAC_CONFIG` | R/W | Bit 0: fast DAC offset binary. Bit 1: slow DAC offset binary. |

Both default to 0 (two's complement), which is what the Red Pitaya board
wrapper expects after its own sign/invert conversion.

---

## Still missing against the canonical map

Listed so the gap stays visible rather than silent:

- **11.2 ADC configuration** — per-channel offset, gain and valid
  thresholds. `adc_guard.py` has no bounds check, so packet 8.3's "too
  many consecutive samples outside configured bounds" is not detected.
- **11.4 Fast controller** — `FAST_CTRL_CONFIG`, `FAST_KI_LOCAL`,
  compensator coefficients, `FAST_SLEW_LIMIT`, `FAST_OUT_CURRENT`.
- **11.3 Error diagnostics** — `ERROR_THRESH_LOCKED`,
  `ERROR_THRESH_UNLOCK`, `ERROR_RMS_WINDOW`, `ERROR_CURRENT`,
  `ERROR_ABS_MAX`.
- **11.6** — `RAMP_CONFIG`, `RAMP_CURRENT`, `RAMP_CYCLE_COUNT`
  (`ramp_scan` produces a cycle count that no register exposes).
- **11.8** — `AUTOLOCK_CONFIG`, `AUTOLOCK_Y_OFFSET`, `AUTOLOCK_STATUS`.
- **11.10** — `DAC_ROUTE`, clamp mirrors, test-pattern codes.
- **11.11 Latency instrumentation** — the whole block, plus
  `latency_probe.sv`. Packet 7.3 requires every fast-path module to
  declare its valid-in to valid-out latency.
