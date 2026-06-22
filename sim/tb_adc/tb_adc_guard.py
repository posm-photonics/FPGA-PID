from amaranth import *
from amaranth.sim import Simulator
from rtl.adc.adc_guard import ADCGuard

def test_adc_guard():
    m = ADCGuard(width=17, guard_count_threshold=3)

    sim = Simulator(m)
    sim.add_clock(1e-6)

    def process():
        # NORMAL OPERATION
        for i in range(5):
            yield m.i_valid.eq(1)
            yield m.i_ch0.eq(i)
            yield m.i_ch1.eq(i + 1)
            yield m.i_overrange_ch0.eq(0)
            yield m.i_overrange_ch1.eq(0)
            yield
            yield

            faults = yield m.o_fault_flags
            assert faults == 0

    
        # STUCK-AT DETECTION
        for i in range(10):
            yield m.i_valid.eq(1)
            yield m.i_ch0.eq(100)  # stuck
            yield m.i_ch1.eq(200)
            yield
            yield

        faults = yield m.o_fault_flags
        assert (faults & (1 << 2)) != 0, "stuck CH0 not detected"

        # OVER-RANGE
        yield m.i_overrange_ch1.eq(1)
        yield
        yield

        faults = yield m.o_fault_flags
        assert (faults & (1 << 1)) != 0

        
        # MISSING VALID
        yield m.i_valid.eq(0)
        yield
        yield

        faults = yield m.o_fault_flags
        assert (faults & (1 << 4)) != 0

    sim.add_sync_process(process)
    with sim.write_vcd("adc_guard.vcd"):
        sim.run()
