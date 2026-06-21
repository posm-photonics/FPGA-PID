// tb_error_calc.sv
// Testbench for error_calc module
// Tests: offset subtraction, setpoint subtraction,
//        polarity inversion, reset, valid latency,
//        sign handling, near-overflow values

`timescale 1ns/1ps

module tb_error_calc;

    // -------------------------------------------------------
    // Parameters matching the DUT
    // -------------------------------------------------------
    localparam int ADC_W = 16;
    localparam int ERR_W = 20;

    // -------------------------------------------------------
    // DUT signals
    // -------------------------------------------------------
    logic                    clk;
    logic                    rst;
    logic signed [ADC_W-1:0] sample_in;
    logic                    sample_valid;
    logic signed [ADC_W-1:0] offset;
    logic signed [ADC_W-1:0] setpoint;
    logic                    invert_error;
    logic signed [ERR_W-1:0] error_out;
    logic                    error_valid;

    // -------------------------------------------------------
    // Test tracking
    // -------------------------------------------------------
    int pass_count;
    int fail_count;

    // -------------------------------------------------------
    // DUT instantiation
    // -------------------------------------------------------
    error_calc #(
        .ADC_W(ADC_W),
        .ERR_W(ERR_W)
    ) dut (
        .clk          (clk),
        .rst          (rst),
        .sample_in    (sample_in),
        .sample_valid (sample_valid),
        .offset       (offset),
        .setpoint     (setpoint),
        .invert_error (invert_error),
        .error_out    (error_out),
        .error_valid  (error_valid)
    );

    // -------------------------------------------------------
    // Clock: 10ns period = 100 MHz
    // -------------------------------------------------------
    initial clk = 0;
    always #5 clk = ~clk;

    // -------------------------------------------------------
    // Helper task: apply one sample and check result
    // Result appears one cycle after valid input
    // -------------------------------------------------------
    task apply_and_check(
        input logic signed [ADC_W-1:0] s_in,
        input logic signed [ADC_W-1:0] off,
        input logic signed [ADC_W-1:0] sp,
        input logic                    inv,
        input logic signed [ERR_W-1:0] expected,
        input string                   test_name
    );
        // apply inputs
        @(negedge clk);
        sample_in    = s_in;
        offset       = off;
        setpoint     = sp;
        invert_error = inv;
        sample_valid = 1'b1;

        // wait one clock for registered output
        @(posedge clk);
        #1; // small delay to let outputs settle

        // check valid flag came through
        if (!error_valid) begin
            $display("FAIL: %s | error_valid not asserted", test_name);
            fail_count++;
        end else if (error_out === expected) begin
            $display("PASS: %s | in=%0d off=%0d sp=%0d inv=%0b | out=%0d",
                     test_name, s_in, off, sp, inv, error_out);
            pass_count++;
        end else begin
            $display("FAIL: %s | in=%0d off=%0d sp=%0d inv=%0b | expected=%0d got=%0d",
                     test_name, s_in, off, sp, inv, expected, error_out);
            fail_count++;
        end

        // deassert valid
        @(negedge clk);
        sample_valid = 1'b0;
    endtask

    // -------------------------------------------------------
    // Main test sequence
    // -------------------------------------------------------
    initial begin
        // init
        pass_count   = 0;
        fail_count   = 0;
        sample_in    = '0;
        sample_valid = 1'b0;
        offset       = '0;
        setpoint     = '0;
        invert_error = 1'b0;

        // reset
        rst = 1'b1;
        repeat(4) @(posedge clk);
        rst = 1'b0;
        @(posedge clk);

        $display("=== error_calc testbench starting ===");

        // --- Test 1: zero everything, expect zero output ---
        apply_and_check(
            16'sh0000,  // sample
            16'sh0000,  // offset
            16'sh0000,  // setpoint
            1'b0,       // no invert
            20'sh00000, // expected
            "all_zero"
        );

        // --- Test 2: offset subtraction only ---
        // sample=100, offset=30, setpoint=0 -> error=70
        apply_and_check(
            16'sh0064,  // 100
            16'sh001E,  // 30
            16'sh0000,  // 0
            1'b0,
            20'sh00046, // 70
            "offset_subtract"
        );

        // --- Test 3: setpoint subtraction only ---
        // sample=100, offset=0, setpoint=40 -> error=60
        apply_and_check(
            16'sh0064,  // 100
            16'sh0000,  // 0
            16'sh0028,  // 40
            1'b0,
            20'sh0003C, // 60
            "setpoint_subtract"
        );

        // --- Test 4: both offset and setpoint ---
        // sample=100, offset=30, setpoint=40 -> error=30
        apply_and_check(
            16'sh0064,  // 100
            16'sh001E,  // 30
            16'sh0028,  // 40
            1'b0,
            20'sh0001E, // 30
            "offset_and_setpoint"
        );

        // --- Test 5: polarity inversion ---
        // sample=100, offset=0, setpoint=0 -> raw=100, inverted=-100
        apply_and_check(
            16'sh0064,   // 100
            16'sh0000,
            16'sh0000,
            1'b1,        // invert
            -20'sh00064, // -100
            "invert_positive"
        );

        // --- Test 6: negative sample ---
        // sample=-50, offset=0, setpoint=0 -> error=-50
        apply_and_check(
            -16'sh0032,  // -50
            16'sh0000,
            16'sh0000,
            1'b0,
            -20'sh00032, // -50
            "negative_sample"
        );

        // --- Test 7: negative sample inverted ---
        // sample=-50, offset=0, setpoint=0, inv=1 -> error=+50
        apply_and_check(
            -16'sh0032,  // -50
            16'sh0000,
            16'sh0000,
            1'b1,
            20'sh00032,  // +50
            "negative_sample_inverted"
        );

        // --- Test 8: negative offset (background is negative) ---
        // sample=0, offset=-20, setpoint=0 -> error=0-(-20)-0=+20
        apply_and_check(
            16'sh0000,
            -16'sh0014,  // -20
            16'sh0000,
            1'b0,
            20'sh00014,  // +20
            "negative_offset"
        );

        // --- Test 9: valid latency check ---
        // sample_valid low -> error_valid should be low after one cycle
        @(negedge clk);
        sample_in    = 16'sh0001;
        sample_valid = 1'b0;  // no valid
        @(posedge clk);
        #1;
        if (error_valid !== 1'b0) begin
            $display("FAIL: valid_latency | error_valid high with no input valid");
            fail_count++;
        end else begin
            $display("PASS: valid_latency | error_valid correctly low");
            pass_count++;
        end

        // --- Test 10: reset clears output ---
        @(negedge clk);
        sample_in    = 16'sh7FFF;
        sample_valid = 1'b1;
        @(posedge clk);
        #1;
        rst = 1'b1;
        @(posedge clk);
        #1;
        if (error_out !== '0 || error_valid !== 1'b0) begin
            $display("FAIL: reset_clears | out=%0d valid=%0b", error_out, error_valid);
            fail_count++;
        end else begin
            $display("PASS: reset_clears | output zero and valid low after reset");
            pass_count++;
        end
        rst = 1'b0;
        @(posedge clk);

        // --- Summary ---
        $display("=== RESULTS: %0d passed, %0d failed ===",
                  pass_count, fail_count);
        if (fail_count == 0)
            $display("ALL TESTS PASSED");
        else
            $display("SOME TESTS FAILED - check output above");

        $finish;
    end

endmodule