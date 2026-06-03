# 01 System Overview

---

## System Block Diagram
(update when block diagram is made)

---

## Modules

| Module | Location | Purpose |
|--------|----------|---------|
| `sat_math` | `rtl/common/` | Saturating arithmetic utilities used everywhere |
| `round_shift` | `rtl/common/` | Rounding right-shift for fixed-point scaling |
| `sign_extend` | `rtl/common/` | Sign extension utility |
| `adc_formatter` | `rtl/dsp/` | Converts ADC output format to signed two's complement |
| `error_calc` | `rtl/dsp/` | Offset correction, setpoint subtraction, polarity inversion |
| `iir_lowpass_1p` | `rtl/dsp/` | First-order IIR low-pass filter |
| `moving_average` | `rtl/dsp/` | Optional boxcar average for diagnostics |
| `pi_controller` | `rtl/dsp/` | Fixed-point incremental PI with saturation and anti-windup |
| `output_limiter` | `rtl/dsp/` | Hard programmable limits on actuator-facing output |
| `lock_detect` | `rtl/dsp/` | Status metrics: error threshold, saturation flag, lock counter |
| `ramp_scan` | `rtl/control/` | Triangle or sawtooth scan generator in DAC-code units |
| `mode_mux` | `rtl/control/` | Selects what drives the DAC: zero, fixed, ramp, PI, hold |
| `lock_fsm` | `rtl/control/` | State machine: idle, scan, armed, locked, hold, fault |
| `safety_interlock` | `rtl/control/` | External fault input handling |
| `register_bank_simple` | `rtl/bus/` | Memory-mapped register file for software control |
| `lock_core_top` | `rtl/top/` | Board-independent top level, instantiates all modules |
| `board_top_stub` | `rtl/top/` | Board-specific wrapper, pins, clocks, ADC/DAC interfaces |

---

## Interface Convention

All DSP modules use a simple valid stream interface:

```systemverilog
input  logic clk
input  logic rst
input  logic signed [W-1:0] sample_in
input  logic sample_valid
output logic signed [W-1:0] sample_out
output logic sample_out_valid
```

- Synchronous reset throughout
- All samples are signed two's complement internally
- If ADC is offset-binary, convert to signed immediately in adc_formatter
- Never silently wrap actuator-facing signals — always saturate

---

## What Is Not In This Project

- Analog conditioning, photodiode amplifier, or laser driver PCB
- ADC or DAC hardware selection — the core is hardware-agnostic
- Host computer interface, networking, or GUI at v0
- Automatic lock acquisition or peak finding at v0
- Lock-in demodulation at v0
