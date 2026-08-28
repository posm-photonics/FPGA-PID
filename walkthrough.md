# PDH Implementation Walkthrough

> **CORRECTION (2026-08-28, pre-ship audit).** The validation section at
> the bottom of this document was wrong in a way that mattered, and it
> is corrected in place below. In short: the top-level design did not
> elaborate at all, so the claim that "the RTL logic itself synthesizes
> cleanly" was false, and the `DriverConflict` described as a test
> environment quirk was a real design defect that had kept the only
> full-system testbench from ever running. Three defects in the PDH
> subsystem itself are also recorded below.

The Pound-Drever-Hall (PDH) modulation and demodulation system has been
implemented and integrated into the existing FPGA POSM repository
according to the design plan. See the corrected validation section for
what was and was not verified.

## Changes Made

### 1. New DSP Modules
I implemented four new modules in `rtl/dsp/`:
- **NCO** (`nco.py`): 32-bit phase accumulator with a 256-entry quarter-wave sine LUT. It generates phase-coherent 16-bit sine and cosine waves for both the modulation DAC and the digital mixer.
- **Demodulator** (`demodulator.py`): Multiplies the raw ADC signal with the NCO reference, scaling and saturating the output to a 20-bit signed result.
- **DemodLowpass** (`demod_lowpass.py`): A first-order IIR filter with programmable bandwidth (via a right-shift parameter `alpha_shift`). It uses a 40-bit internal accumulator for precision and outputs the 20-bit filtered error signal.
- **PDHFrontend** (`pdh_frontend.py`): The top-level wrapper that instantiates the components, scales the modulation amplitude (Q2.14), and multiplexes between the direct ADC signal and the PDH error signal based on `pdh_enable`.

### 2. Register Integration
- **`rtl/bus/register_defs.py`**: Added register addresses `0x200` to `0x210` for PDH configuration.
- **`rtl/bus/register_bank.py`**: Updated the register bank to decode and hold the new PDH configuration state (`pdh_enable`, `pdh_mod_freq`, `pdh_mod_amp`, `pdh_demod_phase`, `pdh_lpf_alpha`).

### 3. Top-Level Integration
- **`top/lock_core_top.py`**:
  - Instantiated `PDHFrontend`.
  - Redirected the output of `ADCFrontendTop` through the PDH module.
  - Spliced the PDH output into `ErrorCalc`.
  - Added a new top-level output port `o_dac_mod` to drive the EOM modulation DAC.

### 4. Tests & Models
- Added `FakePDHCavity` in `sim/models/fake_pdh_cavity.py` for closed-loop physics simulation.
- Created `tb_nco.py`, `tb_demodulator.py`, `tb_demod_lowpass.py`, and `tb_pdh_closed_loop.py` under `sim/tb_dsp/` to test the logic.

### 5. Documentation
- Added `docs/09_pdh_architecture.md` to document the new subsystem.
- Updated `docs/05_register_map.md` with the new PDH registers.

## Validation Results (corrected)

- Unit tests for `nco`, `demodulator` and `demod_lowpass` did pass.
  However, `tb_demod_lowpass.py` was **failing** on the committed tree
  ("Expected 10000, got 9973"), and that red test was shipped. It was
  catching a real defect: the LPF accumulated at input scale and floored
  its update, so it settled permanently short of its input by up to
  `2^alpha - 1` counts (4095 counts, a 41% amplitude error, at
  `alpha_shift = 12`). Fixed; the test now sweeps alpha and requires
  exact convergence.

- **The `DriverConflict` was not a test-environment issue.** Driving
  `ClockSignal("sync")` from combinational logic makes it impossible to
  attach a simulation clock, and in synthesis it routes a clock through
  general fabric rather than a global buffer. Because it blocked the only
  full-system testbench, every defect at a module boundary went
  unexercised; the pre-ship audit found eleven ship-blockers in
  `lock_core_top.py` alone. Fixed, and the integration testbench now runs
  the full packet 12.2 sequence.

- **"The RTL logic itself synthesizes cleanly" was false.** The design
  did not elaborate: `rtl/dac/dac_fast_formatter.py` raised
  `TypeError: 'int' object is not subscriptable`, which took down
  `build/generate_verilog.py`, `run_bench.py` and both integration
  testbenches. There was no Verilog and therefore no bitstream. Fixed;
  `generate_verilog.py` now produces output.

### PDH-specific defects found and fixed

- **`PDH_DEMOD_PHASE` did nothing.** The offset was applied inside the
  shared NCO whose output drove both the modulation DAC and the mixer
  reference, so their relative phase was pinned at zero. Packet 5.1
  specifies the EOM drive at phase 0 and the mixer LO at `phi_demod`,
  and Linien applies its `delay` CSR inside `Demodulate` only. Without
  an adjustable demod phase there is no way to compensate the round trip
  through the EOM, cavity, photodiode and ADC. Now verified in
  simulation: the I channel follows cos(phase) across 0-270 degrees.

- **The modulation output could wrap.** `(mod_product >> 14)[:16]` was a
  bare truncation of a value with an 18-bit range, so any `mod_amp`
  above 16384 (Q2.14 > 1.0) overflowed and mangled the EOM drive with
  sign flips. `mod_amp` is a 16-bit register documented as Q2.14, so
  that was reachable by design. Saturating now.

- **The error path narrowed and wrapped.** `error_sample` was 17 bits
  (saturated down from the 20-bit LPF result) and was then assigned into
  a 16-bit `ErrorCalc` input, truncating the MSB: PDH error values
  outside +/-32767 wrapped sign and inverted the feedback. The path is
  20 bits end to end now.

## Open Items

- `o_dac_mod` is routed out of `LockCoreTop` on its own port, but the
  board wrapper never connects it: the Red Pitaya has two DAC channels
  and both are committed. On that board the PDH subsystem demodulates a
  signal that is never modulated, and synthesis prunes the modulation
  datapath entirely. The packet's own architecture (section 5.1)
  generates the EOM drive from an external AD9959 DDS, so for v1 an
  FPGA modulation output may not be needed at all. **Architecture
  decision required.**

- **The PDH block sits in the fast path, which packet 7.2 forbids** for
  the required v1 lock ("Digital demodulation for the required v1
  lock"), and packet 2 freezes the primary error signal as the
  analogue-demodulated MTS error. It adds two cycles to the fast path
  even when disabled. **Scope decision required.**

- The `LockFSM` did have to change: the zoom ramp used to stop at
  exactly the moment the autolock was enabled, so the verifier had no
  scan data to track. The claim that "the `wide_scan` state provides
  plenty of time for the LPF to settle" was not the relevant question.
