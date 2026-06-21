// tb_pi_core.sv
// Testbench for pi_core module
//
// Tests:
//   1.  P-only response (ki=0)
//   2.  I-only response (kp=0)
//   3.  PI combined response
//   4.  Positive step convergence
//   5.  Negative step convergence
//   6.  Output clamp high
//   7.  Output clamp low
//   8.  Anti-windup
//   9.  Hold mode
//   10. Integrator reset
//   11. Integrator load
//   12. Zero gains
//   13. Lock disable safe output

`timescale 1ns/1ps

module tb_pi_core;

    // -------------------------------------------------------
    // Parameters
    // -------------------------------------------------------
    localparam int ERR_W     = 20;
    localparam int OUT_W     = 16;
    localparam int GAIN_W    = 18;
    localparam int GAIN_FRAC = 14;
    localparam int ACC_W     = 40;

    // Q3.14 gain constants
    // real_gain = value / 2^14 = value / 16384
    localparam signed [GAIN_W-1:0] KP_HALF  = 18'sh02000; // 0.5
    localparam signed [GAIN_W-1:0] KP_ONE   = 18'sh04000; // 1.0
    localparam signed [GAIN_W-1:0] KI_SMALL = 18'sh00100; // ~0.004
    localparam signed [GAIN_W-1:0] ZERO_G   = 18'sh00000; // 0.0

    // -------------------------------------------------------
    // DUT signals
    // -------------------------------------------------------
    logic                     clk;
    logic                     rst;
    logic signed [ERR_W-1:0]  error_in;
    logic                     error_valid;
    logic signed [GAIN_W-1:0] kp;
    logic signed [GAIN_W-1:0] ki;
    logic                     lock_enable;
    logic                     hold_enable;
    logic                     integrator_reset;
    logic                     integrator_load;
    logic signed [ACC_W-1:0]  load_value;
    logic signed [OUT_W-1:0]  out_min;
    logic signed [OUT_W-1:0]  out_max;
    logic signed [OUT_W-1:0]  out_safe;
    logic signed [OUT_W-1:0]  control_out;
    logic                     control_valid;
    logic                     sat_hi;
    logic                     sat_lo;

    // -------------------------------------------------------
    // Test tracking
    // -------------------------------------------------------
    int pass_count;
    int fail_count;

    // -------------------------------------------------------
    // DUT instantiation
    // -------------------------------------------------------
    pi_core #(
        .ERR_W    (ERR_W),
        .OUT_W    (OUT_W),
        .GAIN_W   (GAIN_W),
        .GAIN_FRAC(GAIN_FRAC),
        .ACC_W    (ACC_W)
    ) dut (
        .clk              (clk),
        .rst              (rst),
        .error_in         (error_in),
        .error_valid      (error_valid),
        .kp               (kp),
        .ki               (ki),
        .lock_enable      (lock_enable),
        .hold_enable      (hold_enable),
        .integrator_reset (integrator_reset),
        .integrator_load  (integrator_load),
        .load_value       (load_value),
        .out_min          (out_min),
        .out_max          (out_max),
        .out_safe         (out_safe),
        .control_out      (control_out),
        .control_valid    (control_valid),
        .sat_hi           (sat_hi),
        .sat_lo           (sat_lo)
    );

    // -------------------------------------------------------
    // Clock
    // -------------------------------------------------------
    initial clk = 0;
    always #5 clk = ~clk;

    // -------------------------------------------------------
    // Helper: apply one error sample and wait for output
    // -------------------------------------------------------
    task send_error(input logic signed [ERR_W-1:0] err);
        @(negedge clk);
        error_in    = err;
        error_valid = 1'b1;
        @(posedge clk);
        #1;
        @(negedge clk);
        error_valid = 1'b0;
    endtask

    // -------------------------------------------------------
    // Helper: reset the DUT cleanly
    // -------------------------------------------------------
    task do_reset();
        rst              = 1'b1;
        integrator_reset = 1'b0;
        integrator_load  = 1'b0;
        lock_enable      = 1'b0;
        hold_enable      = 1'b0;
        error_valid      = 1'b0;
        error_in         = '0;
        repeat(4) @(posedge clk);
        rst = 1'b0;
        @(posedge clk);
    endtask

    // -------------------------------------------------------
    // Helper: check a condition
    // -------------------------------------------------------
    task check(
        input logic condition,
        input string test_name,
        input string detail
    );
        if (condition) begin
            $display("PASS: %s | %s", test_name, detail);
            pass_count++;
        end else begin
            $display("FAIL: %s | %s", test_name, detail);
            fail_count++;
        end
    endtask

    // -------------------------------------------------------
    // Main test sequence
    // -------------------------------------------------------
    initial begin
        pass_count = 0;
        fail_count = 0;

        // default limits and safe code
        out_min  = -16'sh7FFF;
        out_max  =  16'sh7FFF;
        out_safe =  16'sh0000;
        kp       = KP_ONE;
        ki       = KI_SMALL;
        load_value = '0;

        $display("=== pi_core testbench starting ===");

        // ===================================================
        // Test 1: P-only response
        // ki=0, kp=1.0, error=100
        // expected output ~ 100 (Kp * error, no integrator)
        // ===================================================
        do_reset();
        kp          = KP_ONE;
        ki          = ZERO_G;
        lock_enable = 1'b1;

        send_error(20'sh00064); // error = 100
        @(posedge clk); #1;

        check(control_out > 0,
              "p_only",
              $sformatf("output=%0d should be positive for positive error", control_out));
        check(control_valid === 1'b1,
              "p_only_valid",
              "control_valid should be asserted");

        // ===================================================
        // Test 2: I-only accumulation
        // kp=0, ki=small, feed same error 10 times
        // output should grow each cycle
        // ===================================================
        do_reset();
        kp          = ZERO_G;
        ki          = KI_SMALL;
        lock_enable = 1'b1;

        begin
            logic signed [OUT_W-1:0] prev_out;
            logic signed [OUT_W-1:0] growing;
            prev_out = 0;
            growing  = 1'b1;

            repeat(10) begin
                send_error(20'sh00064); // error = 100
                @(posedge clk); #1;
                if (control_out < prev_out) growing = 1'b0;
                prev_out = control_out;
            end
            check(growing === 1'b1,
                  "i_only_accumulates",
                  $sformatf("output grew to %0d over 10 cycles", control_out));
        end

        // ===================================================
        // Test 3: PI positive step convergence
        // Feed constant positive error, output should increase
        // then integrator drives it toward rail
        // ===================================================
        do_reset();
        kp          = KP_HALF;
        ki          = KI_SMALL;
        lock_enable = 1'b1;

        begin
            logic signed [OUT_W-1:0] out_after_5;
            logic signed [OUT_W-1:0] out_after_20;

            repeat(5)  send_error(20'sh00200); // error = 512
            out_after_5 = control_out;
            repeat(15) send_error(20'sh00200);
            out_after_20 = control_out;

            check(out_after_20 > out_after_5,
                  "pi_positive_step_grows",
                  $sformatf("out@5=%0d out@20=%0d", out_after_5, out_after_20));
        end

        // ===================================================
        // Test 4: Negative step
        // Feed constant negative error, output should decrease
        // ===================================================
        do_reset();
        kp          = KP_HALF;
        ki          = KI_SMALL;
        lock_enable = 1'b1;

        begin
            logic signed [OUT_W-1:0] out_after_20;
            repeat(20) send_error(-20'sh00200); // error = -512
            out_after_20 = control_out;
            check(out_after_20 < 0,
                  "pi_negative_step",
                  $sformatf("out=%0d should be negative", out_after_20));
        end

        // ===================================================
        // Test 5: Output clamp high
        // Large positive error should hit out_max
        // sat_hi should assert
        // ===================================================
        do_reset();
        kp          = KP_ONE;
        ki          = KI_SMALL;
        lock_enable = 1'b1;
        out_max     = 16'sh0064; // clamp at 100

        repeat(30) send_error(20'sh07FFF); // large positive error
        @(posedge clk); #1;

        check(control_out === 16'sh0064,
              "clamp_high",
              $sformatf("out=%0d should be clamped to 100", control_out));
        check(sat_hi === 1'b1,
              "sat_hi_flag",
              "sat_hi should be asserted when clamped high");

        out_max = 16'sh7FFF; // restore

        // ===================================================
        // Test 6: Output clamp low
        // Large negative error should hit out_min
        // sat_lo should assert
        // ===================================================
        do_reset();
        kp          = KP_ONE;
        ki          = KI_SMALL;
        lock_enable = 1'b1;
        out_min     = -16'sh0064; // clamp at -100

        repeat(30) send_error(-20'sh07FFF); // large negative error
        @(posedge clk); #1;

        check(control_out === -16'sh0064,
              "clamp_low",
              $sformatf("out=%0d should be clamped to -100", control_out));
        check(sat_lo === 1'b1,
              "sat_lo_flag",
              "sat_lo should be asserted when clamped low");

        out_min = -16'sh7FFF; // restore

        // ===================================================
        // Test 7: Anti-windup
        // Clamp output, then reverse error
        // Output should respond quickly without long unwind
        // ===================================================
        do_reset();
        kp          = KP_HALF;
        ki          = KI_SMALL;
        lock_enable = 1'b1;
        out_max     = 16'sh00C8; // clamp at 200

        // drive into positive saturation
        repeat(50) send_error(20'sh07FFF);

        // now feed negative error
        // with anti-windup the output should start dropping quickly
        begin
            logic signed [OUT_W-1:0] out_before;
            logic signed [OUT_W-1:0] out_after;
            out_before = control_out;
            repeat(5) send_error(-20'sh00200); // moderate negative error
            @(posedge clk); #1;
            out_after = control_out;

            check(out_after < out_before,
                  "anti_windup",
                  $sformatf("out dropped from %0d to %0d after error reversal",
                             out_before, out_after));
        end

        out_max = 16'sh7FFF;

        // ===================================================
        // Test 8: Hold mode
        // Output should freeze when hold_enable is high
        // ===================================================
        do_reset();
        kp          = KP_ONE;
        ki          = KI_SMALL;
        lock_enable = 1'b1;

        // build up some output
        repeat(5) send_error(20'sh00200);
        begin
            logic signed [OUT_W-1:0] held_val;
            held_val = control_out;

            // enable hold
            @(negedge clk);
            hold_enable = 1'b1;

            // send more errors - output should not change
            repeat(5) send_error(20'sh00200);
            @(posedge clk); #1;

            check(control_out === held_val,
                  "hold_mode",
                  $sformatf("output stayed at %0d during hold", held_val));

            hold_enable = 1'b0;
        end

        // ===================================================
        // Test 9: Integrator reset
        // ===================================================
        do_reset();
        kp          = ZERO_G;   // P-only so output reflects integrator
        ki          = KI_SMALL;
        lock_enable = 1'b1;

        // build up integrator
        repeat(20) send_error(20'sh00200);

        // reset integrator
        @(negedge clk);
        integrator_reset = 1'b1;
        @(posedge clk); #1;
        integrator_reset = 1'b0;

        // send one more sample to update output
        send_error(20'sh00200);
        @(posedge clk); #1;

        check(control_out === 16'sh0000,
              "integrator_reset",
              $sformatf("output=%0d should be zero after integrator reset with kp=0",
                         control_out));

        // ===================================================
        // Test 10: Integrator load
        // ===================================================
        do_reset();
        kp          = ZERO_G;
        ki          = ZERO_G;   // freeze integrator after load
        lock_enable = 1'b1;

        // load a known value into integrator
        @(negedge clk);
        load_value      = 40'sh0000004000; // 16384 = 1.0 in Q3.14 -> output ~ 1
        integrator_load = 1'b1;
        @(posedge clk); #1;
        integrator_load = 1'b0;

        // send zero error so output = integrator only
        send_error(20'sh00000);
        @(posedge clk); #1;

        check(control_out !== 16'sh0000,
              "integrator_load",
              $sformatf("output=%0d should reflect loaded integrator value",
                         control_out));

        // ===================================================
        // Test 11: Zero gains
        // No matter what error comes in, output should be zero
        // ===================================================
        do_reset();
        kp          = ZERO_G;
        ki          = ZERO_G;
        lock_enable = 1'b1;

        repeat(5) send_error(20'sh07FFF);
        @(posedge clk); #1;

        check(control_out === 16'sh0000,
              "zero_gains",
              $sformatf("output=%0d should be zero with both gains zero",
                         control_out));

        // ===================================================
        // Test 12: Lock disable outputs safe code
        // ===================================================
        do_reset();
        kp          = KP_ONE;
        ki          = KI_SMALL;
        lock_enable = 1'b0; // disabled
        out_safe    = 16'sh0042; // safe code = 66

        send_error(20'sh07FFF); // large error but lock is off
        @(posedge clk); #1;

        check(control_out === 16'sh0042,
              "lock_disable_safe",
              $sformatf("output=%0d should be safe code 66 when lock disabled",
                         control_out));

        // ===================================================
        // Summary
        // ===================================================
        $display("=== RESULTS: %0d passed, %0d failed ===",
                  pass_count, fail_count);
        if (fail_count == 0)
            $display("ALL TESTS PASSED");
        else
            $display("SOME TESTS FAILED - check output above");

        $finish;
    end

endmodule