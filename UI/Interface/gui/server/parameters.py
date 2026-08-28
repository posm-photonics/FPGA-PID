"""
gui/server/parameters.py

Typed parameter definitions for the POSM GUI.

This is the single place that knows how a human-readable name like
"p_gain" maps onto a register address, a bit position (if it's a flag
living inside a shared control/status word), a fixed-point scale
factor, and a valid range. Every other GUI-side module -- posm_server,
mock_backend, the real hardware backend, and every client panel --
talks in these names only. Nothing upstream of this file should ever
see a raw register address.

Addresses come from rtl/bus/register_defs.py (source of truth for the
FPGA side) plus rtl/control/slow_recenter.py and
rtl/control/trace_capture.py's own register blocks. If you change an
address in the RTL, change it here too -- see the docstring at the top
of protocol.py for the same warning.

PRE-SHIP AUDIT NOTE (2026-08-28)
The FPGA-side readback gap described below is FIXED: register_bank.py
now has a read decode for every R/W register, so these parameters read
back correctly. The ADDRESS DIVERGENCE is NOT fixed and still needs a
decision -- see rtl/bus/register_defs.py. The relocated block at
0x020-0x070 overlaps the range packet section 11.2 reserves for ADC
configuration (0x040-0x068), so the ADC block had to be parked at 0x0A0.
Either move this block back to canonical addresses (invalidating every
existing bitstream and this file at the same time) or amend the packet.

Newly added register groups, all readable and writable:
  error calc      ERROR_SETPOINT / ERROR_OFFSET   (packet 11.3)
  ADC config      ADC_CONFIG / ADC_GUARD_COUNT
  fast controller FAST_INT_LEAK                   (packet 11.4)
  lock watch      the five thresholds that used to be hardcoded
                  constants in lock_core_top      (packet 11.9)
  DAC config      DAC_CONFIG                      (packet 11.10)

ORIGINAL STATUS NOTE (kept for history; the readback half is resolved):
Three groups of parameters below (FAST_*, RAMP_*, AUTOLOCK_*) point at
register addresses (0x020-0x070) that did NOT exist in the FPGA
register map until this GUI project added them (see the "added for
the GUI project" comment in register_defs.py / register_bank.py).
Before this change, pi_controller's Kp/Ki, the ramp-scan bounds, and
the autolock feature-detection window were wired *into* the RTL
datapath from register_bank's Signals, but nothing ever decoded a bus
address onto those Signals -- they sat frozen at their Amaranth
`reset=` values forever, and no software (not even the existing
posm_reg_server.py sanity dashboard, which only touches the global
0x000-0x01C block) could move them. That gap is now closed at the
register_bank.py level, but the FPGA bitstream on any board you've
already built needs to be regenerated and reflashed before these
particular parameters will do anything on real hardware. Until then,
run against mock_backend.py (which uses the same updated RTL, in
Amaranth simulation) or expect writes to these addresses to be
silently ignored by an old bitstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from rtl.bus import register_defs as R


class Kind(Enum):
    REGISTER = "register"   # whole 32-bit register holds one value
    BIT = "bit"              # one bit inside a shared register
    FIELD = "field"          # multi-bit field inside a shared register
    PULSE = "pulse"          # write-any-value-to-trigger (e.g. TRACE_START)


@dataclass(frozen=True)
class Parameter:
    name: str                 # the only thing the GUI/wire protocol ever uses
    addr: int                 # byte address of the register that holds it
    kind: Kind
    group: str                # which panel/block this belongs to
    description: str
    writable: bool = True
    signed: bool = False
    width: int = 32           # bit width within the register
    bit: Optional[int] = None      # for Kind.BIT
    bit_lo: Optional[int] = None   # for Kind.FIELD
    scale: float = 1.0         # engineering_value = raw / scale
    unit: str = ""
    min_value: Optional[float] = None   # engineering units
    max_value: Optional[float] = None   # engineering units

    def raw_to_value(self, raw: int) -> float:
        if self.kind in (Kind.BIT,):
            v = (raw >> self.bit) & 1
            return bool(v)
        if self.kind == Kind.FIELD:
            mask = (1 << self.width) - 1
            v = (raw >> self.bit_lo) & mask
            if self.signed and (v & (1 << (self.width - 1))):
                v -= (1 << self.width)
            return v / self.scale if self.scale != 1.0 else v
        # Kind.REGISTER
        mask = (1 << self.width) - 1
        v = raw & mask
        if self.signed and (v & (1 << (self.width - 1))):
            v -= (1 << self.width)
        return v / self.scale if self.scale != 1.0 else v

    def value_to_raw(self, value, current_raw: int = 0) -> int:
        """Return the full 32-bit word to write to `addr`.

        For BIT/FIELD kinds this does a read-modify-write against
        `current_raw` (the last known value of the shared register)
        since a bit-level parameter can't be written in isolation --
        the bus is whole-register-at-a-time.
        """
        if self.kind == Kind.BIT:
            bitval = 1 if value else 0
            if bitval:
                return current_raw | (1 << self.bit)
            return current_raw & ~(1 << self.bit)
        if self.kind == Kind.FIELD:
            raw_field = int(round(value * self.scale)) if self.scale != 1.0 else int(value)
            mask = (1 << self.width) - 1
            raw_field &= mask
            cleared = current_raw & ~(mask << self.bit_lo)
            return cleared | (raw_field << self.bit_lo)
        if self.kind == Kind.PULSE:
            return 1
        # Kind.REGISTER
        raw = int(round(value * self.scale)) if self.scale != 1.0 else int(value)
        if self.min_value is not None:
            raw = max(raw, int(round(self.min_value * self.scale)))
        if self.max_value is not None:
            raw = min(raw, int(round(self.max_value * self.scale)))
        mask = (1 << self.width) - 1
        return raw & mask


def _reg(name, addr, group, desc, **kw):
    return Parameter(name=name, addr=addr, kind=Kind.REGISTER, group=group, description=desc, **kw)


def _bit(name, addr, bit, group, desc, writable=True):
    return Parameter(name=name, addr=addr, kind=Kind.BIT, bit=bit, group=group,
                      description=desc, writable=writable)


def _field(name, addr, bit_lo, width, group, desc, writable=True, **kw):
    return Parameter(name=name, addr=addr, kind=Kind.FIELD, bit_lo=bit_lo, width=width,
                      group=group, description=desc, writable=writable, **kw)


PARAMETERS: dict[str, Parameter] = {}


def _register_all(params):
    for p in params:
        if p.name in PARAMETERS:
            raise ValueError(f"duplicate parameter name: {p.name}")
        PARAMETERS[p.name] = p


# ---------------------------------------------------------------------
# Global control / status (0x000-0x01C) -- the only block the old
# sanity-check dashboard (scripts/Posm_Dashboard.html) could reach.
# ---------------------------------------------------------------------
_register_all([
    _reg("version", R.ADDR_VERSION, "system", "Register-map version word (read-only)",
         writable=False),

    _bit("global_enable", R.ADDR_CONTROL, R.CTRL_GLOBAL_ENABLE, "system",
         "Master enable for the whole lock core"),
    _bit("soft_reset", R.ADDR_CONTROL, R.CTRL_SOFT_RESET, "system",
         "Pulse: soft-resets the core (self-clears)"),
    _bit("outputs_enable", R.ADDR_CONTROL, R.CTRL_OUTPUTS_ENABLE, "system",
         "Enable DAC outputs to drive real actuators"),
    _bit("lock_enable_request", R.ADDR_CONTROL, R.CTRL_LOCK_ENABLE_REQUEST, "system",
         "Request the lock FSM begin acquiring lock"),
    _bit("hold_request", R.ADDR_CONTROL, R.CTRL_HOLD_REQUEST, "system",
         "Freeze the fast-loop integrator / hold current output"),
    _bit("fault_clear_request", R.ADDR_CONTROL, R.CTRL_FAULT_CLEAR_REQUEST, "system",
         "Pulse: ask the FSM to clear all sticky faults"),
    _bit("integrator_reset", R.ADDR_CONTROL, R.CTRL_INTEGRATOR_RESET, "pi",
         "Force the PI integrator to zero"),
    _bit("integrator_load", R.ADDR_CONTROL, R.CTRL_INTEGRATOR_LOAD, "pi",
         "Load the PI integrator from the autolock handoff value"),
    _bit("trace_capture_enable", R.ADDR_CONTROL, R.CTRL_TRACE_CAPTURE_ENABLE, "scope",
         "Master enable for trace capture"),
    _bit("autolock_enable", R.ADDR_CONTROL, R.CTRL_AUTOLOCK_ENABLE, "autolock",
         "Enable the autolock supervisor"),
    _bit("slow_recenter_enable", R.ADDR_CONTROL, R.CTRL_SLOW_RECENTER_ENABLE, "slow",
         "Enable slow-DAC recentering after lock"),
    _bit("adc_test_pattern_en", R.ADDR_CONTROL, R.CTRL_ADC_TEST_PATTERN_EN, "diagnostics",
         "Route a known ADC test pattern instead of live samples"),
    _bit("dac_test_pattern_en", R.ADDR_CONTROL, R.CTRL_DAC_TEST_PATTERN_EN, "diagnostics",
         "Route a known DAC test pattern instead of the control output"),

    _field("mode", R.ADDR_MODE, 0, 8, "system", "Passthrough mode word interpreted by lock_fsm"),

    _bit("status_locked", R.ADDR_STATUS, 4, "status", "Lock FSM reports LOCKED", writable=False),
    _bit("status_scanning", R.ADDR_STATUS, 5, "status", "Lock FSM is in a scan state", writable=False),
    _bit("status_saturation", R.ADDR_STATUS, 6, "status", "Output limiter is saturating", writable=False),
    _bit("status_trace_ready", R.ADDR_STATUS, 7, "status", "A trace capture is ready to read", writable=False),
    _bit("status_fault_active", R.ADDR_STATUS, 8, "status", "A fault is currently active", writable=False),
    _field("lock_state", R.ADDR_STATUS, 0, 4, "status", "lock_fsm.LockState value", writable=False),

    _bit("fault_adc_ch0_overrange", R.ADDR_FAULT_STATUS, 0, "faults", "ADC channel 0 overrange (sticky)", writable=False),
    _bit("fault_adc_ch1_overrange", R.ADDR_FAULT_STATUS, 1, "faults", "ADC channel 1 overrange (sticky)", writable=False),
    _bit("fault_adc_ch0_stuck", R.ADDR_FAULT_STATUS, 2, "faults", "ADC channel 0 appears stuck (sticky)", writable=False),
    _bit("fault_adc_ch1_stuck", R.ADDR_FAULT_STATUS, 3, "faults", "ADC channel 1 appears stuck (sticky)", writable=False),
    _bit("fault_adc_missing_valid", R.ADDR_FAULT_STATUS, 4, "faults", "ADC valid stream dropped out (sticky)", writable=False),
    _bit("fault_lock_watch", R.ADDR_FAULT_STATUS, 5, "faults", "lock_watch requested a fault (sticky)", writable=False),
    _bit("fault_relock_requested", R.ADDR_FAULT_STATUS, 6, "faults", "lock_watch requested a relock (sticky)", writable=False),
    _bit("fault_external_interlock", R.ADDR_FAULT_STATUS, 7, "faults", "External hardware interlock tripped (sticky)", writable=False),

    _field("fault_enable_mask", R.ADDR_FAULT_ENABLE, 0, 12, "faults", "Which of the 12 fault bits latch when tripped"),
    _reg("clear_all_faults", R.ADDR_FAULT_CLEAR, "faults", "Write 0xFFF to write-one-to-clear every sticky fault bit"),
    _field("debug_select", R.ADDR_DEBUG_SELECT, 0, 8, "diagnostics", "Debug mux select passthrough"),
])

# ---------------------------------------------------------------------
# Fast loop / PI controller (0x020-0x030) -- NEWLY ADDRESSABLE, see the
# module docstring above. Gains are Q3.14 fixed point
# (rtl/dsp/pi_controller.py): real_gain = register_value / 2**14.
# ---------------------------------------------------------------------
_register_all([
    _reg("p_gain", R.ADDR_FAST_KP, "pi", "Fast-loop proportional gain (Kp)",
         signed=True, width=18, scale=16384.0, unit="", min_value=-8.0, max_value=8.0),
    _reg("i_gain", R.ADDR_FAST_KI, "pi", "Fast-loop integral gain (Ki)",
         signed=True, width=18, scale=16384.0, unit="", min_value=-8.0, max_value=8.0),
    _reg("fast_out_min", R.ADDR_FAST_OUT_MIN, "pi", "Hard lower clamp on the fast DAC output",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("fast_out_max", R.ADDR_FAST_OUT_MAX, "pi", "Hard upper clamp on the fast DAC output",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("fast_out_safe", R.ADDR_FAST_OUT_SAFE, "pi", "Fast DAC output forced here while faulted",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
])

# ---------------------------------------------------------------------
# Ramp / scan (0x034-0x048) -- NEWLY ADDRESSABLE.
# ---------------------------------------------------------------------
_register_all([
    _reg("ramp_min", R.ADDR_RAMP_MIN, "scan", "Wide-scan lower bound",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("ramp_max", R.ADDR_RAMP_MAX, "scan", "Wide-scan upper bound",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("ramp_step", R.ADDR_RAMP_STEP, "scan", "Ramp step size per tick",
         width=16, min_value=1, max_value=65535, unit="DAC code/tick"),
    _reg("ramp_tick_div", R.ADDR_RAMP_TICK_DIV, "scan", "Clock-cycle divider between ramp steps (scan rate)",
         width=16, min_value=1, max_value=65535, unit="cycles"),
    _reg("ramp_center", R.ADDR_RAMP_CENTER, "scan", "Zoom-scan center position",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("ramp_width", R.ADDR_RAMP_WIDTH, "scan", "Zoom-scan half-width around center",
         width=16, min_value=1, max_value=65535, unit="DAC code"),
])

# ---------------------------------------------------------------------
# Autolock feature detection (0x04C-0x070) -- NEWLY ADDRESSABLE.
# ---------------------------------------------------------------------
_register_all([
    _reg("autolock_window_min", R.ADDR_AUTOLOCK_WINDOW_MIN, "autolock",
         "Scan-position window (low) to search for the lock feature", width=16,
         min_value=0, max_value=65535, unit="DAC code"),
    _reg("autolock_window_max", R.ADDR_AUTOLOCK_WINDOW_MAX, "autolock",
         "Scan-position window (high) to search for the lock feature", width=16,
         min_value=0, max_value=65535, unit="DAC code"),
    _reg("autolock_expected_min_x", R.ADDR_AUTOLOCK_EXPECTED_MIN_X, "autolock",
         "Expected feature position, low bound", width=16, min_value=0, max_value=65535),
    _reg("autolock_expected_max_x", R.ADDR_AUTOLOCK_EXPECTED_MAX_X, "autolock",
         "Expected feature position, high bound", width=16, min_value=0, max_value=65535),
    _reg("autolock_lock_x", R.ADDR_AUTOLOCK_LOCK_X, "autolock",
         "Scan position to hand off to the fast loop for locking", width=16,
         min_value=0, max_value=65535),
    _reg("autolock_amp_min", R.ADDR_AUTOLOCK_AMP_MIN, "autolock",
         "Minimum feature amplitude to accept as a real feature", signed=True, width=24,
         min_value=-(1 << 23), max_value=(1 << 23) - 1),
    _reg("autolock_width_min", R.ADDR_AUTOLOCK_WIDTH_MIN, "autolock",
         "Minimum feature width to accept", width=16, min_value=0, max_value=65535),
    _reg("autolock_width_max", R.ADDR_AUTOLOCK_WIDTH_MAX, "autolock",
         "Maximum feature width to accept", width=16, min_value=0, max_value=65535),
    _reg("autolock_slope_sign", R.ADDR_AUTOLOCK_SLOPE_SIGN, "autolock",
         "Expected sign of the feature's slope (0/1)", width=1, min_value=0, max_value=1),
    _reg("autolock_retry_limit", R.ADDR_AUTOLOCK_RETRY_LIMIT, "autolock",
         "Number of relock attempts before giving up to FAULT", width=8, min_value=0, max_value=255),
    _bit("error_invert", R.ADDR_ERROR_CONFIG, 0, "fast_loop",
         "Invert the signed error before the PI controller"),
    _reg("lock_error_max", R.ADDR_LOCK_ERROR_MAX, "fast_loop",
         "Maximum absolute error accepted by the hardware lock-quality check",
         width=24, min_value=0, max_value=(1 << 24) - 1),

    # ---------------------------------------------------------------
    # Error calculation (packet 11.3)
    #
    # AUDIT FIX: error_calc's offset and setpoint used to be hardwired
    # to zero in lock_core_top with no registers behind them. Packet 4.3
    # Eq. 13 defines the error as p*(x - ERROR_OFFSET - ERROR_SETPOINT),
    # and the PC is supposed to compute the y-offset from the selected
    # feature (packet 9.2 step 3). Without it the zero crossing is not
    # at zero error and the servo holds the wrong point.
    # ---------------------------------------------------------------
    _reg("error_setpoint", R.ADDR_ERROR_SETPOINT, "fast_loop",
         "Desired lock error, usually zero after offset correction",
         width=20, signed=True),
    _reg("error_offset", R.ADDR_ERROR_OFFSET, "fast_loop",
         "DC/background offset subtracted from the ADC sample",
         width=20, signed=True),

    # ---------------------------------------------------------------
    # ADC configuration
    # ---------------------------------------------------------------
    _bit("adc_format_mode", R.ADDR_ADC_CONFIG, R.ADC_CFG_FORMAT_MODE,
         "adc", "0 = offset binary, 1 = two's complement passthrough"),
    _bit("adc_fault_enable", R.ADDR_ADC_CONFIG, R.ADC_CFG_FAULT_ENABLE,
         "adc", "Allow ADC overrange / missing-valid to force a fault"),
    _reg("adc_guard_count", R.ADDR_ADC_GUARD_COUNT, "adc",
         "Consecutive unchanged samples before the stuck-ADC flag is raised",
         width=16, min_value=0, max_value=65535),

    # ---------------------------------------------------------------
    # Fast controller extras (packet 11.4)
    # ---------------------------------------------------------------
    _reg("fast_int_leak", R.ADDR_FAST_INT_LEAK, "pi",
         "Leaky-integrator shift; 0 disables the leak. The CTL200 AC "
         "modulation input cannot carry DC authority, so a pure "
         "accumulator winds up against an unresponsive actuator.",
         width=5, min_value=0, max_value=31),

    # ---------------------------------------------------------------
    # Lock check / lock watch (packet 11.9)
    #
    # AUDIT FIX: all five of these were HARDCODED CONSTANTS in
    # lock_core_top and appeared nowhere in the register map, so
    # retuning the safety watchdog required a resynthesis. The old
    # saturation timeout of 100 cycles (800 ns at 125 MHz) tripped on
    # ordinary lock-acquisition transients.
    # ---------------------------------------------------------------
    _reg("lock_check_delay", R.ADDR_LOCK_CHECK_DELAY, "lock_watch",
         "Samples the lock-quality condition must hold before lock is declared",
         width=32, min_value=0),
    _reg("lock_max_sat_count", R.ADDR_LOCK_MAX_SAT_COUNT, "lock_watch",
         "Samples of continuous output saturation before a fault is raised",
         width=32, min_value=0),
    _reg("lock_adc_timeout", R.ADDR_LOCK_ADC_TIMEOUT, "lock_watch",
         "Samples of missing ADC valid before an ADC fault is raised",
         width=32, min_value=0),
    _reg("lock_error_timeout", R.ADDR_LOCK_ERROR_TIMEOUT, "lock_watch",
         "Samples of excess error before a relock is requested",
         width=32, min_value=0),
    _reg("lock_jump_limit", R.ADDR_LOCK_JUMP_LIMIT, "lock_watch",
         "Maximum fast-output change per jump window before a relock is requested",
         width=16, min_value=0, max_value=65535),
    _reg("lock_jump_window", R.ADDR_LOCK_JUMP_WINDOW, "lock_watch",
         "log2 of the jump comparison window in samples",
         width=5, min_value=0, max_value=31),
    _reg("lock_state_timeout", R.ADDR_LOCK_STATE_TIMEOUT, "lock_watch",
         "Cycles an acquisition state may wait before escalating to fault; 0 disables",
         width=32, min_value=0),
    _reg("lock_relock_limit", R.ADDR_LOCK_RELOCK_LIMIT, "lock_watch",
         "Relock attempts before escalating to fault; 0 disables the limit",
         width=8, min_value=0, max_value=255),

    # ---------------------------------------------------------------
    # DAC configuration (packet 11.10)
    # ---------------------------------------------------------------
    _bit("dac_fast_offset_bin", R.ADDR_DAC_CONFIG, R.DAC_CFG_FAST_OFFSET_BIN,
         "dac", "Fast DAC encoding: 0 = two's complement, 1 = offset binary"),
    _bit("dac_slow_offset_bin", R.ADDR_DAC_CONFIG, R.DAC_CFG_SLOW_OFFSET_BIN,
         "dac", "Slow DAC encoding: 0 = two's complement, 1 = offset binary"),
])

# ---------------------------------------------------------------------
# Slow controller / recentering (0x100-0x124) -- already addressable
# on real hardware today (rtl/control/slow_recenter.py).
# ---------------------------------------------------------------------
_register_all([
    _bit("slow_recenter_config_enable", R.ADDR_SLOW_CTRL_CONFIG, R.SLOW_CFG_RECENTER_ENABLE,
         "slow", "Enable the slow-recentering accumulator"),
    _bit("slow_recenter_config_hold", R.ADDR_SLOW_CTRL_CONFIG, R.SLOW_CFG_HOLD,
         "slow", "Freeze the slow accumulator, keep last output"),
    _bit("slow_recenter_config_accum_reset", R.ADDR_SLOW_CTRL_CONFIG, R.SLOW_CFG_ACCUM_RESET,
         "slow", "Force the slow accumulator to zero"),
    _bit("slow_recenter_config_accum_load", R.ADDR_SLOW_CTRL_CONFIG, R.SLOW_CFG_ACCUM_LOAD,
         "slow", "Load the slow accumulator from a write to slow_out_current"),
    _field("slow_recenter_tick_div_shift", R.ADDR_SLOW_CTRL_CONFIG, R.SLOW_CFG_TICK_DIV_SHIFT,
           R.SLOW_CFG_TICK_DIV_WIDTH, "slow", "log2(slow tick divider) -- how slow the recenter loop runs"),
    _reg("slow_bias", R.ADDR_SLOW_BIAS, "slow", "DC bias / center code added to the slow accumulator",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("slow_recenter_target", R.ADDR_SLOW_RECENTER_TARGET, "slow",
         "u_fast_center: fast-DAC center the recenter loop tries to hold", signed=True, width=16,
         min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("slow_recenter_gain", R.ADDR_SLOW_RECENTER_GAIN, "slow", "Ks, slow recenter gain",
         signed=True, width=16, scale=4096.0, min_value=-8.0, max_value=8.0),
    _reg("slow_out_min", R.ADDR_SLOW_OUT_MIN, "slow", "Hard lower clamp on the slow DAC output",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("slow_out_max", R.ADDR_SLOW_OUT_MAX, "slow", "Hard upper clamp on the slow DAC output",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("slow_out_safe", R.ADDR_SLOW_OUT_SAFE, "slow", "Slow DAC output forced here while faulted",
         signed=True, width=16, min_value=-32768, max_value=32767, unit="DAC code"),
    _reg("slow_slew_limit", R.ADDR_SLOW_SLEW_LIMIT, "slow", "Max |delta| per slow tick",
         width=16, min_value=0, max_value=65535, unit="DAC code/tick"),
    _reg("slow_out_current", R.ADDR_SLOW_OUT_CURRENT, "slow", "Current slow DAC command (post-clamp)",
         signed=True, width=16, writable=False, unit="DAC code"),
])

# ---------------------------------------------------------------------
# Trace capture (0x180-0x1A0) -- already addressable on real hardware.
# ---------------------------------------------------------------------
_register_all([
    _bit("trace_config_enable", R.ADDR_TRACE_CONFIG, R.TRACE_CFG_ENABLE, "scope",
         "Trace-capture block enable"),
    _bit("trace_config_channel_sel", R.ADDR_TRACE_CONFIG, R.TRACE_CFG_CHANNEL_SEL, "scope",
         "0 = capture error signal (CH0), 1 = capture raw RF monitor (CH1)"),
    _reg("trace_start", R.ADDR_TRACE_START, "scope", "Write any value to arm+start a capture",
         width=32),
    _reg("trace_length", R.ADDR_TRACE_LENGTH, "scope", "Number of (X, Y) pairs to capture",
         width=13, min_value=1, max_value=4096),
    _reg("trace_decim", R.ADDR_TRACE_DECIM, "scope", "Capture every Nth valid sample",
         width=16, min_value=1, max_value=65535),
    _bit("trace_status_busy", R.ADDR_TRACE_STATUS, R.TRACE_STAT_BUSY, "scope",
         "A capture is currently in progress", writable=False),
    _bit("trace_status_ready", R.ADDR_TRACE_STATUS, R.TRACE_STAT_READY, "scope",
         "A completed capture is ready to read", writable=False),
    _bit("trace_status_overflow", R.ADDR_TRACE_STATUS, R.TRACE_STAT_OVERFLOW, "scope",
         "A new capture started before the previous one finished", writable=False),
    _reg("trace_write_ptr", R.ADDR_TRACE_WRITE_PTR, "scope", "Current internal write pointer",
         width=13, writable=False),
    _reg("trace_read_addr", R.ADDR_TRACE_READ_ADDR, "scope", "Host readback address (0..depth-1)",
         width=13, min_value=0, max_value=4095),
    _reg("trace_read_data_x", R.ADDR_TRACE_READ_DATA_X, "scope", "Scan code at trace_read_addr",
         signed=True, width=16, writable=False),
    _reg("trace_read_data_y", R.ADDR_TRACE_READ_DATA_Y, "scope", "Error/CH1 sample at trace_read_addr",
         signed=True, width=20, writable=False),
])


def get(name: str) -> Parameter:
    try:
        return PARAMETERS[name]
    except KeyError:
        raise KeyError(f"unknown parameter '{name}'") from None


def all_names() -> list[str]:
    return list(PARAMETERS.keys())


def by_group(group: str) -> list[Parameter]:
    return [p for p in PARAMETERS.values() if p.group == group]
