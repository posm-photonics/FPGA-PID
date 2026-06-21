// error_calc.sv
// Converts validated ADC sample into a signed control error
// e[n] = p(sample_in - offset - setpoint)
// p = +1 or -1 depending on invert_error
//
// Signal widths:
//   sample_in : signed ADC_W (16-bit)
//   offset    : signed ADC_W (16-bit)
//   setpoint  : signed ADC_W (16-bit)
//   error_out : signed ERR_W (20-bit) -- extra bits guard against subtraction overflow
//
// Latency: 1 clock cycle (registered output)
// Reset behavior: output zero, error_valid low until first valid input

`timescale 1ns/1ps

module error_calc #(
    parameter int ADC_W = 16,
    parameter int ERR_W = 20
)(
    input  logic                     clk,
    input  logic                     rst,

    // sample stream in
    input  logic signed [ADC_W-1:0]  sample_in,
    input  logic                     sample_valid,

    // runtime config registers
    input  logic signed [ADC_W-1:0]  offset,      // DC background offset
    input  logic signed [ADC_W-1:0]  setpoint,    // desired lock point
    input  logic                     invert_error, // flips sign for slope polarity

    // error stream out
    output logic signed [ERR_W-1:0]  error_out,
    output logic                     error_valid
);

    // -------------------------------------------------------
    // Internal signals
    // Width: ADC_W+1 for each subtraction to catch carry/sign
    // Final result sign-extended to ERR_W
    // -------------------------------------------------------
    logic signed [ADC_W:0]   x_corr;      // sample_in - offset  (17-bit)
    logic signed [ADC_W+1:0] err_raw;     // x_corr - setpoint   (18-bit)
    logic signed [ERR_W-1:0] err_wide;    // sign-extended to 20-bit
    logic signed [ERR_W-1:0] err_inv;     // polarity applied

    // -------------------------------------------------------
    // Combinational error computation
    // Do all math in wider types to avoid silent truncation
    // -------------------------------------------------------
    always_comb begin
        // step 1: subtract offset from sample
        // sign-extend both to ADC_W+1 before subtracting
        x_corr  = $signed({sample_in[ADC_W-1], sample_in})
                - $signed({offset[ADC_W-1],    offset});

        // step 2: subtract setpoint
        // sign-extend x_corr to ADC_W+2
        err_raw = $signed({x_corr[ADC_W], x_corr})
                - $signed({setpoint[ADC_W-1], setpoint[ADC_W-1], setpoint});

        // step 3: sign-extend to ERR_W
        err_wide = ERR_W'(signed'(err_raw));

        // step 4: apply polarity
        err_inv = invert_error ? -err_wide : err_wide;
    end

    // -------------------------------------------------------
    // Registered output
    // Output is zero and invalid during reset
    // Valid follows sample_valid with one cycle latency
    // -------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            error_out   <= '0;
            error_valid <= 1'b0;
        end else begin
            error_valid <= sample_valid;
            if (sample_valid) begin
                error_out <= err_inv;
            end
        end
    end

endmodule