# create_vivado_project.tcl
# Run this from the repo root in the Vivado TCL console:
#   source scripts/create_vivado_project.tcl
#
# This script creates the Vivado project from scratch
# using files from the repo. No .xpr file is committed
# to git because it contains absolute paths.

# -------------------------------------------------------
# Project settings — change these if needed
# -------------------------------------------------------
set project_name "fpga_mts_lock"
set project_dir  "./POSM_FPGA_LOCK"
set fpga_part    "xc7a35tcpg236-1"  ;# Basys 3 part number

# -------------------------------------------------------
# Create the project
# -------------------------------------------------------
create_project $project_name $project_dir -part $fpga_part -force

# -------------------------------------------------------
# Set project properties
# -------------------------------------------------------
set_property target_language    SystemVerilog [current_project]
set_property simulator_language SystemVerilog [current_project]
set_property default_lib        work          [current_project]

# -------------------------------------------------------
# Add RTL source files
# -------------------------------------------------------

# common utilities
add_files -norecurse {
    rtl/common/sat_math.sv
    rtl/common/round_shift.sv
    rtl/common/sign_extend.sv
    rtl/common/stream_delay.sv
    rtl/common/edge_detect.sv
    rtl/common/sync_reset.sv
}

# ADC
add_files -norecurse {
    rtl/adc/adc_formatter.sv
    rtl/adc/adc_guard.sv
}

# DAC
add_files -norecurse {
    rtl/dac/dac_fast_formatter.sv
    rtl/dac/dac_slow_formatter.sv
}

# DSP
add_files -norecurse {
    rtl/dsp/error_calc.sv
    rtl/dsp/pi_core.sv
    rtl/dsp/optional_compensator.sv
    rtl/dsp/lock_watch.sv
}

# Control
add_files -norecurse {
    rtl/control/ramp_scan.sv
    rtl/control/slow_recenter.sv
    rtl/control/output_limiter.sv
    rtl/control/fault_gate.sv
    rtl/control/lock_fsm.sv
}

# Autolock
add_files -norecurse {
    rtl/autolock/robust_autolock.sv
    rtl/autolock/peak_tracker.sv
    rtl/autolock/feature_matcher.sv
}

# Bus
add_files -norecurse {
    rtl/bus/register_bank.sv
    rtl/bus/register_defs_pkg.sv
}

# Top level
add_files -norecurse {
    rtl/top/lock_core_top.sv
    rtl/top/board_top_stub.sv
}

# -------------------------------------------------------
# Add simulation files
# -------------------------------------------------------
add_files -fileset sim_1 -norecurse {
    sim/tb_common/tb_sat_math.sv
    sim/tb_common/tb_round_shift.sv
    sim/tb_dsp/tb_error_calc.sv
    sim/tb_dsp/tb_pi_core.sv
    sim/tb_dsp/tb_iir_lowpass_1p.sv
    sim/tb_control/tb_ramp_scan.sv
    sim/tb_control/tb_lock_fsm.sv
    sim/tb_system/tb_lock_core_top.sv
    sim/tb_system/fake_plant_model.sv
}

# -------------------------------------------------------
# Add constraints
# -------------------------------------------------------
add_files -fileset constrs_1 -norecurse {
    constraints/generic_placeholder.xdc
}

# -------------------------------------------------------
# Set top level modules
# -------------------------------------------------------
set_property top lock_core_top     [current_fileset]
set_property top tb_pi_core        [get_filesets sim_1]

# -------------------------------------------------------
# Update compile order
# -------------------------------------------------------
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

puts ""
puts "====================================================="
puts "Project created successfully at: $project_dir"
puts "To run a simulation:"
puts "  1. Open Vivado GUI"
puts "  2. Open project at $project_dir/$project_name.xpr"
puts "  3. Click Run Simulation"
puts "Or in TCL console:"
puts "  launch_simulation"
puts "  run all"
puts "====================================================="