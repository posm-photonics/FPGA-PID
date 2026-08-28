# ============================================================================
# scripts/build_posm_red_pitaya.tcl
# ============================================================================
#
# Non-project-mode Vivado build script for the POSM lock core on Red Pitaya
# STEMlab 125-14 (XC7Z010).
#
# WHAT THIS DOES: it builds red_pitaya_top.v (Red Pitaya's own, proven top
# level -- PS7, PLL, ADC/DAC IOB/ODDR, everything except the actual DSP)
# together with this project's generated red_pitaya_lock_core.v, and
# produces a bitstream. It does NOT build a Vivado IP-integrator block
# design from scratch, and it does NOT reimplement the PS7/clocking/IO --
# that is Red Pitaya's own already-working RTL, reused unmodified. See
# top/RedPitaya_Lock_Core.py's module docstring for why.
#
# WHAT THIS SCRIPT ASSUMES YOU HAVE ALREADY DONE (one-time setup):
#
#   1. Cloned the official FPGA sources next to this repo:
#        git clone https://github.com/RedPitaya/RedPitaya-FPGA.git
#        cd RedPitaya-FPGA && git checkout <PIN A SPECIFIC TAG HERE>
#      Pin a specific commit/tag rather than floating on `master`. This
#      is not just hygiene: see WARNING 3 below -- the safety of the
#      whole register path rests on which clock domain that revision
#      puts the sys_* bus in.
#
#   2. Generated this project's Verilog:
#        python3 build/generate_verilog.py
#      which writes build/out/red_pitaya_lock_core.v
#
#   3. Manually added ONE instantiation of red_pitaya_lock_core into
#      RedPitaya-FPGA's red_pitaya_top.v, wired into a free bus slot
#      (slot 6 is free in the classic v0.94-era layout -- confirm against
#      whatever RedPitaya-FPGA commit/tag you're actually building from,
#      since slot usage can change between releases). This is a manual
#      RTL edit, not something this TCL script does for you -- editing
#      someone else's always-changing top-level file via scripted text
#      surgery is exactly the kind of "fix works until it silently
#      doesn't" trap worth avoiding. Wire it roughly like:
#
#        red_pitaya_lock_core i_posm (
#          .clk         ( adc_clk  ),
#          .rst         ( ~adc_rstn ),
#          .i_adc_dat_a ( adc_dat_a ),   // raw 14-bit, pre-two's-complement
#          .i_adc_dat_b ( adc_dat_b ),
#          .o_dac_dat_a ( /* sum into dac_a path, see red_pitaya_top.v's
#                            existing dac_a_sum wiring for the pattern */ ),
#          .o_dac_dat_b ( /* likewise for dac_b */ ),
#          .i_external_interlock ( exp_p_in[1] ),  // pick a free exp pin
#          .i_feature_selected   ( exp_p_in[2] ),  // see WARNING below
#          .o_lock_state  ( led_o[3:0] ),
#          .o_lock_fault  ( led_o[4]   ),
#          .o_trace_ready ( led_o[5]   ),
#          .o_heartbeat   ( led_o[6]   ),  // see WARNING below
#          .sys_addr  ( sys_addr ), .sys_wdata ( sys_wdata ), .sys_sel ( sys_sel ),
#          .sys_wen   ( sys_wen[6] ), .sys_ren  ( sys_ren[6] ),
#          .sys_rdata ( sys_rdata[6*32+31:6*32] ),
#          .sys_err   ( sys_err[6] ), .sys_ack  ( sys_ack[6] )
#        );
#
#      Double-check the exact `adc_dat_a`/`adc_dat_b`/DAC-summing net names
#      against whatever red_pitaya_top.v revision you're actually on --
#      they've shifted between Red Pitaya firmware releases.
#
#      ------------------------------------------------------------------
#      WARNINGS FROM THE PRE-SHIP AUDIT -- read before wiring
#      ------------------------------------------------------------------
#      1. i_feature_selected was previously suggested as `1'b1`. DO NOT
#         DO THAT. lock_fsm leaves WIDE_SCAN on
#         (trace_ready | feature_selected), so tying it high skips the
#         wide scan and the trace capture entirely in one cycle and
#         jumps straight to a zoom scan around ramp_center. The whole
#         acquisition sequence in packet 9.2 is defeated.
#
#         Strictly, this signal should not be a pin at all: packet 9.2
#         step 2 makes feature selection a PC action after the operator
#         clicks the trace. There is no register for it yet. Until there
#         is, drive it from a spare exp pin (or tie it LOW and drive the
#         sequence from trace_ready).
#
#      2. .rst( ~adc_rstn ) -- CHECK THE INVERSION. The core uses a
#         synchronous, active-HIGH reset. Get this backwards and the
#         entire core sits in reset reading zeros, which from the bench
#         is indistinguishable from a broken register map. That is what
#         o_heartbeat is for: it is a free-running counter bit, so an LED
#         that blinks proves the core is clocked and out of reset before
#         you debug anything else.
#
#      3. The sys_* register bus is assumed to be in the adc_clk domain.
#         That is true for RedPitaya-FPGA v0.94, where red_pitaya_ps
#         presents the bus on adc_clk, but this repo does not pin a
#         commit and does not verify it. If a different revision presents
#         sys_* on clk_fpga_0 instead, the 32-bit write data crosses two
#         asynchronous 125 MHz clocks with NO synchroniser, and the
#         constraints file already contains
#             set_false_path -from [get_clocks clk_fpga_0] -to [get_clocks adc_clk]
#         which tells the tool not to check it. Torn register writes
#         would follow. PIN THE COMMIT and confirm the domain in the
#         netlist before the first bitstream.
#
# USAGE:
#   vivado -mode batch -source scripts/build_posm_red_pitaya.tcl \
#          -tclargs <path-to-RedPitaya-FPGA-repo>
#
# OUTPUT:
#   vivado/build/posm_red_pitaya.runs/impl_1/red_pitaya_top.bit
#
# The resulting .bit still needs converting to .bit.bin for Red Pitaya OS to
# load (see FPGA Reprogramming Guide -> write_cfgmem, or the project's own
# bit2bin step if using the official Makefile flow instead of this script).
# ============================================================================

set rp_fpga_root [lindex $argv 0]
if {$rp_fpga_root eq ""} {
    puts "ERROR: pass the path to your RedPitaya-FPGA checkout as -tclargs <path>"
    exit 1
}

set repo_root   [file normalize [file join [file dirname [info script]] ..]]
set gen_verilog [file join $repo_root "build" "out" "red_pitaya_lock_core.v"]
set constraints [file join $repo_root "constraints" "board_specific" "RedPitaya_125-14_constraint.xdc"]

if {![file exists $gen_verilog]} {
    puts "ERROR: $gen_verilog not found -- run `python3 build/generate_verilog.py` first"
    exit 1
}

if {![file exists $constraints]} {
    puts "ERROR: $constraints not found"
    exit 1
}

set proj_name "posm_red_pitaya"
set part      "xc7z010clg400-1"
set build_dir [file join $repo_root "vivado" "build"]

file mkdir $build_dir
create_project -force $proj_name $build_dir -part $part

# ---------------------------------------------------------------------------
# Sources: Red Pitaya's own common RTL (PS7 wrapper, PLL, ADC/DAC IO, HK,
# scope, etc.) + this project's generated Verilog. red_pitaya_top.v must
# already contain the red_pitaya_lock_core instantiation described above.
# ---------------------------------------------------------------------------
set common_rtl_dir [file join $rp_fpga_root "rtl"]
set prj_rtl_dir     [file join $rp_fpga_root "prj" "v0.94" "rtl"]

if {![file isdirectory $common_rtl_dir]} {
    puts "ERROR: $common_rtl_dir not found -- check the RedPitaya-FPGA path you passed in"
    exit 1
}

add_files -norecurse [glob -nocomplain [file join $common_rtl_dir "*.v"]]
add_files -norecurse [glob -nocomplain [file join $common_rtl_dir "*.sv"]]
add_files -norecurse [glob -nocomplain [file join $prj_rtl_dir "*.v"]]
add_files -norecurse [glob -nocomplain [file join $prj_rtl_dir "*.sv"]]
add_files -norecurse $gen_verilog

add_files -fileset constrs_1 -norecurse $constraints

# The Zynq PS7 wrapper (red_pitaya_ps.v / .xci) typically ships as part of
# RedPitaya-FPGA's own project generation, not as plain RTL -- if your
# checkout uses a block-design-based PS wrapper (an .xci/.bd rather than a
# flat .v), source that project's own project-mode/tcl generation for the
# PS7 piece instead of trying to add it here as a raw file. This script
# assumes the flat-RTL v0.94-style layout where red_pitaya_ps.v is plain
# Verilog wrapping the PS7 primitive.

set_property top red_pitaya_top [current_fileset]
update_compile_order -fileset sources_1

# ---------------------------------------------------------------------------
# Synthesis, implementation, bitstream
# ---------------------------------------------------------------------------
launch_runs synth_1 -jobs [get_param general.maxThreads]
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {
    puts "ERROR: synth_1 did not complete"
    exit 1
}

launch_runs impl_1 -to_step write_bitstream -jobs [get_param general.maxThreads]
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    puts "ERROR: impl_1 did not complete"
    exit 1
}

set bit_path [file join $build_dir "$proj_name.runs" "impl_1" "red_pitaya_top.bit"]
puts "Build complete: $bit_path"
puts "Next: convert to .bit.bin for Red Pitaya OS 2.00+ (write_cfgmem or the"
puts "official Makefile's bit2bin step), then load with fpgautil / overlay.sh."
