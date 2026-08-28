# 04 Control notes

This file was empty. It records the control-loop decisions that are
easy to get wrong in fixed point and that the pre-ship audit found had
been got wrong.

---

## Loop structure

```
I[n+1] = I[n] + Ki*e[n]
u[n]   = Kp*e[n] + I[n]
u_lim  = clip(u, out_min, out_max)
```

Packet 4.4 Eqs. 15-16 and 8.5 Eqs. 19-21. No derivative term: packet 2
freezes the baseline as "PI core plus optional low-latency compensation.
No naked derivative term."

The optional compensator (`optional_compensator.sv`, packet 8.6) does
not exist yet. It is meant to be bypassed by default, so its absence
does not change v1 behaviour.

---

## Integral action in fixed point

See `docs/03_fixed_point_scaling.md` for the full argument. The short
version: accumulate `Ki*e` at full precision and shift only on read-out.
Shifting first gives a dead zone for small errors and a monotonic drift
to a rail on a zero-mean error, because an arithmetic right shift floors
and is therefore asymmetric about zero.

---

## Anti-windup

Two independent mechanisms, both present:

1. **Conditional integration.** Stop integrating when the output is
   already at a limit *and* the new increment would push it further in
   the same direction. Motion back toward the linear region is always
   allowed.
2. **Integrator clamp.** The accumulator itself is clamped to
   `out_min << gain_frac` .. `out_max << gain_frac`. Linien does the same
   thing (`max_pos_extra` / `max_neg_extra` in its `calculate_i`).

Conditional integration alone is not enough: it leaves the accumulator
free to grow whenever the proportional term happens to keep the summed
output in range.

The windup test must use the true increment, not the shifted one. When
the shifted increment was used, it read as zero inside the dead zone and
released anti-windup when it should not have.

---

## Leaky integrator

`FAST_INT_LEAK` (packet 11.4) subtracts `I >> leak_shift` on each update.
0 disables it.

This is not optional polish on this hardware. Packet 3.4 and the
"Important" box in 8.5 both say the CTL200 AC modulation input cannot
carry true DC correction, so long-term integral authority has to live on
the slow/DC path. A pure accumulator on the fast path winds up against
an actuator that physically cannot respond to it.

---

## Split-actuator control

| Output | Destination | Role |
|---|---|---|
| DAC_FAST | CTL200 AC / high-frequency modulation input | High-frequency correction |
| DAC_SLOW | CTL200 DC modulation input | Scan, centring, long-term drift |

Packet 3.4 Eqs. 4-5. They are different actuators with different
transfer functions, which is why loading a slow-path scan code into the
fast integrator is a units error. That preload is retained in
`lock_core_top.py` for behavioural continuity but flagged; the slow
handoff is what actually parks the slow actuator.

---

## Polarity

`ERROR_CONFIG` bit 0 inverts the error. Packet 4.4 puts it bluntly: "If
the controller polarity is wrong, the loop becomes positive feedback and
runs away."

Two ways that has bitten this design already, both fixed:

- A 20-bit error assigned into a 16-bit port truncated the MSB, so PDH
  error values outside +/-32767 **wrapped sign** and inverted the
  feedback on large excursions.
- The relay auto-tuner emitted negative Kp and Ki once its ultimate-gain
  computation was corrected, because the result overflowed a signed(18)
  target with no saturation. A relay experiment can only produce a
  positive ultimate gain, so it is clamped to non-negative now.

---

## Lock detection

The lock-quality condition must hold continuously for
`LOCK_CHECK_DELAY` samples before lock is declared. Packet 9.2 step 8:
"FPGA waits a configured delay and checks error/output metrics."

Without persistence, a single noisy sample drops the lock. At 125 MHz
that is not a hypothetical. Linien gates its equivalent decision on
`waited_long_enough` for the same reason.

The failure path is the watchdog's filtered opinion
(`lock_watch.unlock_detected`), not an instantaneous complement of the
pass condition. Those are different questions and they need different
time constants.

---

## Gain calibration on Red Pitaya

The ADC and DAC scaling conventions in the board wrapper are not
symmetric, and it is worth knowing before tuning gains on hardware:

- ADC: a 14-bit sample is **sign-extended** into a 16-bit field, so the
  signal occupies a quarter of the numeric range. Full scale is +/-8191,
  not +/-32767.
- DAC: a 16-bit code is **shifted down by 2** to 14 bits, which maps
  16-bit full scale onto the converter's full scale.

The net effect is a factor of 4 between the naive code-domain loop gain
and the real one. It is not a bug, but a Kp computed on paper without
accounting for it will be off by 4x.
