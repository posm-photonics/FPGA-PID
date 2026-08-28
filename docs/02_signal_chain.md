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
  -> pdh_frontend     PDH demod, or a matched delay when off    2 cycles
  -> error_calc       e = p*(x - offset - setpoint)             1 cycle
  -> pi_core          Kp*e + I                                  1 cycle
  -> output_limiter   programmable hard clamp                   1 cycle
  -> fault_gate       safe-code override                        1 cycle
  -> dac_fast_formatter  clamp + encode                         1 cycle
  -> DAC_FAST
```

**Total: 7 clock cycles = 56 ns at 125 MHz.**

Against packet 7.3's table that sits between "excellent" (50 ns, 18
degrees of phase lag at 1 MHz) and "still reasonable" (100 ns, 36
degrees), before the ADC deserialisation, the DAC ODDR stage and the
analogue group delay are added.

Two cycles of that total come from `pdh_frontend`, and they are spent
even when PDH mode is off: the direct path is delayed to match the PDH
path so that toggling `PDH_CONTROL` cannot change the loop delay.
Deterministic latency is worth more than 16 ns here, but see the
architectural note below.

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
