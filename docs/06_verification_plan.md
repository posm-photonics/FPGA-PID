# 06 Verification plan

This file was empty. Packet section 12 defines the plan; this records
what actually exists, what it proves, and what is still missing.

---

## How to run everything

```bash
pip install -r docs/Requirements.txt

# Every testbench
for f in $(find sim -name 'tb_*.py'); do
    echo "== $f"; python3 "$f" || echo "FAILED: $f"
done

# Full-system integration
python3 sim/tb_lock_core_top.py

# Closed-loop demo with plots and CSV (packet 12.3)
python3 sim/run_closed_loop_demo.py

# Architecture comparison harness
python3 run_bench.py

# Elaboration must succeed before any of this means anything
python3 build/generate_verilog.py
```

There is **no CI**. Adding it is the single highest-value process change
available: a red test was committed and shipped
(`tb_demod_lowpass.py` failed with "Expected 10000, got 9973"), and the
only integration testbench could not run at all for long enough that its
failure got written into `walkthrough.md` as a known quirk.

---

## Module-level tests (packet 12.4)

| Testbench | Covers | State |
|---|---|---|
| `tb_common/tb_sat_math.py` | Saturating helpers | passing |
| `tb_adc/tb_adc_formatter.py` | Coding conversion, sign extension, boundaries | passing |
| `tb_adc/tb_adc_guard.py` | Overrange, stuck rail, missing valid, fault count | passing |
| `tb_adc/tb_adc_frontend_top.py` | ADC ingestion chain | passing |
| `tb_dsp/tb_error_calc.py` | Offset, setpoint, polarity, valid latency | passing |
| `tb_dsp/tb_pi_controller.py` | P-only, I-only, PI, clamp, anti-windup, hold, reset, load | passing (26 cases) |
| `tb_dsp/tb_nco.py` | Amplitude, quadrature phase relationship | passing |
| `tb_dsp/tb_demodulator.py` | Mixing identity | passing |
| `tb_dsp/tb_demod_lowpass.py` | Step response, reset, dead-zone sweep | passing |
| `tb_dsp/tb_pdh_closed_loop.py` | PDH path | passing |
| `tb_control/tb_output_limiter.py` | Boundary values, saturation flags | passing |
| `tb_control/tb_fault_gate.py` | Safe-code override | passing |
| `tb_control/tb_ramp_scan.py` | Triangle behaviour, zoom bounds, no overshoot | passing |
| `tb_control/tb_trace_capture.py` | Decimation, buffer write/read, overflow, ready | passing |
| `tb_control/tb_robust_autolock.py` | Feature match, reject, retry, lock trigger | passing |
| `tb_control/tb_slow_recenter.py` | Centering, slew limit, saturation | passing |
| `tb_control/tb_lock_fsm.py` | Legal transitions, fault priority, explicit clear | passing |
| `tb_control/tb_lock_watch.py` | Unlock, saturation, rails, jumps | passing |
| `tb_lock_core_top.py` | Full scan-to-lock with a fake plant | passing |

---

## Integration tests

`sim/tb_lock_core_top.py` implements packet 12.2 and adds one directed
regression guard per confirmed defect. Each test names the finding it
protects, so a reintroduced bug fails with a message that says what
broke rather than just an assertion number.

| Test | Guards |
|---|---|
| `test_register_readback` | 21 R/W registers that read back 0 |
| `test_fault_is_recoverable` | FAULT was an unescapable state |
| `test_trace_capture_fills` | Trace strobed once per sweep, not per step |
| `test_acquisition_reaches_lock` | The packet 12.2 sequence end to end |
| `test_closed_loop_converges` | PI integrator dead zone and rail drift |
| `test_acquisition_times_out` | Waiting states with no timeout |

---

## What the tests deliberately do not cover

Listed so the gaps stay visible.

**Timing closure.** Nothing here says anything about whether the design
meets 8 ns on an XC7Z010-1. Every multiplier in the fast path is
combinational: `PICore` runs error -> 20x18 multiply -> shift -> 40-bit
add -> 40-bit compare -> mux -> flop in one path, twice in parallel.
Synthesis and a timing report are a prerequisite to shipping, not a
formality. If the PI path fails, add a DSP48 pipeline register and
re-declare the module's latency.

**Clock-domain crossing.** The `sys_*` register bus is assumed to be in
the `adc_clk` domain. That holds for RedPitaya-FPGA v0.94, but this repo
neither pins a commit nor verifies it, and the constraints file already
contains `set_false_path -from clk_fpga_0 -to adc_clk`. If a future
revision presents the bus on `clk_fpga_0`, 32 bits of write data cross
two asynchronous 125 MHz clocks with the tool told not to check it.

**Reset assertion and release.** No test covers reset behaviour beyond
power-on, and the build script asks the integrator to hand-wire
`~adc_rstn`. `o_heartbeat` exists so a wrong inversion is visible in one
LED rather than looking like a broken register map.

**The benchmark harness cannot detect fixed-point defects.**
`bench/architectures/linien_reference.py` is an idealised
floating-point model (`integral += error * dt`) and says so in its own
docstring: "not Linien gateware". It has no truncation behaviour, so
comparing POSM against it can never surface the class of bug that was
actually breaking this design. `linien_hardware.py` is a stub requiring
an injected client that does not exist. Do not report benchmark output
as parity with Linien.

**Analogue and board-level behaviour.** Overrange detection is tied off
in the Red Pitaya wrapper, so a fault source packet 10.1 requires cannot
fire on the target board.

---

## Required plots (packet 12.3)

`sim/run_closed_loop_demo.py` writes CSV and plots to the configured
output directory. Packet 12.3 asks for scan code, error trace versus
scan code, the selected feature window and computed zero crossing, fast
and slow DAC versus time, corrected error, lock state, and saturation
and fault flags. Check the current output against that list before
signing off M5.
