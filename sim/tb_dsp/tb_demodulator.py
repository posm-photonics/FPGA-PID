import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from amaranth.sim import Simulator, Tick
from rtl.dsp.demodulator import Demodulator

def test_demodulator():
    dut = Demodulator(adc_w=17, ref_w=16, out_w=20, shift=13)
    
    sim = Simulator(dut)
    sim.add_clock(1e-8)
    
    def process():
        # Test DC offset with no AC signal
        yield dut.adc_in.eq(1000)
        yield dut.ref_sin.eq(32767)
        yield dut.ref_cos.eq(0)
        yield dut.adc_valid.eq(1)
        yield Tick()
        yield Tick()
        # LATENCY UPDATE: Demodulator is 2 cycles now (products registered
        # into the DSP48E1 MREG so the fast path meets 8 ns). One extra
        # tick per observation; the values asserted are unchanged.
        yield Tick()
        
        i_out = (yield dut.i_out)
        q_out = (yield dut.q_out)
        
        # 1000 * 32767 >> 13 = 3999
        assert i_out == 3999, f"Expected 3999, got {i_out}"
        assert q_out == 0, f"Expected 0, got {q_out}"
        
        # Test full negative scale
        yield dut.adc_in.eq(-1000)
        yield dut.ref_sin.eq(-32767)
        yield dut.ref_cos.eq(0)
        yield dut.adc_valid.eq(1)
        yield Tick()
        yield Tick()
        # LATENCY UPDATE: Demodulator is 2 cycles now (products registered
        # into the DSP48E1 MREG so the fast path meets 8 ns). One extra
        # tick per observation; the values asserted are unchanged.
        yield Tick()
        
        i_out = (yield dut.i_out)
        q_out = (yield dut.q_out)
        
        assert i_out == 3999, f"Expected 3999, got {i_out}"
        assert q_out == 0, f"Expected 0, got {q_out}"
        
        print("PASS: test_demodulator")

    sim.add_process(process)
    sim.run()

if __name__ == "__main__":
    test_demodulator()
