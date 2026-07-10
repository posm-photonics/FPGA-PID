"""
tb_trace_capture.py

Minimal smoke test for trace_capture.py. Not a substitute for the
full test list in packet section 12.4 ("decimation, buffer write/read,
overflow, ready flag") -- checks the basic start -> capture N pairs ->
ready -> readback flow.

Run with:
    python3 -m sim.tb_modules.tb_trace_capture
"""

from amaranth.sim import Simulator
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from rtl.control.trace_capture import TraceCapture
from rtl.bus.register_defs import (
    ADDR_TRACE_CONFIG, ADDR_TRACE_START, ADDR_TRACE_LENGTH,
    ADDR_TRACE_DECIM, ADDR_TRACE_STATUS, ADDR_TRACE_READ_ADDR,
    ADDR_TRACE_READ_DATA_X, ADDR_TRACE_READ_DATA_Y,
    TRACE_CFG_ENABLE, TRACE_STAT_READY,
)


def bus_write(ctx, dut, addr, data):
    ctx.set(dut.adr, addr)
    ctx.set(dut.dat_w, data & 0xFFFF_FFFF)
    ctx.set(dut.we, 1)
    ctx.set(dut.stb, 1)
    yield
    ctx.set(dut.we, 0)
    ctx.set(dut.stb, 0)
    yield


def bus_read(ctx, dut, addr):
    ctx.set(dut.adr, addr)
    ctx.set(dut.we, 0)
    ctx.set(dut.stb, 1)
    yield
    val = ctx.get(dut.dat_r)
    ctx.set(dut.stb, 0)
    yield
    return val


def main():
    dut = TraceCapture(depth=64, dac_w=16, err_w=20)
    sim = Simulator(dut)
    sim.add_clock(1e-8)

    N = 8

    def bench(ctx):
        yield from bus_write(ctx, dut, ADDR_TRACE_CONFIG, 1 << TRACE_CFG_ENABLE)
        yield from bus_write(ctx, dut, ADDR_TRACE_LENGTH, N)
        yield from bus_write(ctx, dut, ADDR_TRACE_DECIM, 1)
        yield from bus_write(ctx, dut, ADDR_TRACE_START, 1)

        for i in range(N):
            ctx.set(dut.scan_code, i * 10)
            ctx.set(dut.error_sample, i * 3 - 5)
            ctx.set(dut.sample_valid, 1)
            yield
            ctx.set(dut.sample_valid, 0)
            yield

        yield
        status = yield from bus_read(ctx, dut, ADDR_TRACE_STATUS)
        assert status & (1 << TRACE_STAT_READY), f"expected ready, got status={status:#x}"

        yield from bus_write(ctx, dut, ADDR_TRACE_READ_ADDR, 3)
        yield
        x = yield from bus_read(ctx, dut, ADDR_TRACE_READ_DATA_X)
        y = yield from bus_read(ctx, dut, ADDR_TRACE_READ_DATA_Y)
        print(f"pair[3] = (X={x}, Y={y})")
        assert x == 30, f"expected X=30, got {x}"
        assert (y & 0xFFFFF) == (4 & 0xFFFFF), f"expected Y=4, got {y}"

        print("tb_trace_capture: OK")

    sim.add_testbench(bench)
    sim.run()


if __name__ == "__main__":
    main()
