from amaranth import *
from amaranth.sim import Simulator
from rtl.adc.adc_frontend_top import ADCFrontendTop

def test_adc_frontend_top():
    m = ADCFrontendTop(width=16)

    sim = Simulator(m)
    sim.add_clock(1e-6)

    def process():
        # NORMAL STREAMING
        for i in range(20):
            yield m.i_format_mode.eq(1)
            yield m.i_ch0.eq(i)
            yield m.i_ch1.eq(i + 10)
            yield m.i_valid.eq(1)
            yield m.i_overrange_ch0.eq(0)
            yield m.i_overrange_ch1.eq(0)

            yield
            yield

            assert (yield m.o_valid) == 1

        # FAULT INJECTION
        yield m.i_overrange_ch0.eq(1)
        yield
        yield

        faults = yield m.o_fault_flags
        assert faults != 0, "frontend failed to propagate guard fault"

    sim.add_sync_process(process)
    with sim.write_vcd("adc_frontend_top.vcd"):
        sim.run()
