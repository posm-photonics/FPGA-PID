from amaranth import *
from amaranth.sim import *
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from top.lock_core_top import LockCoreTop
from rtl.bus.register_defs import (
    ADDR_CONTROL,
    ADDR_MODE,
    ADDR_TRACE_CONFIG,
    ADDR_TRACE_LENGTH,
    ADDR_TRACE_START,
    ADDR_SLOW_CTRL_CONFIG,
    ADDR_SLOW_RECENTER_TARGET,
    ADDR_SLOW_RECENTER_GAIN,
    ADDR_SLOW_OUT_MIN,
    ADDR_SLOW_OUT_MAX,
    ADDR_SLOW_OUT_SAFE,
    ADDR_SLOW_BIAS,
    ADDR_FAULT_ENABLE,
    ADDR_FAULT_CLEAR,
    CTRL_GLOBAL_ENABLE,
    CTRL_LOCK_ENABLE_REQUEST,
    CTRL_TRACE_CAPTURE_ENABLE,
    CTRL_AUTOLOCK_ENABLE,
    CTRL_SLOW_RECENTER_ENABLE,
)


class FakeMTSModel:
    def __init__(self):
        self.offset = -120
        self.slope = 0.8
        self.amplitude = 2200
        self.noise = 5

    def sample(self, scan_code, time_index):
        feature = self.amplitude * (1.0 - ((scan_code - 2000) / 4000.0) ** 2)
        return int(self.offset + feature * self.slope + ((time_index % 7) - 3) * self.noise)


def write_reg(dut, addr, value):
    yield dut.adr.eq(addr)
    yield dut.dat_w.eq(value)
    yield dut.we.eq(1)
    yield dut.stb.eq(1)
    yield
    yield dut.we.eq(0)
    yield dut.stb.eq(0)
    yield


def tb(dut):
    yield dut.rst.eq(1)
    for _ in range(4):
        yield
    yield dut.rst.eq(0)

    yield from write_reg(dut, ADDR_CONTROL, 
                            (1 << CTRL_GLOBAL_ENABLE) | (1 << CTRL_LOCK_ENABLE_REQUEST))
    yield from write_reg(dut, ADDR_MODE, 0)
    yield from write_reg(dut, ADDR_TRACE_CONFIG, 1)
    yield from write_reg(dut, ADDR_TRACE_LENGTH, 256)
    yield from write_reg(dut, ADDR_SLOW_CTRL_CONFIG, (1 << CTRL_TRACE_CAPTURE_ENABLE) 
            | (1 << CTRL_AUTOLOCK_ENABLE) | (1 << CTRL_SLOW_RECENTER_ENABLE))
    yield from write_reg(dut, ADDR_SLOW_BIAS, 0)
    yield from write_reg(dut, ADDR_SLOW_RECENTER_TARGET, 0)
    yield from write_reg(dut, ADDR_SLOW_RECENTER_GAIN, 64)
    yield from write_reg(dut, ADDR_SLOW_OUT_MIN, -4096)
    yield from write_reg(dut, ADDR_SLOW_OUT_MAX, 4096)
    yield from write_reg(dut, ADDR_SLOW_OUT_SAFE, 0)
    yield from write_reg(dut, ADDR_FAULT_ENABLE, 0xFFFF)

    model = FakeMTSModel()
    for idx in range(400):
        scan_code = 1000 + ((idx // 4) % 200) * 20
        sample = model.sample(scan_code, idx)
        yield dut.i_adc_ch0.eq(sample)
        yield dut.i_adc_valid.eq(1)
        yield dut.i_adc_overrange_ch0.eq(0)
        yield dut.i_adc_overrange_ch1.eq(0)
        yield dut.i_external_interlock.eq(0)
        yield dut.i_feature_selected.eq(idx > 150)
        yield

    for _ in range(40):
        yield dut.i_adc_valid.eq(0)
        yield


if __name__ == "__main__":
    dut = LockCoreTop()
    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_process(tb)
    sim.run()
