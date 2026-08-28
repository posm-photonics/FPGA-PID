# References

Full annotated reading list: `docs/08_reading_list.md`.

## The two sources this design is audited against

**Linien** — <https://github.com/linien-org/linien>,
Wiegand et al., arXiv:2203.02947.
Read `linien/gateware/logic/pid.py` before touching the PI controller
and `linien/gateware/logic/modulate.py` before touching the PDH path.
Those two files answer the two questions this project got wrong:

- `pid.py` shows the correct fixed-point integrator: accumulate at full
  precision in a wide register and shift only on read-out.
- `modulate.py` and `chains.py` show that the demodulation phase offset
  belongs to the demodulator alone. `Modulate` drives its CORDIC from
  the bare accumulator phase; `Demodulate` takes that same phase and
  adds its own `delay` CSR.

Treat it as a reference for structure, not code to port. Packet 15.1:
"Do not copy the whole Red Pitaya/Migen/server stack."

**POSM_project_FPGALock.pdf** — the onboarding packet, v3 draft.
The normative specification for this project: module list (section 8),
acquisition workflow (section 9), fault architecture (section 10),
canonical register map (section 11), verification plan (section 12),
acceptance checklist (section 14).

Where the repository knowingly diverges from the packet, the divergence
is recorded rather than hidden:

- Register addresses for 21 registers (`docs/05_register_map.md`).
- Digital demodulation in the fast path, which section 7.2 forbids
  (`rtl/dsp/pdh_frontend.py`).
- Target hardware: the packet specifies Ultra96V2 / ADC3664 / AD9117 and
  section 15.3 says "Do not make POSM a Red Pitaya compatibility
  project", while `docs/00_project_brief.md` lists "Red Pitaya
  compatibility or Linien clone" as out of scope at v0. The only board
  integration in this repo is Red Pitaya. That needs either an amended
  packet or a written rationale in
  `docs/07_hardware_integration_notes.md`.
