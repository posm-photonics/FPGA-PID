# nco.py
# Numerically-Controlled Oscillator for PDH modulation/demodulation
#
# Architecture:
#   32-bit phase accumulator advancing by `freq_word` each clock.
#   256-entry quarter-wave sine LUT (16-bit signed output).
#   Phase-coherent sin/cos from the same accumulator.
#
# Ports:
#   freq_word    : unsigned 32b  — phase increment per clock
#   phase_offset : unsigned 32b  — added to accumulator before LUT lookup
#   o_sin        : signed 16b   — sine output
#   o_cos        : signed 16b   — cosine output (sin + 90°)
#   o_phase      : unsigned 32b — raw phase accumulator (for diagnostics)
#
# Output amplitude: ±32767  (Q1.14, but treated as integer codes downstream)
# Latency: 1 clock cycle (registered output)
#
# Quarter-wave symmetry:
#   The LUT stores sin(θ) for θ in [0, π/2) — 256 entries.
#   Full-wave reconstruction uses quadrant bits from the phase accumulator:
#     quadrant 0 (0–π/2)   :  LUT[index]
#     quadrant 1 (π/2–π)   :  LUT[255 - index]
#     quadrant 2 (π–3π/2)  : -LUT[index]
#     quadrant 3 (3π/2–2π) : -LUT[255 - index]
#
# Fixed-point format:
#   Phase accumulator : 32-bit unsigned, full revolution = 2^32
#   Sine/cosine output: 16-bit signed, max = +32767, min = -32767
#   (note: -32768 is intentionally avoided for symmetry)

import math
from amaranth import *


def _generate_quarter_sine_lut(n_entries=256, amplitude=32767):
    """Generate quarter-wave sine LUT values.

    Returns a list of `n_entries` integers representing sin(θ) for
    θ in [0, π/2).  The maximum value is `amplitude` (default 32767),
    and the first entry is 0.
    """
    lut = []
    for i in range(n_entries):
        # θ spans [0, π/2) — the endpoint π/2 is NOT included because
        # quadrant-1 mirroring (255 - index) reaches it.
        theta = (i / n_entries) * (math.pi / 2)
        value = int(round(amplitude * math.sin(theta)))
        lut.append(value)
    return lut


class NCO(Elaboratable):
    """Numerically-Controlled Oscillator with quarter-wave sine LUT.

    Parameters
    ----------
    phase_w : int
        Phase accumulator width (default 32).
    lut_depth : int
        Number of entries in the quarter-wave LUT (default 256).
    out_w : int
        Output sample width in bits (default 16, signed).
    """

    # LUT depth exponent: 2^LUT_BITS = lut_depth
    LUT_BITS = 8  # 256 entries

    def __init__(self, phase_w=32, lut_depth=256, out_w=16):
        assert lut_depth == (1 << self.LUT_BITS), \
            "lut_depth must be a power of 2 matching LUT_BITS"

        self.phase_w = phase_w
        self.lut_depth = lut_depth
        self.out_w = out_w

        # --- input ports ---
        self.freq_word = Signal(phase_w)          # phase increment per clock
        self.phase_offset = Signal(phase_w)       # added before LUT lookup

        # --- output ports ---
        self.o_sin = Signal(signed(out_w))        # sine output
        self.o_cos = Signal(signed(out_w))        # cosine output (sin + 90°)
        self.o_phase = Signal(phase_w)            # raw accumulator (diagnostic)

        # Pre-compute the LUT at elaboration time
        self._lut_values = _generate_quarter_sine_lut(lut_depth, (1 << (out_w - 1)) - 1)

    def elaborate(self, platform):
        m = Module()

        phase_acc = Signal(self.phase_w)

        # Phase accumulator: advances by freq_word every clock
        m.d.sync += phase_acc.eq(phase_acc + self.freq_word)
        m.d.comb += self.o_phase.eq(phase_acc)

        # --- LUT as Amaranth Memory ---
        # Store the quarter-wave table in a Memory for clean synthesis.
        # Amaranth <0.5 uses width instead of shape.
        # Since we have negative values in init, we should convert them to unsigned for Memory,
        # but the init list is Python ints which might work. Let's just use width.
        # Actually, it's safer to store unsigned and cast.
        unsigned_lut = [val & ((1 << self.out_w) - 1) for val in self._lut_values]
        lut_mem = Memory(width=self.out_w, depth=self.lut_depth, init=unsigned_lut)
        m.submodules.sin_rd = sin_rd = lut_mem.read_port()
        m.submodules.cos_rd = cos_rd = lut_mem.read_port()

        # --- Phase decomposition ---
        # The top 2 bits of the effective phase select the quadrant.
        # The next LUT_BITS bits select the LUT index.
        # For sine: effective_phase = phase_acc + phase_offset
        # For cosine: effective_phase = phase_acc + phase_offset + 2^30 (90° shift)

        sin_phase = Signal(self.phase_w)
        cos_phase = Signal(self.phase_w)
        quarter_shift = (1 << (self.phase_w - 2))  # 90° = 2^30 for 32-bit

        m.d.comb += [
            sin_phase.eq(phase_acc + self.phase_offset),
            cos_phase.eq(phase_acc + self.phase_offset + quarter_shift),
        ]

        # Quadrant and index extraction
        sin_quadrant = Signal(2)
        cos_quadrant = Signal(2)
        sin_index_raw = Signal(self.LUT_BITS)
        cos_index_raw = Signal(self.LUT_BITS)
        sin_index = Signal(self.LUT_BITS)
        cos_index = Signal(self.LUT_BITS)

        # Top 2 bits = quadrant, next LUT_BITS bits = index
        m.d.comb += [
            sin_quadrant.eq(sin_phase >> (self.phase_w - 2)),
            cos_quadrant.eq(cos_phase >> (self.phase_w - 2)),
            sin_index_raw.eq(sin_phase[self.phase_w - 2 - self.LUT_BITS:self.phase_w - 2]),
            cos_index_raw.eq(cos_phase[self.phase_w - 2 - self.LUT_BITS:self.phase_w - 2]),
        ]

        # Mirror index in quadrants 1 and 3 (odd quadrants)
        with m.If(sin_quadrant[0]):
            m.d.comb += sin_index.eq((self.lut_depth - 1) - sin_index_raw)
        with m.Else():
            m.d.comb += sin_index.eq(sin_index_raw)

        with m.If(cos_quadrant[0]):
            m.d.comb += cos_index.eq((self.lut_depth - 1) - cos_index_raw)
        with m.Else():
            m.d.comb += cos_index.eq(cos_index_raw)

        # LUT read
        m.d.comb += [
            sin_rd.addr.eq(sin_index),
            cos_rd.addr.eq(cos_index),
        ]

        # --- Output with sign correction (registered, 1-cycle latency) ---
        # Negate in quadrants 2 and 3 (upper half)
        # Pipeline the quadrant for alignment with 1-cycle memory read
        sin_quadrant_d = Signal(2)
        cos_quadrant_d = Signal(2)
        m.d.sync += [
            sin_quadrant_d.eq(sin_quadrant),
            cos_quadrant_d.eq(cos_quadrant),
        ]

        # sin output
        with m.If(sin_quadrant_d[1]):
            m.d.comb += self.o_sin.eq(-sin_rd.data.as_signed())
        with m.Else():
            m.d.comb += self.o_sin.eq(sin_rd.data.as_signed())

        # cos output
        with m.If(cos_quadrant_d[1]):
            m.d.comb += self.o_cos.eq(-cos_rd.data.as_signed())
        with m.Else():
            m.d.comb += self.o_cos.eq(cos_rd.data.as_signed())

        return m
