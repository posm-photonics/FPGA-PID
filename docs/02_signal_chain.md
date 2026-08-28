# 02 Signal chain

This file was empty. It records the actual datapath as built, with the
declared latency of every stage, which packet section 7.3 requires:
"Every module in the fast path must declare its valid-in -> valid-out
latency in cycles."

---

## Fast path (Category A, latency-critical)

```
ADC_CH0 (14-bit, board encoding)
  -> [board wrapper] {sign, ~magnitude} -> two's complement     0 cycles
  -> adc_formatter    coding -> signed                          0 (comb)
  -> adc_guard        validity flags only, values untouched     0 (comb)
  -> pdh_frontend     PDH demod, or a matched delay when off    4 cycles
       demodulator      multiply (DSP MREG) + shift + clamp       2
       demod_lowpass    pre-add stage + IIR recurrence            2
  -> error_calc       e = p*(x - offset - setpoint)             1 cycle
  -> pi_core          Kp*e + I  (multiply in DSP MREG)          2 cycles
  -> output_limiter   programmable hard clamp                   1 cycle
  -> fault_gate       safe-code override                        1 cycle
  -> dac_fast_formatter  clamp + encode                         1 cycle
  -> DAC_FAST
```

**Total: 10 clock cycles = 80 ns at 125 MHz.**

Against packet 7.3's table that is inside "still reasonable" (100 ns,
36 degrees of phase lag at 1 MHz) and outside "excellent" (50 ns, 18
degrees), before the ADC deserialisation, the DAC ODDR stage and the
analogue group delay are added.

### Why this grew from 7 cycles to 10

Synthesis was finally run (`yosys synth_xilinx -family xc7`). Every
multiplier in the design came out of it as a DSP48E1 with `MREG=0` and
`PREG=0`, meaning the 25x18 multiply and the 48-bit post-adder were
combinational and shared a register-to-register path with the wide
accumulator arithmetic behind them. Structural analysis of the
synthesised netlist put the worst path at 7.73 ns of LOGIC delay alone,
against an 8.0 ns budget, leaving 0.27 ns for all routing on a -1 part.
That does not build.

Three cycles were added, one per offending stage, each by registering a
multiplier product so the DSP absorbs it as `MREG`:

| stage | was | now | why |
|---|---|---|---|
| `demodulator` | 1 | 2 | I/Q products registered |
| `demod_lowpass` | 2 | 3 | round-add hoisted out of the IIR recurrence and registered |
| `pi_core` | 1 | 2 | Kp*e and Ki*e registered; this is exactly the remedy S3-2 specified |

The worst path is now 4.38 ns of logic, leaving about 3.6 ns for
routing. See `build/report_paths.py` for the measurement and its
limits -- it is a structural analysis, not a Vivado timing signoff.

Three things were deliberately NOT pipelined:

* **The `pi_core` integrator recurrence.** `integrator -> int_sum ->
  int_next -> integrator` is a single-cycle feedback loop. Splitting it
  would halve the effective integral rate and change the tuning. It was
  shortened instead, by gating anti-windup on the already-registered
  saturation flags rather than the combinational ones.
* **The `demod_lowpass` IIR recurrence**, for the same reason. The
  pre-add was moved out of it by the identity `(x - acc) + r ==
  (x + r) - acc`, which is exact.
* **Anything on the slow path**, which needed no latency argument at all
  (see below).

At the loop bandwidths this servo targets, 24 ns of added delay is a
fraction of a degree of phase; the pole it adds sits at about 6 MHz.

### The obvious way to get back under 50 ns

Four of the ten cycles are `pdh_frontend`, and they are spent even when
PDH mode is off, because the direct path is delayed to match the PDH
path so that toggling `PDH_CONTROL` cannot change the loop delay.
Removing the PDH block from the v1 fast path -- which packet 7.2 asks
for anyway, see the architectural note below -- would leave 6 cycles =
48 ns, inside "excellent". That is a scope decision, not a timing fix,
so it has not been made here.

### Architectural note

Packet 7.2 lists "Digital demodulation for the required v1 lock" under
"Not allowed in the fast path", and packet 2 freezes the primary error
signal as the **analogue**-demodulated MTS error on ADC_CH0. The PDH
block is nonetheless spliced between the ADC front end and `error_calc`.

This is an unresolved scope question, not a bug. Either remove the block
from the v1 fast path or amend the packet.

---

## Slow path (Category B)

```
ramp_scan (during scan states)      \
                                     >-- mux -- output_limiter
slow_lock_base + slow_recenter      /               |
                                                    v
                                        dac_slow_formatter
                                                    |
                                                    v
                                                DAC_SLOW
```

The slow output previously went straight from the mux to the top-level
port with no limiter, no formatter and no fault gate, so a fault parked
the fast DAC safely while the slow DAC (the one driving the piezo) kept
driving whatever it had. `dac_slow_formatter.py` existed and was never
instantiated.

`slow_lock_base` is latched at ARM_LOCK entry from the position the
autolock measured, and the recenter accumulator is added to it as a
correction. That is packet Eq. 25, where the recenter term corrects the
operating point rather than replacing it. Without the base, the slow DAC
stepped from the verified feature to mid-scale at exactly the moment
lock was attempted, which is the "kick" packet 9.3 forbids.

---

## Supervisory path (Category B)

```
ramp tick ---> trace_capture ---> BRAM ---> host readback
           \-> robust_autolock -> feature_match / feature_failed
                                   |
lock_watch -----------------------\|/
                                lock_fsm ---> enables for everything above
```

`trace_capture` and `robust_autolock` are both strobed from the ramp
tick, one sample per ramp step. The trace was previously strobed from
the completed-sweep pulse (one point per entire scan) and the autolock
from a signal that was high every clock, so it saw fast-loop noise
rather than the scan trace.

---

## Sample rate

There is no decimation anywhere. `i_adc_valid` is tied high in the Red
Pitaya wrapper, so the whole design runs at 125 MHz.

Several module docstrings were written against a "1 MHz fast-loop rate"
assumption from the packet. That matters for the Category B blocks:
`slow_recenter` defaults its tick divider to 2^12 (about 33 us)
specifically because a divider of 0 would run the slow loop at the full
sample rate, which packet 8.11 forbids and which would make the slow
loop faster than the fast loop it is correcting.

That divider is also why `slow_recenter` was the cheapest timing fix in
the design. Synthesis put its worst path at 7.73 ns of logic -- the
single worst path in the whole core -- through a combinational DSP48E1
multiply and a 40-bit slew clamp. Three pipeline registers were added
(multiplier operands, product, slew clamp) and `slow_tick` is delayed by
the same three cycles so the arithmetic per tick is unchanged. 24 ns of
added latency against a 33 us tick period is 0.07 % of one update, so
unlike the fast path this needed no latency argument at all. Its worst
path is now 2.87 ns.
