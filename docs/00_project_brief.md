# 00 Project Brief

**Project:** POSM FPGA Laser-Lock Core
**Version:** 2

---

## Mission

Build a hardware-agnostic FPGA control core that takes signed ADC samples from a spectroscopy error signal, computes a control error, filters it, applies PI control, clamps the result, and produces signed DAC commands for laser current or piezo control.

---

## Scope

**In scope:**
- Fixed-point SystemVerilog PI servo engine
- Simulation-first development
- Generic streams and registers — no vendor-specific assumptions in the core

**Out of scope at v0:**
- Analog front end, PCB, laser driver, HV piezo driver
- Red Pitaya compatibility or Linien clone
- Networking, GUI, autolock

---

## Milestones

| # | Deliverable |
|---|-------------|
| M0 | Repo skeleton, simulation tooling, sat_math, error_calc |
| M1 | IIR filter, PI controller, output limiter, testbenches, plots |
| M2 | Ramp scan, mode mux, lock FSM |
| M3 | Register bank, software-visible config words |
| M4 | Dev-board demo with real GPIO/DAC output |
| M5 | Live spectroscopy signal scanned and locked |

---

## First Deliverable

A simulated lock core with testbenches and plots showing step response convergence,
output clamping, and anti-windup behavior. No hardware until M4.
