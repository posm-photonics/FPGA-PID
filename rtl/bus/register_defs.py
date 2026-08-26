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
# Common widths (defaults; override via module parameters as needed)
# ---------------------------------------------------------------------
ADC_W = 16   # ADC sample width
DAC_W = 16   # DAC code width
ERR_W = 20   # internal error/accumulator width
REG_W = 32   # bus register width
