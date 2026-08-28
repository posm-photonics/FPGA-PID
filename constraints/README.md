# Constraints

## Files

| File | Board | Use |
|---|---|---|
| `board_specific/RedPitaya_125-14_constraint.xdc` | Red Pitaya STEMlab 125-14 (XC7Z010) | The real target |
| `board_specific/Basys3_constraint.xdc` | Basys 3 (XC7A35T) | M4 dev-board demo only |

The Basys 3 file says so in its own header: "Do NOT use for
lock_core_top synthesis until M4."

## Clocking

`create_clock -period 8.000 -name adc_clk [get_ports adc_clk_p_i]`

The core is a single synchronous domain clocked by `adc_clk`.

## Read this before the first bitstream

The file contains, inherited from Red Pitaya's own constraints:

```tcl
set_false_path -from [get_clocks clk_fpga_0] -to [get_clocks adc_clk]
```

and a comment asserting that "posm_lock_core and its sys_* register bus
run entirely within the adc_clk domain... no new clock domain crossing
is introduced by this project's addition".

That assertion is **probably** correct for RedPitaya-FPGA v0.94, where
`red_pitaya_ps` presents the `sys_*` bus on `adc_clk`. It is not
verified anywhere, this repo does not contain that file, and
`scripts/build_posm_red_pitaya.tcl` does not pin a commit.

If a different revision presents `sys_*` on `clk_fpga_0` instead, then
32 bits of write data and 12 bits of address cross two asynchronous
125 MHz clocks with no synchroniser, and the false path above tells the
tool not to check it. The symptom is a register that occasionally
latches a torn value, mixing old and new bits. For `FAST_OUT_MAX` that
means a garbage limit on an actuator.

**Pin the RedPitaya-FPGA commit and confirm the domain in the netlist
before building.**

## Still missing

No timing exceptions or `ASYNC_REG` properties are declared for the two
asynchronous board inputs (`i_external_interlock`, `i_feature_selected`).
They are now synchronised in RTL with `FFSynchronizer`, but the
synchroniser flops should also carry `ASYNC_REG` so the placer keeps
them together. Add that when the design first goes through
implementation.
