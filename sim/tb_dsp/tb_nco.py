import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from amaranth.sim import Simulator, Tick
from rtl.dsp.nco import NCO

def test_nco():
    dut = NCO(phase_w=32, lut_depth=256, out_w=16)
    
    # 2^32 / 256 = 16777216 for one full wave over 256 samples
    freq_word = 16777216
    
    sim = Simulator(dut)
    sim.add_clock(1e-8)
    
    def process():
        yield dut.freq_word.eq(freq_word)
        yield dut.phase_offset.eq(0)
        
        # Reset and wait a cycle
        yield Tick()
        yield Tick()
        
        # Record a full period
        sin_vals = []
        cos_vals = []
        
        for _ in range(300):
            yield Tick()
            sin_vals.append((yield dut.o_sin))
            cos_vals.append((yield dut.o_cos))
            
        # Check amplitude
        max_sin = max(sin_vals)
        min_sin = min(sin_vals)
        
        # With 256 entries and peak not at exactly an integer index, max is 32766
        assert max_sin == 32766, f"Expected max 32766, got {max_sin}"
        assert min_sin == -32766, f"Expected min -32766, got {min_sin}"
        
        # Check that cos is 90 degrees out of phase with sin
        # By finding the index of the max value
        sin_max_idx = sin_vals.index(max_sin)
        cos_max_idx = cos_vals.index(max(cos_vals))
        
        # 90 degrees in a 256-sample period is 64 samples
        phase_diff = (sin_max_idx - cos_max_idx) % 256
        assert phase_diff == 64, f"Expected phase diff 64, got {phase_diff}"
        
        print("PASS: test_nco")

    sim.add_process(process)
    sim.run()

if __name__ == "__main__":
    test_nco()
