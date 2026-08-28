# 03 Fixed-point scaling

This file was empty, while `README.md` pointed at it for "full binary
point documentation". The pre-ship audit found two separate defects that
this document would have prevented, so it is written out with the
reasoning, not just the widths.

---

## The rule that matters most

**Never shift a product down before accumulating it.**

Accumulate at full precision and shift only when you read the
accumulator out. This is the most important convention in the
repository, and violating it produced the most serious defect found in
the audit.

### Why

An arithmetic right shift is floor division, which is asymmetric about
zero:

```
 12800 >> 14  ==  0        (positive truncates toward zero)
-12800 >> 14  == -1        (negative truncates away from zero)
```

If you shift `Ki * e` down to output units *before* adding it to the
integrator, then for every sample where `|Ki * e| < 2^gain_frac`:

- a positive error contributes exactly **0**, and
- a negative error contributes exactly **-1**.

Two failures follow, both reproduced in simulation on the old
`pi_controller.py`:

**Dead zone.** With a constant error of +100 and `ki = 128`,
`100 * 128 = 12800`, and `12800 >> 14 = 0`. The integrator never moved.
There is no integral action at all below `2^gain_frac / ki` counts of
error: 128 counts at `ki = 128`, 16384 counts at `ki = 1`.

**Rail drift.** With a zero-mean error alternating +3 / -3 (mean exactly
zero), the integrator accumulated -1 every other sample and drove the
output to the negative rail in about 8000 clocks, 64 us at 125 MHz,
where anti-windup latched it. A perfectly balanced error signal parked
the actuator at a rail.

### The correct structure

```python
# i_term is Ki*e at FULL precision -- NOT shifted
i_term.eq(self.error_in * self.ki)

# accumulate at full precision
int_sum.eq(integrator + i_term)

# shift only on read-out, with round-to-nearest
int_out.eq((integrator + (1 << (gain_frac - 1))) >> gain_frac)
```

The 40-bit accumulator then actually buys the precision it was sized
for. Under the old code the extra width bought nothing, because the
value was truncated before it ever reached the accumulator.

Both reference sources agree on this structure:

- **Linien** (`gateware/logic/pid.py`): `int_reg` is
  `width + coeff_width + 4` bits and carries 18 fractional bits below
  the output LSB. `ki_mult` is shifted by 4, not by `coeff_width`, and
  `int_out` is `int_reg >> extra_width`.
- **POSM packet section 8.5** lists "wide internal accumulator" as a
  required feature of `pi_core`.

The same defect appeared independently in `demod_lowpass.py`, where the
IIR accumulated at input scale and floored the update, leaving the
filter permanently short of its input by up to `2^alpha - 1` counts
(measured: 4095 counts short at `alpha_shift = 12`, a 41% amplitude
error). Same cause, same fix.

---

## Rounding

Where a shift is unavoidable, round to nearest rather than flooring:

```python
scaled.eq((value + (1 << (shift - 1))) >> shift)
```

Flooring introduces a systematic -0.5 LSB bias on every term. On a
one-shot term that is negligible. Inside a feedback loop that seeks
zero, a systematic bias becomes a lock-point offset.

For a variable shift the rounding constant has to be built at runtime:

```python
round_add.eq(Const(1, unsigned(W)) << (shift - 1))   # guard shift == 0
```

Amaranth rejects a signed shift amount, so keep the shift amount in an
unsigned signal.

---

## Widths in the fast path

| Signal | Format | Notes |
|---|---|---|
| ADC raw | unsigned 16 | Bit pattern; encoding set by `ADC_CONFIG` |
| ADC sample | signed 17 | `adc_formatter` output; +1 bit for offset-binary conversion |
| PDH mixer product | signed 33 | 17 x 16 |
| PDH mixer output | signed 20 | product >> 13, saturated |
| PDH LPF accumulator | signed 40 | 20 integer + 20 fractional bits |
| Error | signed 20 | `err_w`, end to end from the PDH front end to the PI |
| Kp, Ki | signed 18, Q3.14 | `gain_frac = 14` |
| Kp*e, Ki*e | signed 38 | 20 + 18 |
| PI accumulator | signed 40 | Holds Ki*e at full precision |
| PI output | signed 16 | Clamped to `FAST_OUT_MIN` / `MAX` |
| Limiter / gate / formatter | signed 24 | Controller domain |
| DAC code | unsigned 16 | Encoding set by `DAC_CONFIG` |

The error path is 20 bits from `pdh_frontend.error_sample` all the way
to `pi_ctrl.error_in`. It previously narrowed to 17 bits inside the PDH
front end and was then assigned into a 16-bit `ErrorCalc` input, which
truncated the MSB: PDH error values outside +/-32767 **wrapped sign**.
Packet 4.4 is blunt about why that matters: "If the controller polarity
is wrong, the loop becomes positive feedback and runs away."

---

## Signed versus unsigned at module boundaries

Two rules, both learned the hard way here:

1. **A DAC code is not a number.** `o_dac_fast` and `o_dac_slow` are
   unsigned bit patterns whose meaning depends on `DAC_CONFIG`. Do not
   do arithmetic on them. Everything upstream of the formatter is signed
   controller units; everything downstream is a code.

2. **Do not mix two views of one signal.** `lock_watch` used to declare
   its DAC monitor ports unsigned and then reinterpret the same signal
   with `.as_signed()` a few lines later. Fed from a two's-complement
   path with limits of 0 and 65535, the unsigned rail comparison
   asserted whenever the output sat at 0 or -1, which is exactly where a
   healthy servo sits.

The same trap caught the autolock: its scan-position ports were unsigned
while `ramp_scan.ramp_out` is `signed(16)`, so -3200 read as 62336 and
the width and slope tests broke for any feature spanning the zero code.

---

## Saturation versus truncation

Every actuator-facing narrowing must saturate, never truncate. The
pattern is: clamp in the **signed** domain at full width, then convert.

```python
with m.If(value > max_signed):
    m.d.comb += clamped.eq(max_signed)
with m.Elif(value < min_signed):
    m.d.comb += clamped.eq(min_signed)
with m.Else():
    m.d.comb += clamped.eq(value)
```

The DAC formatters previously computed `value + 2^(W-1)` straight into
an **unsigned** W-bit signal and then tested `formatted > 2^W - 1` and
`formatted < 0`. Neither test can ever be true for an unsigned W-bit
signal, so both clamp arms were dead logic and a negative overflow
wrapped to a large positive code, driving the actuator to the opposite
rail.

Clamping guard bits must survive to the comparison. `ramp_scan` computed
its zoom bounds in `dac_w + 1` bits, with a comment saying the extra bit
was there "to catch overflow before clamping", and then discarded that
bit with a `[:dac_w]` slice. Linien keeps the guard bit by instantiating
its sweep `Limit()` one bit wider than the data and sign-extending
min/max into it.

---

## Q-format quick reference

`Q3.14` in an 18-bit signed register: 1 sign bit, 3 integer bits, 14
fractional bits. Range -8.0 to +7.99994, resolution 1/16384.

```python
real_to_q314(0.5)   # 8192
q314_to_real(8192)  # 0.5
```

Helpers live in `rtl/common/sat_math.py`.

`SLOW_RECENTER_GAIN` uses `GAIN_FRAC = 12`, not 14. Check
`slow_recenter.py` before reusing the Q3.14 helpers on it.
