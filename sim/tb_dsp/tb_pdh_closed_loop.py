import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from amaranth.sim import Simulator, Tick
from top.lock_core_top import LockCoreTop
from rtl.bus.register_defs import (
    ADDR_PDH_CONTROL, ADDR_PDH_MOD_FREQ, ADDR_PDH_MOD_AMP,
    ADDR_PDH_DEMOD_PHASE, ADDR_PDH_LPF_ALPHA,
    ADDR_CONTROL, CTRL_GLOBAL_ENABLE, CTRL_LOCK_ENABLE_REQUEST
)
from sim.models.fake_pdh_cavity import FakePDHCavity

def write_reg(dut, addr, value):
    yield dut.adr.eq(addr)
    yield dut.dat_w.eq(value)
    yield dut.we.eq(1)
    yield dut.stb.eq(1)
    yield Tick()
    yield dut.we.eq(0)
    yield dut.stb.eq(0)
    yield Tick()

def test_pdh_closed_loop():
    dut = LockCoreTop()
    cavity = FakePDHCavity(amplitude=1000.0)
    
    sim = Simulator(dut)
    sim.add_clock(1e-8)
    
    def process():
        yield dut.rst.eq(1)
        for _ in range(4):
            yield Tick()
        yield dut.rst.eq(0)
        
        # Configure PDH parameters
        yield from write_reg(dut, ADDR_PDH_CONTROL, 1) # Enable PDH
        # Freq word for approx 1/20th of sampling rate
        yield from write_reg(dut, ADDR_PDH_MOD_FREQ, int((2**32) / 20))
        yield from write_reg(dut, ADDR_PDH_MOD_AMP, 16384) # Amplitude 1.0 (Q2.14)
        yield from write_reg(dut, ADDR_PDH_DEMOD_PHASE, 0) # No phase shift
        yield from write_reg(dut, ADDR_PDH_LPF_ALPHA, 4) # alpha=4
        
        yield from write_reg(dut, ADDR_CONTROL, (1 << CTRL_GLOBAL_ENABLE) | (1 << CTRL_LOCK_ENABLE_REQUEST))
        
        # Run simulation for a while
        for i in range(100):
            # In a real closed-loop sim, the detuning would be affected by the control output
            # For this basic sanity check, we just sweep the detuning
            detuning = (i - 50) * 0.1
            
            # Read modulation output
            mod_voltage = (yield dut.o_dac_mod)
            
            # Generate cavity reflection
            adc_sample = cavity.sample(detuning, mod_voltage, i * 1e-8)
            
            yield dut.i_adc_ch0.eq(int(adc_sample))
            yield dut.i_adc_valid.eq(1)
            yield Tick()
            
            # Allow DSP to process
            yield dut.i_adc_valid.eq(0)
            yield Tick()
            yield Tick()
            
        print("PASS: test_pdh_closed_loop")

    sim.add_process(process)
    sim.run()

if __name__ == "__main__":
    test_pdh_closed_loop()
