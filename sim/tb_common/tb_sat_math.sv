// tb_sat_math.sv
// Testbench for sat_math_pkg
// Tests: positive overflow, negative overflow, zero,
//        +1, -1, and exact boundary values

`timescale 1ns/1ps

module tb_sat_math;
    import sat_math_pkg::*;

    // test variables
    logic signed [17:0] test_in_18;
    logic signed [39:0] test_in_40;
    logic signed [15:0] result_16;
    int pass_count;
    int fail_count;

    // helper task
    task check_16(
        input logic signed [17:0] in_val,
        input logic signed [15:0] expected,
        input string test_name
    );
        result_16 = sat_18to16(in_val);
        if (result_16 === expected) begin
            $display("PASS: %s | in=%0d out=%0d", test_name, in_val, result_16);
            pass_count++;
        end else begin
            $display("FAIL: %s | in=%0d expected=%0d got=%0d",
                      test_name, in_val, expected, result_16);
            fail_count++;
        end
    endtask

    initial begin
        pass_count = 0;
        fail_count = 0;

        $display("=== sat_math testbench starting ===");

        // --- sat_18to16 tests ---
        // positive overflow: 32768 -> clamp to 32767
        check_16(18'sh08000, 16'sh7FFF, "pos_overflow");

        // negative overflow: -32769 -> clamp to -32768
        check_16(-18'sh08001, -16'sh8000, "neg_overflow");

        // zero passthrough
        check_16(18'sh0, 16'sh0, "zero");

        // +1 passthrough
        check_16(18'sh1, 16'sh1, "plus_one");

        // -1 passthrough
        check_16(-18'sh1, -16'sh1, "minus_one");

        // exact max boundary: 32767 -> 32767
        check_16(18'sh7FFF, 16'sh7FFF, "exact_max");

        // exact min boundary: -32768 -> -32768
        check_16(-18'sh8000, -16'sh8000, "exact_min");

        // one above max: 32768 -> 32767
        check_16(18'sh8000, 16'sh7FFF, "one_above_max");

        // one below min: -32769 -> -32768
        check_16(-18'sh8001, -16'sh8000, "one_below_min");

        // --- summary ---
        $display("=== RESULTS: %0d passed, %0d failed ===",
                  pass_count, fail_count);

        if (fail_count == 0)
            $display("ALL TESTS PASSED");
        else
            $display("SOME TESTS FAILED - check output above");

        $finish;
    end

endmodule