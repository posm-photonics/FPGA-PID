// pi_core.sv
// Fixed-point PI controller for POSM MTS laser lock
//
// Implements:
//   I[n] = I[n-1] + Ki * e[n]        (with anti-windup)
//   u[n] = Kp * e[n] + I[n]
//   ulim[n] = clip(u[n], umin, umax)
//
// Signal widths:
//   error_in   : signed ERR_W  (20-bit from error_calc)
//   kp, ki     : signed GAIN_W (18-bit, Q3.14 format)
//   accumulator: signed ACC_W  (40-bit, prevents overflow)
//   control_out: signed OUT_W  (16-bit DAC command)
//
// Fixed-point gain format: Q3.14
//   real_gain = kp_register / 2^14
//   example: kp=0.5 -> store 8192 (0.5 * 2^14)
//   example: kp=2.0 -> store 32768 (2.0 * 2^14)
//
// Latency: 2 clock cycles
// Reset behavior: all state cleared, output zero, valid low

`timescale 1ns/1ps

module pi_core #(
    parameter int ERR_W    = 20,    // error input width from error_calc
    parameter int OUT_W    = 16,    // DAC output width
    parameter int GAIN_W   = 18,    // Kp and Ki register width
    parameter int GAIN_FRAC = 14,   // fractional bits in gain (Q3.14)
    parameter int ACC_W    = 40     // internal accumulator width
)(
    input  logic                      clk,
    input  logic                      rst,

    // error stream in (from error_calc)
    input  logic signed [ERR_W-1:0]   error_in,
    input  logic                      error_valid,

    // gain registers (written by software)
    input  logic signed [GAIN_W-1:0]  kp,
    input  logic signed [GAIN_W-1:0]  ki,

    // control inputs
    input  logic                      lock_enable,      // enables PI output
    input  logic                      hold_enable,      // freezes output, keeps integrator
    input  logic                      integrator_reset, // clears integrator to zero
    input  logic                      integrator_load,  // loads integrator from load_value
    input  logic signed [ACC_W-1:0]   load_value,       // value to load into integrator

    // output limits (written by software)
    input  logic signed [OUT_W-1:0]   out_min,
    input  logic signed [OUT_W-1:0]   out_max,

    // safe output code during fault/idle
    input  logic signed [OUT_W-1:0]   out_safe,

    // outputs
    output logic signed [OUT_W-1:0]   control_out,
    output logic                      control_valid,
    output logic                      sat_hi,           // output clamped high
    output logic                      sat_lo            // output clamped low
);

    // -------------------------------------------------------
    // Internal state
    // -------------------------------------------------------
    logic signed [ACC_W-1:0]    integrator;       // I[n] accumulator
    logic signed [ERR_W-1:0]    error_prev;       // e[n-1] for later PID extension

    // -------------------------------------------------------
    // Wide intermediate signals
    // Bit growth documentation:
    //   p_term_wide : ERR_W + GAIN_W = 20 + 18 = 38 bits
    //   i_term_wide : ERR_W + GAIN_W = 20 + 18 = 38 bits
    //   p_scaled    : 38 - GAIN_FRAC = 24 bits -> sign extended to ACC_W
    //   i_scaled    : 38 - GAIN_FRAC = 24 bits -> sign extended to ACC_W
    //   candidate   : ACC_W (40 bits)
    // -------------------------------------------------------
    logic signed [ERR_W+GAIN_W-1:0]  p_term_wide;  // Kp * e[n], full precision
    logic signed [ERR_W+GAIN_W-1:0]  i_term_wide;  // Ki * e[n], full precision
    logic signed [ACC_W-1:0]         p_scaled;     // p_term shifted by GAIN_FRAC
    logic signed [ACC_W-1:0]         i_scaled;     // i_term shifted by GAIN_FRAC
    logic signed [ACC_W-1:0]         candidate;    // u[n] before clamping
    logic signed [ACC_W-1:0]         int_candidate;// integrator before anti-windup

    // -------------------------------------------------------
    // Saturation detection on candidate output
    // Compare against sign-extended limits
    // -------------------------------------------------------
    logic sat_hi_comb;
    logic sat_lo_comb;
    logic signed [ACC_W-1:0] out_max_ext;
    logic signed [ACC_W-1:0] out_min_ext;

    // -------------------------------------------------------
    // Anti-windup logic
    // Suppress integrator update if:
    //   output is saturated high AND error is positive
    //   output is saturated low  AND error is negative
    // -------------------------------------------------------
    logic windup_suppress;

    // -------------------------------------------------------
    // Stage 1: combinational math
    // -------------------------------------------------------
    always_comb begin

        // sign-extend limits to accumulator width for comparison
        out_max_ext = ACC_W'(signed'(out_max));
        out_min_ext = ACC_W'(signed'(out_min));

        // multiply: full precision, no rounding yet
        // ERR_W + GAIN_W = 38 bits
        p_term_wide = error_in * kp;
        i_term_wide = error_in * ki;

        // scale: arithmetic right shift by GAIN_FRAC (14 bits)
        // this is the fixed-point divide by 2^14
        // sign is preserved by arithmetic shift
        p_scaled = ACC_W'(signed'(p_term_wide >>> GAIN_FRAC));
        i_scaled = ACC_W'(signed'(i_term_wide >>> GAIN_FRAC));

        // integrator candidate: I[n-1] + Ki*e[n]
        int_candidate = integrator + i_scaled;

        // output candidate: Kp*e[n] + I[n]
        // uses current integrator state (before update)
        candidate = p_scaled + integrator;

        // saturation detection
        sat_hi_comb = (candidate > out_max_ext);
        sat_lo_comb = (candidate < out_min_ext);

        // anti-windup: suppress integrator if pushing further into saturation
        windup_suppress = (sat_hi_comb && (i_scaled > 0))
                       || (sat_lo_comb && (i_scaled < 0));

    end

    // -------------------------------------------------------
    // Stage 2: registered output and state update
    // -------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            integrator    <= '0;
            error_prev    <= '0;
            control_out   <= '0;
            control_valid <= 1'b0;
            sat_hi        <= 1'b0;
            sat_lo        <= 1'b0;

        end else begin

            // default: hold current output, deassert valid
            control_valid <= 1'b0;

            // integrator reset takes priority over everything
            if (integrator_reset) begin
                integrator <= '0;

            // integrator load for smooth scan-to-lock handoff
            end else if (integrator_load) begin
                integrator <= load_value;

            // normal operation: update on valid error sample
            end else if (error_valid && lock_enable && !hold_enable) begin
                // update integrator unless anti-windup suppresses it
                if (!windup_suppress) begin
                    integrator <= int_candidate;
                end
            end

            // output update
            if (error_valid && lock_enable) begin

                if (hold_enable) begin
                    // freeze output, keep current control_out
                    control_valid <= 1'b1;

                end else begin
                    // clamp candidate to limits and register output
                    if (sat_hi_comb) begin
                        control_out <= out_max;
                        sat_hi      <= 1'b1;
                        sat_lo      <= 1'b0;
                    end else if (sat_lo_comb) begin
                        control_out <= out_min;
                        sat_hi      <= 1'b0;
                        sat_lo      <= 1'b1;
                    end else begin
                        // truncate: take lower OUT_W bits of candidate
                        // safe because we checked bounds above
                        control_out <= candidate[OUT_W-1:0];
                        sat_hi      <= 1'b0;
                        sat_lo      <= 1'b0;
                    end
                    control_valid <= 1'b1;

                end

            end else if (!lock_enable) begin
                // not locked: output safe code
                control_out   <= out_safe;
                sat_hi        <= 1'b0;
                sat_lo        <= 1'b0;
                control_valid <= error_valid;
            end

            // store previous error for future PID extension
            if (error_valid) begin
                error_prev <= error_in;
            end

        end
    end

endmodule