import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from amaranth.sim import Simulator, Tick
from rtl.dsp.demod_lowpass import DemodLowpass

def test_lpf():
    dut = DemodLowpass(in_w=20, out_w=20, acc_w=40, max_alpha=20)
    
    sim = Simulator(dut)
    sim.add_clock(1e-8)
    
    def process():
        # Set alpha shift to 4
        yield dut.alpha_shift.eq(4)
        yield dut.reset_filter.eq(1)
        yield dut.sample_valid.eq(0)
        yield Tick()
        yield dut.reset_filter.eq(0)
        
        # Test DC step response
        yield dut.sample_in.eq(10000)
        yield dut.sample_valid.eq(1)
        
        vals = []
        for _ in range(100):
            yield Tick()
            vals.append((yield dut.sample_out))
            
        # The filter should settle to the input value
        assert vals[-1] == 10000, f"Expected 10000, got {vals[-1]}"
        
        # Test reset
        yield dut.reset_filter.eq(1)
        yield Tick()
        yield dut.reset_filter.eq(0)
        
        out = (yield dut.sample_out)
        assert out == 0, f"Expected 0, got {out}"
        
        print("PASS: test_lpf")

    sim.add_process(process)
    sim.run()

if __name__ == "__main__":
    test_lpf()
