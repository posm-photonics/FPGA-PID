"""
gui/server/mock_backend.py

A fake register backend for posm_server.py that responds to exactly
the same register reads/writes a real board would (rtl/bus/register_bank.py,
rtl/control/slow_recenter.py, rtl/control/trace_capture.py), but is
backed by the *actual* LockCoreTop RTL (top/lock_core_top.py) running
under Amaranth's simulator, closed-loop against sim/models/fake_laser_plant.py
and sim/models/fake_mts_signal.py -- the same plant/signal models
sim/run_closed_loop_demo.py uses for its offline batch runs.

This means the GUI is not testing a hand-rolled approximation of the
hardware -- every panel drives the same Amaranth Signals, the same
PICore/RampScan/RobustAutoLock/LockFSM/FaultGate logic that would run
on the FPGA. Swap this for gui/server/hw_backend.py (real /dev/mem
register access) with a single config flag in posm_server.py once
you're ready to point the GUI at actual hardware -- see docs/10_gui_implementation_plan.md
section 2.

The simulator runs in its own background thread so posm_server.py's
WebSocket handling is never blocked by it. Register reads/writes from
GUI clients are queued as bus transactions and executed inside the
simulation's own clock domain, exactly like a real memory-mapped bus
transaction would be, just executed by Amaranth instead of silicon.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from amaranth.sim import Simulator  # noqa: E402

from top.lock_core_top import LockCoreTop  # noqa: E402
from sim.models.fake_laser_plant import FakeLaserPlant  # noqa: E402
from sim.models.fake_mts_signal import FakeMTSSignal  # noqa: E402
from sim.models.fault_injector import FaultInjector  # noqa: E402
from sim.models.simulation_config import SimulationConfig  # noqa: E402

CLOCK_PERIOD_S = 1e-8          # 100 MHz virtual fast-domain clock
SETTLE_TICKS_PER_STEP = 6      # cycles let through per plant step, plenty
                                # for the fast path's few-cycle latency chain
                                # (PICore latency alone is 2 cycles).
PLANT_STEP_PERIOD_S = 0.01     # wall-clock pacing: 100 plant "samples"/sec,
                                # fast enough to feel live, slow enough that
                                # a browser tab doesn't get flooded.


class _BusOp:
    __slots__ = ("addr", "value", "is_write", "result", "event")

    def __init__(self, addr: int, value: Optional[int]):
        self.addr = addr
        self.value = value
        self.is_write = value is not None
        self.result = None
        self.event = threading.Event()


class MockBackend:
    """Register backend driven by a live Amaranth simulation of LockCoreTop."""

    def __init__(self, sim_config: Optional[SimulationConfig] = None):
        self.sim_config = sim_config or SimulationConfig()
        self.plant = FakeLaserPlant(self.sim_config.laser)
        self.signal = FakeMTSSignal(self.sim_config.spectroscopy)
        self.injector = FaultInjector(self.sim_config.faults)

        self.dut = LockCoreTop()
        self._sim = Simulator(self.dut)

        self._ops: "queue.Queue[_BusOp]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Latest known values, for callers that just want a fast
        # non-blocking peek (scope_streamer / status polling use the
        # blocking read() below instead, this is here for convenience).
        self.last_lock_state = 0
        self.last_fast_dac = 0
        self.last_slow_dac = 0
        self.sim_time_s = 0.0

        # Event log hook: posm_server.py can set this to a callable
        # (kind: str, detail: str) -> None to receive fault/relock
        # events as they happen inside the simulation, same shape as
        # what hw_backend.py would need to synthesize by polling
        # FAULT_STATUS on real hardware.
        self.on_event = None
        self._last_fault_active = False
        self._last_locked = False

    # -------------------------------------------------------------
    # Public backend interface (mirrors hw_backend.HardwareBackend)
    # -------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="mock-backend-sim", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def read(self, addr: int) -> int:
        op = _BusOp(addr, None)
        self._ops.put(op)
        op.event.wait(timeout=2.0)
        return op.result if op.result is not None else 0

    def write(self, addr: int, value: int) -> None:
        op = _BusOp(addr, value & 0xFFFFFFFF)
        self._ops.put(op)
        op.event.wait(timeout=2.0)

    # -------------------------------------------------------------
    # Simulation thread
    # -------------------------------------------------------------
    def _run(self):
        self._sim.add_testbench(self._testbench)
        self._sim.run()

    async def _testbench(self, ctx):
        dut = self.dut

        # Power-on reset.
        ctx.set(dut.rst, 1)
        for _ in range(8):
            ctx.set(dut.clk, 0)
            await ctx.delay(CLOCK_PERIOD_S / 2)
            ctx.set(dut.clk, 1)
            await ctx.delay(CLOCK_PERIOD_S / 2)
        ctx.set(dut.rst, 0)

        fast_dac = 0.0
        slow_dac = 0.0
        next_step_wall = time.monotonic()

        while not self._stop.is_set():
            # 1) drain any pending register operations from GUI clients.
            drained = 0
            while drained < 64:
                try:
                    op = self._ops.get_nowait()
                except queue.Empty:
                    break
                await self._do_bus_op(ctx, op)
                drained += 1

            # 2) advance the plant/signal model by one sample and feed
            #    the DUT, mirroring sim/run_closed_loop_demo.py.
            detuning = self.plant.step(fast_dac, slow_dac)
            measured_error = self.signal.sample(detuning)
            _, adc_sample, _, _ = self.injector.apply(
                detuning, measured_error, fast_dac, slow_dac, self.sim_time_s)

            ctx.set(dut.i_adc_ch0, int(round(adc_sample)) & 0xFFFF)
            ctx.set(dut.i_adc_ch1, int(round(adc_sample * 0.5)) & 0xFFFF)
            ctx.set(dut.i_adc_valid, 1)
            ctx.set(dut.i_adc_overrange_ch0, 0)
            ctx.set(dut.i_adc_overrange_ch1, 0)
            ctx.set(dut.i_external_interlock, 0)
            ctx.set(dut.i_feature_selected, 1)

            for _ in range(SETTLE_TICKS_PER_STEP):
                await self._tick(ctx)

            fast_dac = float(_to_signed(ctx.get(dut.o_dac_fast), 16))
            slow_dac = float(_to_signed(ctx.get(dut.o_dac_slow), 16))
            self.last_fast_dac = fast_dac
            self.last_slow_dac = slow_dac
            self.last_lock_state = ctx.get(dut.lock_state)
            self.sim_time_s += PLANT_STEP_PERIOD_S

            self._maybe_emit_events(ctx)

            # 3) pace to wall-clock so the GUI sees a "live" instrument
            #    rather than a simulation running flat-out.
            next_step_wall += PLANT_STEP_PERIOD_S
            sleep_for = next_step_wall - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_step_wall = time.monotonic()

    async def _tick(self, ctx):
        ctx.set(self.dut.clk, 0)
        await ctx.delay(CLOCK_PERIOD_S / 2)
        ctx.set(self.dut.clk, 1)
        await ctx.delay(CLOCK_PERIOD_S / 2)

    async def _do_bus_op(self, ctx, op: _BusOp):
        dut = self.dut
        ctx.set(dut.adr, op.addr & 0xFFF)
        ctx.set(dut.dat_w, op.value if op.is_write else 0)
        ctx.set(dut.we, 1 if op.is_write else 0)
        ctx.set(dut.stb, 1)
        # Let the combinational read mux settle, and (for writes) let
        # the register actually latch, before sampling/deasserting.
        await self._tick(ctx)
        op.result = ctx.get(dut.dat_r) & 0xFFFFFFFF
        ctx.set(dut.we, 0)
        ctx.set(dut.stb, 0)
        await self._tick(ctx)
        op.event.set()

    def _maybe_emit_events(self, ctx):
        if self.on_event is None:
            return
        fault_active = bool(ctx.get(self.dut.lock_fault))
        locked = self.last_lock_state == 7  # LockState.LOCKED
        if fault_active and not self._last_fault_active:
            self.on_event("fault_tripped", f"lock_fault asserted at t={self.sim_time_s:.2f}s")
        if locked and not self._last_locked:
            self.on_event("locked", f"lock acquired at t={self.sim_time_s:.2f}s")
        if self._last_locked and not locked and not fault_active:
            self.on_event("unlocked", f"lock dropped at t={self.sim_time_s:.2f}s")
        self._last_fault_active = fault_active
        self._last_locked = locked


def _to_signed(value: int, width: int) -> int:
    value &= (1 << width) - 1
    if value & (1 << (width - 1)):
        value -= (1 << width)
    return value
