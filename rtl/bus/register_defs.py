"""
register_defs.py

Shared register address and bit-field constants for the POSM FPGA MTS
lock core, matching the "Canonical Register Map" in section 11 of the
onboarding packet.

This is intentionally just Python constants (mirrors the role of
register_defs_pkg.sv from the repo layout in section 13). Keep this
the single source of truth for addresses so docs, register_bank,
trace_capture, and slow_recenter never drift apart.

All addresses are BYTE offsets, matching the packet. All registers
are 32 bits wide.
"""

# ---------------------------------------------------------------------
# 11.1 Global control / status
# ---------------------------------------------------------------------
ADDR_VERSION        = 0x000  # R
ADDR_CONTROL        = 0x004  # R/W
ADDR_STATUS         = 0x008  # R
ADDR_MODE           = 0x00C  # R/W
ADDR_FAULT_STATUS   = 0x010  # R
ADDR_FAULT_ENABLE   = 0x014  # R/W
ADDR_FAULT_CLEAR    = 0x018  # W (write-one-to-clear)
ADDR_DEBUG_SELECT   = 0x01C  # R/W

# CONTROL bit positions (Table in 11.1)
CTRL_GLOBAL_ENABLE          = 0
CTRL_SOFT_RESET             = 1
CTRL_OUTPUTS_ENABLE         = 2
CTRL_LOCK_ENABLE_REQUEST    = 3
CTRL_HOLD_REQUEST           = 4
CTRL_FAULT_CLEAR_REQUEST    = 5
CTRL_INTEGRATOR_RESET       = 6
CTRL_INTEGRATOR_LOAD        = 7
CTRL_TRACE_CAPTURE_ENABLE   = 8
CTRL_AUTOLOCK_ENABLE        = 9
CTRL_SLOW_RECENTER_ENABLE   = 10
CTRL_ADC_TEST_PATTERN_EN    = 11
CTRL_DAC_TEST_PATTERN_EN    = 12

# ---------------------------------------------------------------------
# 11.5 Slow controller / recentering
# ---------------------------------------------------------------------
ADDR_SLOW_CTRL_CONFIG       = 0x100  # R/W
ADDR_SLOW_BIAS              = 0x104  # R/W
ADDR_SLOW_KI                = 0x108  # R/W (kept for future use by slow scan integrator)
ADDR_SLOW_RECENTER_TARGET   = 0x10C  # R/W
ADDR_SLOW_RECENTER_GAIN     = 0x110  # R/W
ADDR_SLOW_OUT_MIN           = 0x114  # R/W
ADDR_SLOW_OUT_MAX           = 0x118  # R/W
ADDR_SLOW_OUT_SAFE          = 0x11C  # R/W
ADDR_SLOW_SLEW_LIMIT        = 0x120  # R/W
ADDR_SLOW_OUT_CURRENT       = 0x124  # R

# SLOW_CTRL_CONFIG bit positions (not explicitly enumerated in the
# packet beyond "enables scan, hold, slow integrator, recentering" --
# defined here for a concrete implementation)
SLOW_CFG_RECENTER_ENABLE    = 0
SLOW_CFG_HOLD               = 1
SLOW_CFG_ACCUM_RESET        = 2
SLOW_CFG_ACCUM_LOAD         = 3
SLOW_CFG_TICK_DIV_SHIFT     = 8   # bits [15:8] = log2(tick divider)
SLOW_CFG_TICK_DIV_WIDTH     = 8

# ---------------------------------------------------------------------
# 11.7 Trace capture
# ---------------------------------------------------------------------
ADDR_TRACE_CONFIG       = 0x180  # R/W
ADDR_TRACE_START        = 0x184  # W
ADDR_TRACE_LENGTH       = 0x188  # R/W
ADDR_TRACE_DECIM        = 0x18C  # R/W
ADDR_TRACE_STATUS       = 0x190  # R
ADDR_TRACE_WRITE_PTR    = 0x194  # R
ADDR_TRACE_READ_ADDR    = 0x198  # R/W
ADDR_TRACE_READ_DATA_X  = 0x19C  # R
ADDR_TRACE_READ_DATA_Y  = 0x1A0  # R

# TRACE_CONFIG bit positions
TRACE_CFG_ENABLE        = 0
TRACE_CFG_CHANNEL_SEL   = 1   # 0 = ADC_CH0 (mts error), 1 = ADC_CH1 (raw rf monitor diag)

# TRACE_STATUS bit positions
TRACE_STAT_BUSY         = 0
TRACE_STAT_READY        = 1
TRACE_STAT_OVERFLOW     = 2

# ---------------------------------------------------------------------
# Fast loop / PI controller
# ---------------------------------------------------------------------
ADDR_FAST_KP           = 0x020  # R/W  Q3.14 signed gain
ADDR_FAST_KI           = 0x024  # R/W  Q3.14 signed gain
ADDR_FAST_OUT_MIN      = 0x028  # R/W  signed DAC lower clamp
ADDR_FAST_OUT_MAX      = 0x02C  # R/W  signed DAC upper clamp
ADDR_FAST_OUT_SAFE     = 0x030  # R/W  safe DAC output while faulted

# ---------------------------------------------------------------------
# Ramp / scan
# ---------------------------------------------------------------------
ADDR_RAMP_MIN          = 0x034  # R/W  wide-scan lower bound
ADDR_RAMP_MAX          = 0x038  # R/W  wide-scan upper bound
ADDR_RAMP_STEP         = 0x03C  # R/W  ramp step size
ADDR_RAMP_TICK_DIV     = 0x040  # R/W  scan tick division
ADDR_RAMP_CENTER       = 0x044  # R/W  zoom center
ADDR_RAMP_WIDTH        = 0x048  # R/W  zoom half-width

# ---------------------------------------------------------------------
# Autolock descriptor
# ---------------------------------------------------------------------
ADDR_AUTOLOCK_WINDOW_MIN      = 0x04C  # R/W
ADDR_AUTOLOCK_WINDOW_MAX      = 0x050  # R/W
ADDR_AUTOLOCK_EXPECTED_MIN_X  = 0x054  # R/W
ADDR_AUTOLOCK_EXPECTED_MAX_X  = 0x058  # R/W
ADDR_AUTOLOCK_LOCK_X          = 0x05C  # R/W
ADDR_AUTOLOCK_AMP_MIN         = 0x060  # R/W signed
ADDR_AUTOLOCK_WIDTH_MIN       = 0x064  # R/W
ADDR_AUTOLOCK_WIDTH_MAX       = 0x068  # R/W
ADDR_AUTOLOCK_SLOPE_SIGN      = 0x06C  # R/W 0/1
ADDR_AUTOLOCK_RETRY_LIMIT     = 0x070  # R/W
ADDR_ERROR_CONFIG             = 0x074  # R/W
ADDR_LOCK_ERROR_MAX           = 0x078  # R/W unsigned magnitude limit

# ERROR_CONFIG bit positions
ERROR_CFG_INVERT              = 0

# ---------------------------------------------------------------------
# 11.9 PDH Subsystem
# ---------------------------------------------------------------------
ADDR_PDH_CONTROL        = 0x200  # R/W
ADDR_PDH_MOD_FREQ       = 0x204  # R/W
ADDR_PDH_MOD_AMP        = 0x208  # R/W
ADDR_PDH_DEMOD_PHASE    = 0x20C  # R/W
ADDR_PDH_LPF_ALPHA      = 0x210  # R/W

# PDH_CONTROL bit positions
PDH_CTRL_ENABLE         = 0

# ---------------------------------------------------------------------
# 11.3 Error calculation  (canonical packet addresses)
# ---------------------------------------------------------------------
# AUDIT FIX: error_calc's offset and setpoint inputs were HARDWIRED TO
# ZERO in lock_core_top. Packet 4.3 Eq. 13 defines the error as
#     e[n] = p * (x[n] - ERROR_OFFSET - ERROR_SETPOINT)
# and section 4.3 is explicit that e_offset is "a DC/background offset".
# The MTS demodulated signal has a real electronic offset; without
# subtracting it the zero crossing is not at zero error, so the servo
# holds the wrong point. Packet 9.2 step 3 has the PC compute exactly
# this y-offset from the selected feature.
ADDR_ERROR_SETPOINT     = 0x080  # R/W signed
ADDR_ERROR_OFFSET       = 0x084  # R/W signed

# ---------------------------------------------------------------------
# ADC configuration
# ---------------------------------------------------------------------
# NOTE ON ADDRESSES: packet 11.2 places the ADC block at 0x040-0x068.
# That range is already occupied in this repo by RAMP_TICK_DIV (0x040)
# through AUTOLOCK_WIDTH_MAX (0x068), because the GUI project relocated
# the FAST_*/RAMP_*/AUTOLOCK_* registers into 0x020-0x070 instead of
# using the canonical map (see the status note at the top of
# UI/Interface/gui/server/parameters.py). The ADC block is parked at
# 0x0A0 here rather than colliding with it.
#
# This is a KNOWN DIVERGENCE from the canonical map and still needs a
# decision: either move the relocated block back to canonical addresses
# (which invalidates every existing bitstream and the GUI at once), or
# amend packet section 11. Do not add further registers in 0x040-0x068.
ADDR_ADC_CONFIG         = 0x0A0  # R/W bit0 = format mode
ADDR_ADC_GUARD_COUNT    = 0x0A4  # R/W stuck-sample count before fault candidate

ADC_CFG_FORMAT_MODE     = 0      # 0 = offset binary, 1 = two's complement
# AUDIT FIX (S3-5): the ADC guard flags reached the sticky FAULT_STATUS
# word but never reached fault_source, so ADC overrange, a stuck ADC and
# missing valid did not force a safe output, even though packet 10.1
# lists all three as fault sources. This bit gates that path, so an
# operator can disable a noisy source without a rebuild. Default enabled.
ADC_CFG_FAULT_ENABLE    = 1

# ---------------------------------------------------------------------
# 11.4 Fast controller (extras, canonical addresses)
# ---------------------------------------------------------------------
# Packet 11.4 FAST_INT_LEAK. The CTL200 AC modulation input cannot carry
# true DC authority (packet 3.4, and the "Important" box in 8.5), so a
# pure accumulator winds up against an actuator that physically cannot
# respond. 0 = no leak.
ADDR_FAST_INT_LEAK      = 0x0CC  # R/W leaky-integrator shift

# ---------------------------------------------------------------------
# 11.9 Lock check / lock watch  (canonical packet addresses)
# ---------------------------------------------------------------------
# AUDIT FIX (S2-5): all five lock-watch thresholds were HARDCODED
# CONSTANTS in lock_core_top and appeared nowhere in the register map, so
# retuning the safety watchdog required a resynthesis. The hardcoded
# saturation_timeout of 100 cycles (800 ns at 125 MHz) was short enough
# that ordinary lock-acquisition transients tripped it into a fault.
ADDR_LOCK_CHECK_DELAY   = 0x224  # R/W samples the lock check must hold before passing
ADDR_LOCK_MAX_ERROR     = 0x22C  # R/W max |error| while locked
ADDR_LOCK_MAX_SAT_COUNT = 0x234  # R/W saturation duration before fault

# Extensions beyond the canonical block (0x240-0x254 is unused in both
# the packet and this repo).
ADDR_LOCK_ADC_TIMEOUT   = 0x240  # R/W missing-valid samples before ADC fault
ADDR_LOCK_ERROR_TIMEOUT = 0x244  # R/W samples of excess error before relock
ADDR_LOCK_JUMP_LIMIT    = 0x248  # R/W max |delta| in fast output per jump window
ADDR_LOCK_JUMP_WINDOW   = 0x24C  # R/W log2 of the jump comparison window
ADDR_LOCK_STATE_TIMEOUT = 0x250  # R/W cycles before an acquisition state gives up
ADDR_LOCK_RELOCK_LIMIT  = 0x254  # R/W relock attempts before escalating to fault

# ---------------------------------------------------------------------
# 11.10 DAC configuration and safety  (canonical packet address)
# ---------------------------------------------------------------------
# AUDIT FIX (S3-4): dac_fast_fmt.i_mode was hardcoded to 0 in
# lock_core_top, with a comment admitting "DAC fast formatter mode
# selection" was unimplemented. Packet 11.10 defines it as a register.
ADDR_DAC_CONFIG         = 0x260  # R/W
DAC_CFG_FAST_OFFSET_BIN = 0      # 0 = two's complement, 1 = offset binary
DAC_CFG_SLOW_OFFSET_BIN = 1

# ---------------------------------------------------------------------
# Common widths (defaults; override via module parameters as needed)
# ---------------------------------------------------------------------
ADC_W = 16   # ADC sample width
DAC_W = 16   # DAC code width
ERR_W = 20   # internal error/accumulator width
REG_W = 32   # bus register width
