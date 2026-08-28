# 07 Hardware integration notes

This file was empty, and it is the file where the largest open question
in the project should have been answered.

---

## The target-hardware question

`POSM_project_FPGALock.pdf` section 2 freezes the hardware:

| Item | Packet decision |
|---|---|
| FPGA | Ultra96V2 (Zynq 7020) |
| ADC | ADC3664, dual simultaneous |
| DAC | AD9117 dual DAC |
| Laser controller | Koheron CTL200 |

Packet section 15.3, on Red Pitaya references: **"Do not make POSM a
Red Pitaya compatibility project."**

`docs/00_project_brief.md`, committed in this repository, lists under
"Out of scope at v0": **"Red Pitaya compatibility or Linien clone."**

Every board-integration artefact in this repository targets Red Pitaya:

- `top/RedPitaya_Lock_Core.py`
- `rtl/bus/RedPitaya_Bus_Bridge.py`
- `constraints/board_specific/RedPitaya_125-14_constraint.xdc`
- `scripts/build_posm_red_pitaya.tcl`
- `UI/Interface/gui/server/hw_backend.py` (mmaps `/dev/mem` on the board)
- `UI/checker/posm_reg_server.py`

There is no Ultra96V2, ADC3664 or AD9117 wrapper anywhere.

**This needs a decision from the team, not from an audit.** Either:

1. Amend packet section 2 and `docs/00_project_brief.md` to make Red
   Pitaya the v1 target, and write the rationale here; or
2. Treat the Red Pitaya work as a development vehicle and record what
   the real target integration still requires.

Whichever it is, write it down. Right now the repository contradicts
both the packet and its own committed brief with no recorded reason,
and the file where the reason belongs is this one.

---

## Red Pitaya integration, as built

Target: STEMlab 125-14, XC7Z010-1, dual 14-bit 125 MSPS ADC and DAC.

The core is instantiated as one leaf module inside Red Pitaya's own
`red_pitaya_top.v`. Clock generation, PLL setup, differential IO
buffering and the ADC/DAC IOB/ODDR primitives are reused unmodified.

### ADC and DAC encoding

Red Pitaya's firmware does not present raw offset binary. It uses:

```verilog
assign adc_a = {adc_dat_a[13], ~adc_dat_a[12:0]};   // keep sign, invert magnitude
```

and the reverse on output. That conversion is done in
`top/RedPitaya_Lock_Core.py`, so `ADCFrontendTop` is told
`i_format_mode = 1` (two's-complement passthrough).

Watch the bit order: Verilog's `{a, b}` puts `a` in the **high** bits,
Amaranth's `Cat(a, b)` puts `a` in the **low** bits. The wrapper's
`Cat(~dat[:13], dat[13])` is the correct translation of
`{dat[13], ~dat[12:0]}`. This is easy to get backwards and produces a
sign-inverted ADC, which looks exactly like a polarity bug in the servo.

### Scaling asymmetry

- ADC: 14-bit sample sign-extended into 16 bits. Full scale is +/-8191,
  a quarter of the numeric range.
- DAC: 16-bit code shifted down by 2 to 14 bits, mapping 16-bit full
  scale onto converter full scale.

Net factor of 4 between a naively computed loop gain and the real one.
Not a bug, but calibrate with it in mind.

### Sample rate

`i_adc_valid` is tied high, so the design runs at 125 MHz with no
decimation. Module docstrings written against the packet's "1 MHz
fast-loop rate" assumption should be read with that in mind; it is why
`slow_recenter` defaults its tick divider to 2^12.

### Register bus

`RedPitayaBusBridge` adapts the `sys_*` peripheral bus to the internal
`adr/dat_w/dat_r/we/stb` convention, sitting in one of the eight
address-decoded slots (slot 6 is free in the v0.94 layout).

Byte enables (`sys_sel`) are accepted and ignored: every write is a full
32-bit word. `hw_backend.py` uses `struct.pack_into("<I", ...)`, so this
is currently safe, but any software doing sub-word writes will corrupt
the upper bytes.

**Clock domain: verify before building.** See `constraints/README.md`.
The bus is assumed to be in `adc_clk`. That is true for v0.94, is not
verified here, and the constraints file already false-paths
`clk_fpga_0 -> adc_clk`.

---

## Bring-up checklist

1. **Pin the RedPitaya-FPGA commit.** Slot usage and net names have
   shifted between releases, and the register bus's clock domain depends
   on the revision.
2. **Check `.rst( ~adc_rstn )`.** The core uses a synchronous,
   active-high reset. Backwards means the whole core sits in reset
   reading zeros, which is indistinguishable from a broken register map.
   Wire `o_heartbeat` to an LED: it is a free-running counter bit, so a
   blinking LED proves the core is clocked and out of reset.
3. **Do not tie `i_feature_selected` high.** The FSM leaves WIDE_SCAN on
   `trace_ready | feature_selected`, so tying it high skips the wide
   scan and trace capture entirely. Strictly it should be a register
   written by the PC after the operator clicks (packet 9.2 step 2);
   there is no register for it yet.
4. **Read back what you write.** Confirm `VERSION` reads `0x00030000`
   and that a written `FAST_OUT_MAX` reads back before trusting anything
   else.
5. **Confirm output polarity with the servo disabled** before closing
   the loop. Set `OUTPUTS_ENABLE`, drive a known safe code, and check
   the DAC voltage.
6. **Run synthesis and read the timing report** before the first lock
   attempt. Every multiplier in the fast path is combinational; nothing
   in simulation says whether the PI path closes at 8 ns on a -1 part.

---

## Known board-level gaps

- **Overrange is tied off.** `i_adc_overrange_ch0/1` are wired to 0 in
  the board wrapper, so a fault source packet 10.1 requires cannot fire.
  The Red Pitaya front end exposes no overrange flag; detecting a sample
  parked at full scale in the board layer is the usual substitute.
- **`o_dac_mod` has nowhere to go.** The board has two DAC channels and
  both are committed. On this board the PDH subsystem demodulates a
  signal that is never modulated, and synthesis prunes the modulation
  datapath. The packet's own architecture generates the EOM drive from
  an external AD9959, so for v1 an FPGA modulation output should not be
  needed at all.
- **No `ASYNC_REG` constraints** on the two input synchronisers yet.
