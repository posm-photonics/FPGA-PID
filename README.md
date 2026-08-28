# FPGA-PID
A hardware-agnostic FPGA digital servo engine for laser frequency and intensity stabilization. Written in **Amaranth HDL** (Python) and elaborated to Verilog for synthesis, designed for simulation-first development, and structured to be portable across FPGA boards, ADC/DAC hardware, and host interfaces.

> **Note (2026-08-28):** this README previously described a SystemVerilog
> project and gave `xvlog`/`xelab`/`xsim` commands. The repository is and
> has always been Amaranth/Python; none of those commands worked, and the
> module list below referenced files that do not exist. Corrected during
> the pre-ship audit.

---

## What This Is

This project implements the digital control core for locking a laser to a spectroscopy feature. It takes signed ADC samples from an error signal, computes a control error relative to a setpoint, filters it, applies PI control, clamps the result, and produces signed DAC commands for a laser current or piezo actuator.

---

## Development Milestones

| Milestone | Description | Done When |
|-----------|-------------|-----------|
| **M0** | Repo skeleton, build scripts, simulation tooling | Can run a testbench and produce a waveform or CSV |
| **M1** | Core PI in simulation: error_calc, IIR filter, PI controller, output limiter | Step input converges; clamp and anti-windup verified; plots generated |
| **M2** | Scan and hold modes: ramp_scan, lock_fsm | Can switch between passthrough, ramp, hold, and PI lock in simulation |
| **M3** | Register interface | All coefficients, limits, modes, and flags exposed through a config bus |
| **M4** | Dev-board demo | PI loop updates a real GPIO/PWM/DAC output; debug signals observable |
| **M5** | Real lock integration | Live spectroscopy signal can be scanned and locked under supervision |

---

## How to run simulations

Everything is Python. There is no Verilog toolchain in the loop until
synthesis.

```bash
pip install -r docs/Requirements.txt

# Run a single testbench
python3 sim/tb_dsp/tb_pi_controller.py

# Run the full-system integration testbench (scan -> lock -> watch)
python3 sim/tb_lock_core_top.py

# Run every testbench
for f in $(find sim -name 'tb_*.py'); do
    echo "== $f"; python3 "$f" || echo "FAILED: $f";
done
```

## How to build

```bash
# Elaborate the board wrapper to Verilog
python3 build/generate_verilog.py
# -> build/out/red_pitaya_lock_core.v

# Then, with Vivado and a pinned RedPitaya-FPGA checkout:
vivado -mode batch -source scripts/build_posm_red_pitaya.tcl \
       -tclargs <path-to-RedPitaya-FPGA>
```

Read the warnings at the top of `scripts/build_posm_red_pitaya.tcl`
before wiring the core into `red_pitaya_top.v`. Three of them are
bring-up traps that cost real debugging time.

---

## Fixed-Point Conventions

All arithmetic is signed two's-complement. Key widths:

| Signal | Format | Notes |
|--------|--------|-------|
| ADC sample | signed 16-bit | Sign-extend from actual ADC width |
| Error | signed 18-bit | Extra bits for subtraction guard |
| Filter state | signed 24–32 bit | Saturate only at output |
| Kp, Ki | signed Q3.14 | Finalize after simulation experience |
| PI accumulator | signed 40-bit | Holds Ki*e at FULL precision; shifted only on read-out |
| DAC command | signed 16-bit | Map to voltage in hardware layer |

See `docs/03_fixed_point_scaling.md` for full binary point documentation,
including why the accumulator must not be shifted before accumulating.

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
