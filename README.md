# FPGA-PID
A hardware-agnostic FPGA digital servo engine for laser frequency and intensity stabilization. Built in SystemVerilog, designed for simulation-first development, and structured to be portable across FPGA boards, ADC/DAC hardware, and host interfaces.

---

## What This Is

This project implements the digital control core for locking a laser to a spectroscopy feature. It takes signed ADC samples from an error signal, computes a control error relative to a setpoint, filters it, applies PI control, clamps the result, and produces signed DAC commands for a laser current or piezo actuator.

---

## Development Milestones

| Milestone | Description | Done When |
|-----------|-------------|-----------|
| **M0** | Repo skeleton, build scripts, simulation tooling | Can run a SystemVerilog simulation and produce a waveform or CSV |
| **M1** | Core PI in simulation: error_calc, IIR filter, PI controller, output limiter | Step input converges; clamp and anti-windup verified; plots generated |
| **M2** | Scan and hold modes: ramp_scan, mode_mux, lock_fsm | Can switch between passthrough, ramp, hold, and PI lock in simulation |
| **M3** | Register interface | All coefficients, limits, modes, and flags exposed through a config bus |
| **M4** | Dev-board demo | PI loop updates a real GPIO/PWM/DAC output; debug signals observable |
| **M5** | Real lock integration | Live spectroscopy signal can be scanned and locked under supervision |

---

## How to Run Simulations

### Vivado xsim (current toolchain)

```tcl
# Compile and run a single testbench, e.g. tb_sat_math
xvlog --sv rtl/common/sat_math.sv sim/tb_common/tb_sat_math.sv
xelab tb_sat_math --snapshot tb_sat_math_snap
xsim tb_sat_math_snap --runall
Waveforms are written to `outputs/waveforms/`. CSV outputs go to `outputs/csv/`. Use the Python scripts in `sim/tb_system/` to generate plots from CSV data.

---
## Fixed-Point Conventions

All arithmetic is signed two's-complement. Key widths:

| Signal | Format | Notes |
|--------|--------|-------|
| ADC sample | signed 16-bit | Sign-extend from actual ADC width |
| Error | signed 18-bit | Extra bits for subtraction guard |
| Filter state | signed 24–32 bit | Saturate only at output |
| Kp, Ki | signed Q3.14 | Finalize after simulation experience |
| PI accumulator | signed 40-bit | Clamp to DAC limits at output |
| DAC command | signed 16-bit | Map to voltage in hardware layer |

See `docs/03_fixed_point_scaling.md` for full binary point documentation.

---

## Design Rules

- Every RTL module has a testbench or is covered by the top-level integration testbench
- Every signal crossing a module boundary has documented width, signedness, valid/enable, and reset behavior
- Every multiply has explicit bit growth and truncation/rounding documented
- Every actuator-facing output has programmable hard limits
- Every state machine has a safe reset state and a fault/disable behavior
- The repo can be cloned and simulated without hidden local paths

---

## Key References

| Topic | Reference |
|-------|-----------|
| Whole system architecture | [Jørgensen et al. 2016 — Simple FPGA laser lock](https://arxiv.org/pdf/1607.02860) |
| Spectroscopy signal chain | [Linien — Open-source FPGA laser lock (AIP RSI)](https://pubs.aip.org/aip/rsi/article/93/6/063001/2848770/) |
| Fixed-point Verilog | [Project F — Fixed-Point Numbers in Verilog](https://projectf.io/posts/fixed-point-numbers-in-verilog/) |
| Fixed-point PI control | [EmbeddedRelated — Fixed-Point PI Controller](https://www.embeddedrelated.com/showarticle/121.php) |
| FPGA PID hardware | [Kulisz 2024 — FPGA PID Implementation](https://www.mdpi.com/2079-9292/13/8/1598) |
| IIR filter on FPGA | [Parametric IIR Filtering on an FPGA](https://ashrafi.sdsu.edu/PDF/Parametric_IIR_Filtering_on_an_FPGA.pdf) |
| Verilog debug habits | [ZipCPU Tutorial](https://zipcpu.com/tutorial/) |
| Simulation and lint | [Verilator Documentation](https://verilator.org/guide/latest/verilating.html) |

Full reading list in `docs/08_reading_list.md`.

---

## License

Apache 2.0 — see `LICENSE` for details.

