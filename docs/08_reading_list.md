# Reading List

A curated reference list for the POSM FPGA Laser-Lock Core project.
Organized by topic. Start with the starred entries if you are new to the project.

---

## Whole System Architecture

**★ Jørgensen et al. — "A simple laser locking system based on a field-programmable gate array" (2016)**
- Link: https://arxiv.org/pdf/1607.02860
- Why read it: Best first paper. Intentionally simple, aimed at atom cooling/trapping. Shows the whole system from ADC to actuator in a digestible way. Read this before anything else.

---

## Spectroscopy Lock Signal Chain

**★ Linien — "Linien: A versatile, user-friendly, open-source FPGA-based tool for laser stabilization and spectroscopy" (AIP RSI 2022)**
- Link: https://pubs.aip.org/aip/rsi/article/93/6/063001/2848770/Linien-A-versatile-user-friendly-open-source-FPGA
- Why read it: Look at the simplified signal-flow figure and the discussion of integer widths, filtering, and PID blocks. Also useful for future autolock and diagnostics ideas.
- Linien GitHub: https://github.com/linien-org/linien

**Linien v2 arXiv paper**
- Link: https://arxiv.org/abs/2203.02947
- Why read it: Use as the conceptual reference for scan-before-lock behavior. Do not copy the full autolock system for v0.

---

## Open-Source Lockbox Ecosystem

**PyRPL — "PyRPL: A versatile tool for quantum optics experiments" (arXiv 2023)**
- Link: https://arxiv.org/abs/2310.00086
- Why read it: Good reference for why modular modes and diagnostics matter in quantum optics digital feedback systems. Also useful for future automatic feedback controllers, complex filters, resonance search, lock acquisition, and diagnostics.
- PyRPL GitHub: https://github.com/pyrpl-fpga/pyrpl

**NQontrol — Darsow-Fromm et al. (arXiv 2019)**
- Link: https://arxiv.org/abs/1911.08824
- Why read it: Useful system-level comparison for multi-loop digital control in quantum-optics experiments. Relevant for M5+ multi-rate control extensions.

---

## Digital Laser Lock Hardware

**Preuschoff et al. — "Digital laser frequency and intensity stabilization based on the STEMlab platform" (arXiv 2020)**
- Link: https://arxiv.org/abs/2009.00343
- Why read it: Useful for seeing how practical digital laser controllers expose and package actuator/control paths.

**Majumder et al. — "Advancing frequency locking: Modified FPGA-Guided laser frequency locking technique" (Optics and Laser Technology 2023)**
- Link: https://www.sciencedirect.com/science/article/pii/S0030399223011404
- Why read it: Recent example of FPGA-guided scan/lock behavior around spectroscopy signals.

---

## Control Theory and Latency

**★ Tourigny-Plante et al. — "An open and flexible digital phase-locked loop for optical metrology" (arXiv 2018)**
- Link: https://arxiv.org/abs/1804.01028
- Why read it: Good reference for latency, digital control architecture, and why total loop delay matters in optical feedback systems. Read before thinking about loop bandwidth.

---

## Scanning Transfer Cavity

**Pultinevicius et al. — "Scanning transfer cavity lock" (arXiv 2023)**
- Link: https://arxiv.org/abs/2307.10217
- Why read it: Useful context for multi-laser scanning transfer cavity locking. Relevant for later extensions beyond single-laser PI lock.

---

## Fixed-Point Arithmetic

**★ Project F — "Fixed-Point Numbers in Verilog"**
- Link: https://projectf.io/posts/fixed-point-numbers-in-verilog/
- Why read it: The first reference to read before writing any PI or filter arithmetic. Covers fixed-point representation, bit growth, and why division is not straightforward in FPGA RTL.

**★ EmbeddedRelated — "How to Build a Fixed-Point PI Controller That Just Works"**
- Link: https://www.embeddedrelated.com/showarticle/121.php
- Why read it: Excellent practical notes on fixed-point PI, saturation, and anti-windup. Translate the C ideas into RTL. Read alongside pi_controller.sv development.

**Kulisz — "A Hardware Implementation of the PID Algorithm Using FPGA Technology" (Electronics 2024)**
- Link: https://www.mdpi.com/2079-9292/13/8/1598
- Why read it: Good hardware PID reference for serialized vs parallel implementation ideas. v0 stays simpler but this is useful background.

**Analog Devices MT-027 — "ADC Architectures III: Sigma-Delta ADC Basics"**
- Link: https://www.analog.com/media/en/training-seminars/tutorials/MT-027.pdf
- Why read it: Not laser-lock specific, but useful for thinking about ADC data formats, quantization, latency, and digital filtering when integrating real hardware.

---

## IIR and FIR Filter Implementation

**Walker-Howell — "Parametric IIR Filtering on an FPGA"**
- Link: https://ashrafi.sdsu.edu/PDF/Parametric_IIR_Filtering_on_an_FPGA.pdf
- Why read it: More advanced than the v0 first-order filter, but shows FPGA IIR implementation, coefficient handling, testing, and verification workflow.

**ZipCPU DSP — FIR/filter implementation habits**
- Link: https://zipcpu.com/dsp/2017/09/15/fastfir.html
- Why read it: Useful for thinking about streaming DSP structure, valid signals, and test-driven FPGA filter development.

---

## Verilog Coding and Verification

**★ ZipCPU Tutorial — "Verilog, Formal Verification, and Verilator"**
- Link: https://zipcpu.com/tutorial/
- Why read it: Good engineering habits for debugging FPGA designs with open-source tooling. Read early and refer back often.

**ZipCPU — "Building an AXI-Lite Slave the Easy Way"**
- Link: https://zipcpu.com/blog/2020/03/08/easyaxil.html
- Why read it: Use later if/when converting the simple register file into a real AXI-Lite peripheral at M3+.

---

## Simulation Tooling

**Verilator Official Documentation**
- Link: https://verilator.org/guide/latest/verilating.html
- Why read it: Use for linting and fast compiled simulation. Strongest with synthesizable RTL plus C++ harnesses. Remember it is not ideal for delay-heavy SV testbenches.

---

## Board and Hardware Reference

**Red Pitaya STEMlab FPGA Development Docs**
- Link: https://redpitaya.readthedocs.io/en/latest/intro.html
- Why read it: Useful hardware reference for fast analog I/O and FPGA development even if POSM does not use Red Pitaya directly.

**Red Pitaya FPGA Project Top-Level Example**
- Link: https://redpitaya.readthedocs.io/en/latest/developerGuide/fpga/projects/top.html
- Why read it: Good example of board-specific FPGA project organization and register maps.

---

## Reference Codebases

**quartiq/redpid — Digital servo reference project**
- Link: https://github.com/quartiq/redpid
- Why read it: Useful reference for a Red Pitaya digital-servo style project. POSM v0 should stay simpler and direct SystemVerilog but this is useful architectural context.

**Linien GitHub**
- Link: https://github.com/linien-org/linien
- Why read it: Full open-source laser lock codebase. Uses Migen/ARTIQ heritage. Do not copy directly but useful to see how a mature system is structured.

**Migen Documentation**
- Link: https://m-labs.hk/migen/manual/introduction.html
- Why read it: Only needed if reading Linien source code, which uses Migen for RTL generation.

---

## Reading Order for a New Team Member

1. Jørgensen et al. 2016 — whole system overview
2. Project F fixed-point Verilog — before writing any arithmetic
3. EmbeddedRelated fixed-point PI — before writing pi_controller.sv
4. Linien AIP RSI paper — signal chain and integer width discussion
5. ZipCPU tutorial — coding and debug habits
6. Tourigny-Plante et al. — loop latency and bandwidth thinking
7. Everything else as needed per module
