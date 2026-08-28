# nco.py
# Numerically-Controlled Oscillator for PDH modulation/demodulation
#
# Architecture:
#   32-bit phase accumulator advancing by `freq_word` each clock.
#   256-entry quarter-wave sine LUT (16-bit signed output).
#   Phase-coherent sin/cos from the same accumulator.
#
# This file provides two classes:
#
#   SineLUT : a stateless phase-to-sine/cosine converter. Takes an
#             absolute phase word and produces sin/cos. It owns no
#             accumulator.
#   NCO     : a phase accumulator plus one SineLUT, i.e. the original
#             module. Its existing port list is unchanged.
#
# AUDIT FIX (S1-8) -- why the split exists
# ----------------------------------------
# The PDH front end needs TWO references derived from ONE accumulator at
# TWO independent phases: the modulation waveform at phase 0, and the
# mixer reference at phase + demod_phase. Previously a single NCO output
# fed both, so PDH_DEMOD_PHASE rotated the modulation and the mixer
# reference together, their relative phase was pinned at zero, and the
# register was a no-op.
#
# Both reference sources agree that these must be independent:
#   * Linien: Modulate owns the accumulator and drives its CORDIC from
#     the bare phase; Demodulate takes that phase as an input and adds
#     its own `delay` CSR. The offset is applied on the demod side only.
#   * POSM_project_FPGALock.pdf section 5.1: "AD9959 CH A: 10 MHz EOM
#     drive, phase = 0 deg / AD9959 CH B: 10 MHz mixer LO,
#     phase = phi_demod". Section 4.2 Eq. 11 explains why it matters:
#     the LO phase selects which quadrature of the RF signal is measured.
#
# Ports (NCO):
#   freq_word    : unsigned 32b  - phase increment per clock
#   phase_offset : unsigned 32b  - added to accumulator before LUT lookup
#   phase_reset  : 1b            - synchronous accumulator clear
#   o_sin        : signed 16b    - sine output
#   o_cos        : signed 16b    - cosine output (sin + 90 deg)
#   o_phase      : unsigned 32b  - raw phase accumulator
#
# o_phase is the accumulator value BEFORE phase_offset is applied, and it
# is combinational off the accumulator register. A SineLUT driven from
# o_phase therefore produces its output on exactly the same clock as the
# NCO's own o_sin/o_cos: both see the cycle-N accumulator and both
# register their result on cycle N+1.
#
# Output amplitude: +/-32766 (Q1.14, treated as integer codes downstream)
# Latency: 1 clock cycle (registered output)
#
# Quarter-wave symmetry:
#   The LUT stores sin(theta) for theta in [0, pi/2) - 256 entries.
#   Full-wave reconstruction uses quadrant bits from the phase word:
#     quadrant 0 (0-pi/2)     :  LUT[index]
#     quadrant 1 (pi/2-pi)    :  LUT[255 - index]
#     quadrant 2 (pi-3pi/2)   : -LUT[index]
#     quadrant 3 (3pi/2-2pi)  : -LUT[255 - index]
#
# Fixed-point format:
#   Phase accumulator : 32-bit unsigned, full revolution = 2^32
#   Sine/cosine output: 16-bit signed

import math
from amaranth import *


def _generate_quarter_sine_lut(n_entries=256, amplitude=32767):
    """Generate quarter-wave sine LUT values.

    Returns `n_entries` integers sampling sin(theta) for theta in
    [0, pi/2), using MIDPOINT sampling: entry i is the sine at the centre
    of phase bin i, not at its lower edge.

    AUDIT FIX (NCO quarter-wave mirror error)
    -----------------------------------------
    The previous version sampled the lower edge of each bin
    (theta = i/n * pi/2). Reconstruction mirrors the odd quadrants with
    `255 - index`, but under edge sampling the exact mirror of bin i is
    `256 - i`, which is out of range. The result was a systematic phase
    error of half a bin (0.35 degrees) in quadrants 1 and 3 only.

    A quadrant-dependent phase error breaks the waveform's quarter-wave
    symmetry, which puts even harmonics into the reference. In a PDH
    demodulator an even-harmonic component of the reference mixes down to
    a DC term in the baseband error signal, which is a lock-point offset.

    Midpoint sampling makes the mirror exact. For a phase in quadrant 1
    the true value is

        sin(pi/2 + (i+0.5)/n * pi/2) = sin(((n-1-i)+0.5)/n * pi/2)

    which is precisely LUT[n-1-i]. The reconstruction below is then
    correct in all four quadrants with no residual phase error.
    """
    lut = []
    for i in range(n_entries):
        theta = ((i + 0.5) / n_entries) * (math.pi / 2)
        value = int(round(amplitude * math.sin(theta)))
        # Keep the peak one code below full scale so that the negated
        # quadrants stay representable and sin/cos remain symmetric about
        # zero (-32768 has no positive counterpart in 16-bit signed).
        lut.append(min(value, amplitude - 1))
    return lut


class SineLUT(Elaboratable):
    """Stateless phase-to-sine/cosine converter (quarter-wave LUT).

    Parameters
    ----------
    phase_w : int
        Width of the incoming phase word (default 32).
    lut_depth : int
        Number of entries in the quarter-wave LUT (default 256).
    out_w : int
        Output sample width in bits (default 16, signed).

    Ports
    -----
    phase : unsigned input, phase_w bits - absolute phase
    o_sin : signed output, out_w bits
    o_cos : signed output, out_w bits (phase + 90 degrees)

    Latency: 1 clock cycle.
    """

    LUT_BITS = 8  # 256 entries

    def __init__(self, phase_w=32, lut_depth=256, out_w=16):
        assert lut_depth == (1 << self.LUT_BITS), \
            "lut_depth must be a power of 2 matching LUT_BITS"

        self.phase_w = phase_w
        self.lut_depth = lut_depth
        self.out_w = out_w

        self.phase = Signal(phase_w)
        self.o_sin = Signal(signed(out_w))
        self.o_cos = Signal(signed(out_w))

        self._lut_values = _generate_quarter_sine_lut(
            lut_depth, (1 << (out_w - 1)) - 1)

    def elaborate(self, platform):
        m = Module()

        # Store the quarter-wave table in a Memory for clean synthesis.
        # Values are held as raw two's-complement bit patterns and cast
        # back with .as_signed() on read.
        unsigned_lut = [val & ((1 << self.out_w) - 1) for val in self._lut_values]
        lut_mem = Memory(width=self.out_w, depth=self.lut_depth, init=unsigned_lut)
        m.submodules.sin_rd = sin_rd = lut_mem.read_port()
        m.submodules.cos_rd = cos_rd = lut_mem.read_port()

        # --- Phase decomposition ---
        # Top 2 bits select the quadrant, the next LUT_BITS bits the index.
        sin_phase = Signal(self.phase_w)
        cos_phase = Signal(self.phase_w)
        quarter_shift = (1 << (self.phase_w - 2))  # 90 degrees

        m.d.comb += [
            sin_phase.eq(self.phase),
            cos_phase.eq(self.phase + quarter_shift),
        ]

        sin_quadrant = Signal(2)
        cos_quadrant = Signal(2)
        sin_index_raw = Signal(self.LUT_BITS)
        cos_index_raw = Signal(self.LUT_BITS)
        sin_index = Signal(self.LUT_BITS)
        cos_index = Signal(self.LUT_BITS)

        m.d.comb += [
            sin_quadrant.eq(sin_phase >> (self.phase_w - 2)),
            cos_quadrant.eq(cos_phase >> (self.phase_w - 2)),
            sin_index_raw.eq(
                sin_phase[self.phase_w - 2 - self.LUT_BITS:self.phase_w - 2]),
            cos_index_raw.eq(
                cos_phase[self.phase_w - 2 - self.LUT_BITS:self.phase_w - 2]),
        ]

        # Mirror the index in the odd quadrants (1 and 3).
        with m.If(sin_quadrant[0]):
            m.d.comb += sin_index.eq((self.lut_depth - 1) - sin_index_raw)
        with m.Else():
            m.d.comb += sin_index.eq(sin_index_raw)

        with m.If(cos_quadrant[0]):
            m.d.comb += cos_index.eq((self.lut_depth - 1) - cos_index_raw)
        with m.Else():
            m.d.comb += cos_index.eq(cos_index_raw)

        m.d.comb += [
            sin_rd.addr.eq(sin_index),
            cos_rd.addr.eq(cos_index),
        ]

        # --- Output with sign correction (registered, 1-cycle latency) ---
        # Negate in the upper half (quadrants 2 and 3). The quadrant is
        # pipelined by one cycle to line up with the synchronous LUT read.
        sin_quadrant_d = Signal(2)
        cos_quadrant_d = Signal(2)
        m.d.sync += [
            sin_quadrant_d.eq(sin_quadrant),
            cos_quadrant_d.eq(cos_quadrant),
        ]

        with m.If(sin_quadrant_d[1]):
            m.d.comb += self.o_sin.eq(-sin_rd.data.as_signed())
        with m.Else():
            m.d.comb += self.o_sin.eq(sin_rd.data.as_signed())

        with m.If(cos_quadrant_d[1]):
            m.d.comb += self.o_cos.eq(-cos_rd.data.as_signed())
        with m.Else():
            m.d.comb += self.o_cos.eq(cos_rd.data.as_signed())

        return m


class NCO(Elaboratable):
    """Numerically-Controlled Oscillator: phase accumulator + SineLUT.

    Parameters
    ----------
    phase_w : int
        Phase accumulator width (default 32).
    lut_depth : int
        Number of entries in the quarter-wave LUT (default 256).
    out_w : int
        Output sample width in bits (default 16, signed).
    """

    LUT_BITS = SineLUT.LUT_BITS

    def __init__(self, phase_w=32, lut_depth=256, out_w=16):
        self.phase_w = phase_w
        self.lut_depth = lut_depth
        self.out_w = out_w

        # --- input ports ---
        self.freq_word = Signal(phase_w)          # phase increment per clock
        self.phase_offset = Signal(phase_w)       # added before LUT lookup
        # Synchronous phase-accumulator clear. Linien's Modulate has the
        # same control (`sync_phase`); without it there is no way to put
        # the oscillator into a known phase after a reconfiguration.
        self.phase_reset = Signal()

        # --- output ports ---
        self.o_sin = Signal(signed(out_w))        # sine output
        self.o_cos = Signal(signed(out_w))        # cosine output (sin + 90 deg)
        # Raw accumulator, BEFORE phase_offset, combinational off the
        # register. Drive a parallel SineLUT from this to obtain a second
        # reference at an independent phase that stays sample-aligned
        # with o_sin / o_cos.
        self.o_phase = Signal(phase_w)

    def elaborate(self, platform):
        m = Module()

        phase_acc = Signal(self.phase_w)

        # Phase accumulator: advances by freq_word every clock.
        with m.If(self.phase_reset):
            m.d.sync += phase_acc.eq(0)
        with m.Else():
            m.d.sync += phase_acc.eq(phase_acc + self.freq_word)

        m.d.comb += self.o_phase.eq(phase_acc)

        m.submodules.lut = lut = SineLUT(
            phase_w=self.phase_w, lut_depth=self.lut_depth, out_w=self.out_w)

        m.d.comb += [
            lut.phase.eq(phase_acc + self.phase_offset),
            self.o_sin.eq(lut.o_sin),
            self.o_cos.eq(lut.o_cos),
        ]

        return m
