"""
trace_capture.py

Implements trace_capture.sv from section 8.9 / register map section 11.7
of the onboarding packet.

    "Capture line-shape traces for GUI display and feature selection.
    A trace sample is a pair: (Xi, Yi) = (slow scan code, MTS error
    sample). This block is not in the fast feedback path."

This module is explicitly Category B (acquisition/diagnostics, section
6.2): it must never sit in the ADC_CH0 -> DAC_FAST latency-critical
path (section 7.2 forbids "trace capture or BRAM readout" in the fast
path). It is driven by its own `sample_valid` strobe -- normally tied
to the same cadence as ramp_scan's updates during WIDE_SCAN/ZOOM_SCAN,
not to the 1 MHz fast-loop clock enable.

Register map (byte offsets, matches section 11.7 exactly):

    0x180 TRACE_CONFIG      R/W  bit0 enable, bit1 channel_sel
    0x184 TRACE_START       W    write-any to arm+start a capture
    0x188 TRACE_LENGTH      R/W  number of pairs to capture (<= DEPTH)
    0x18C TRACE_DECIM       R/W  capture every Nth valid sample (>=1)
    0x190 TRACE_STATUS      R    bit0 busy, bit1 ready, bit2 overflow
    0x194 TRACE_WRITE_PTR   R    current internal write pointer
    0x198 TRACE_READ_ADDR   R/W  host readback address
    0x19C TRACE_READ_DATA_X R    scan code at TRACE_READ_ADDR
    0x1A0 TRACE_READ_DATA_Y R    error sample at TRACE_READ_ADDR

Integration note: this module decodes the *absolute* byte addresses
above off a shared bus. A parent (lock_core_top) is expected to fan
the same adr/dat_w/we/stb signals out to every register-owning
submodule and OR (or mux) their dat_r outputs together, since each
submodule drives dat_r = 0 when its own address range isn't hit
(see the read mux `Default` case below). This mirrors how
register_bank.py behaves and avoids needing a second, separate
address-decode layer for a first cut.
"""

from amaranth import Module, Signal, Elaboratable, Cat, Mux
from amaranth.hdl.mem import Memory

from .register_defs import (
    ADDR_TRACE_CONFIG, ADDR_TRACE_START, ADDR_TRACE_LENGTH,
    ADDR_TRACE_DECIM, ADDR_TRACE_STATUS, ADDR_TRACE_WRITE_PTR,
    ADDR_TRACE_READ_ADDR, ADDR_TRACE_READ_DATA_X, ADDR_TRACE_READ_DATA_Y,
    TRACE_CFG_ENABLE, TRACE_CFG_CHANNEL_SEL,
    TRACE_STAT_BUSY, TRACE_STAT_READY, TRACE_STAT_OVERFLOW,
    DAC_W, ERR_W,
)


class TraceCapture(Elaboratable):
    """
    Parameters
    ----------
    depth : int
        Number of (X, Y) pairs the trace buffer can hold. Must be a
        power of two for the address width math below. TRACE_LENGTH
        is clipped to this at capture-start time.
    dac_w : int
        Width of the scan-code (X) sample, signed. Defaults to the
        DAC_SLOW code width since the trace's X axis is
        "slow_dac_code" per Eq. 24 in the packet.
    err_w : int
        Width of the corrected error (Y) sample, signed.
    """

    def __init__(self, depth=4096, dac_w=DAC_W, err_w=ERR_W):
        assert depth & (depth - 1) == 0, "depth must be a power of two"
        self.depth = depth
        self.adr_w = depth.bit_length() - 1
        self.dac_w = dac_w
        self.err_w = err_w

        # --- streaming capture inputs (from ramp_scan / error_calc) ---
        self.scan_code    = Signal(signed(dac_w))
        self.error_sample = Signal(signed(err_w))
        self.sample_valid = Signal()   # pulse: new (scan_code, error_sample) pair available
        self.ch1_sample   = Signal(signed(err_w))  # optional raw-RF diagnostic channel (ADC_CH1)

        # --- bus (shared with other register-owning modules) ---
        self.adr   = Signal(12)
        self.dat_w = Signal(32)
        self.dat_r = Signal(32)
        self.we    = Signal()
        self.stb   = Signal()

        # --- external status outputs, mirrored for STATUS register
        # in register_bank (trace_ready bit) ---
        self.trace_ready = Signal()
        self.busy        = Signal()

    def elaborate(self, platform):
        m = Module()

        depth, adr_w = self.depth, self.adr_w

        mem_x = Memory(width=self.dac_w, depth=depth)
        mem_y = Memory(width=self.err_w, depth=depth)
        m.submodules.mem_x_wp = wp_x = mem_x.write_port()
        m.submodules.mem_y_wp = wp_y = mem_y.write_port()
        m.submodules.mem_x_rp = rp_x = mem_x.read_port(transparent=False)
        m.submodules.mem_y_rp = rp_y = mem_y.read_port(transparent=False)

        # ---------------- registers ----------------
        config       = Signal(32)
        length_reg   = Signal(adr_w + 1)   # requested length, clipped to depth at start
        decim_reg    = Signal(16, reset=1)
        read_addr    = Signal(adr_w)

        wr_ptr       = Signal(adr_w)
        capture_len  = Signal(adr_w + 1)   # clipped length actually used this run
        decim_cnt    = Signal(16)

        busy         = Signal()
        ready        = Signal()
        overflow     = Signal()

        enable      = config[TRACE_CFG_ENABLE]
        channel_sel = config[TRACE_CFG_CHANNEL_SEL]

        word_adr = self.adr[2:]

        m.d.comb += [
            self.busy.eq(busy),
            self.trace_ready.eq(ready),
        ]

        # ---------------- start pulse detect ----------------
        start_write = Signal()
        m.d.comb += start_write.eq(
            self.stb & self.we & (word_adr == (ADDR_TRACE_START >> 2))
        )

        # clip requested length to buffer depth
        length_clipped = Signal(adr_w + 1)
        with m.If(length_reg > depth):
            m.d.comb += length_clipped.eq(depth)
        with m.Else():
            m.d.comb += length_clipped.eq(length_reg)

        # ---------------- write-side bus decode ----------------
        with m.If(self.stb & self.we):
            with m.Switch(word_adr):
                with m.Case(ADDR_TRACE_CONFIG >> 2):
                    m.d.sync += config.eq(self.dat_w)
                with m.Case(ADDR_TRACE_LENGTH >> 2):
                    m.d.sync += length_reg.eq(self.dat_w[:adr_w + 1])
                with m.Case(ADDR_TRACE_DECIM >> 2):
                    # DECIM=0 is nonsensical ("every 0th sample"); clamp to 1.
                    with m.If(self.dat_w[:16] == 0):
                        m.d.sync += decim_reg.eq(1)
                    with m.Else():
                        m.d.sync += decim_reg.eq(self.dat_w[:16])
                with m.Case(ADDR_TRACE_READ_ADDR >> 2):
                    m.d.sync += read_addr.eq(self.dat_w[:adr_w])
                # TRACE_START handled below (pulse, not stored state)
                # TRACE_STATUS / WRITE_PTR / READ_DATA_X/Y ignore writes

        # ---------------- capture control ----------------
        capture_now = Signal()  # this cycle actually stores a sample

        with m.If(start_write):
            with m.If(busy):
                # a new start arrived while a capture was already
                # in flight -- flag it rather than silently dropping
                # or corrupting the in-progress buffer.
                m.d.sync += overflow.eq(1)
            with m.Else():
                m.d.sync += [
                    busy.eq(1),
                    ready.eq(0),
                    overflow.eq(0),
                    wr_ptr.eq(0),
                    decim_cnt.eq(0),
                    capture_len.eq(length_clipped),
                ]

        with m.Elif(busy & enable & self.sample_valid):
            with m.If(decim_cnt == (decim_reg - 1)):
                m.d.sync += decim_cnt.eq(0)
                m.d.comb += capture_now.eq(1)
            with m.Else():
                m.d.sync += decim_cnt.eq(decim_cnt + 1)

        with m.If(capture_now):
            m.d.sync += wr_ptr.eq(wr_ptr + 1)
            with m.If((wr_ptr + 1) >= capture_len):
                m.d.sync += [
                    busy.eq(0),
                    ready.eq(1),
                ]

        # writing to memory
        m.d.comb += [
            wp_x.addr.eq(wr_ptr),
            wp_x.data.eq(self.scan_code),
            wp_x.en.eq(capture_now),

            wp_y.addr.eq(wr_ptr),
            wp_y.data.eq(Mux(channel_sel, self.ch1_sample, self.error_sample)),
            wp_y.en.eq(capture_now),
        ]

        # ---------------- host readback ----------------
        m.d.comb += [
            rp_x.addr.eq(read_addr),
            rp_x.en.eq(1),
            rp_y.addr.eq(read_addr),
            rp_y.en.eq(1),
        ]

        status_word = Signal(32)
        m.d.comb += status_word.eq(Cat(busy, ready, overflow))

        # ---------------- read-side bus decode ----------------
        with m.Switch(word_adr):
            with m.Case(ADDR_TRACE_CONFIG >> 2):
                m.d.comb += self.dat_r.eq(config)
            with m.Case(ADDR_TRACE_LENGTH >> 2):
                m.d.comb += self.dat_r.eq(length_reg)
            with m.Case(ADDR_TRACE_DECIM >> 2):
                m.d.comb += self.dat_r.eq(decim_reg)
            with m.Case(ADDR_TRACE_STATUS >> 2):
                m.d.comb += self.dat_r.eq(status_word)
            with m.Case(ADDR_TRACE_WRITE_PTR >> 2):
                m.d.comb += self.dat_r.eq(wr_ptr)
            with m.Case(ADDR_TRACE_READ_ADDR >> 2):
                m.d.comb += self.dat_r.eq(read_addr)
            with m.Case(ADDR_TRACE_READ_DATA_X >> 2):
                m.d.comb += self.dat_r.eq(rp_x.data.as_signed())
            with m.Case(ADDR_TRACE_READ_DATA_Y >> 2):
                m.d.comb += self.dat_r.eq(rp_y.data.as_signed())
            with m.Default():
                m.d.comb += self.dat_r.eq(0)

        return m
