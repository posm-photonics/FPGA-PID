// sat_math.sv
// Saturating arithmetic utilities
// All arithmetic is signed two's complement
// Width-documented, synthesizable, no implicit truncation

package sat_math_pkg;

    // -------------------------------------------------------
    // sat_signed
    // Saturate a wide signed value to a narrower signed output
    // IN_W  : input width  (wider)
    // OUT_W : output width (narrower)
    // -------------------------------------------------------
    function automatic logic signed [15:0] sat_signed_16;
        input logic signed [17:0] x;
        // max =  2^(16-1) - 1 =  32767
        // min = -2^(16-1)     = -32768
        if (x > 18'sh7FFF)
            sat_signed_16 = 16'sh7FFF;
        else if (x < -18'sh8000)
            sat_signed_16 = -16'sh8000;
        else
            sat_signed_16 = x[15:0];
    endfunction

    // -------------------------------------------------------
    // sat_signed_18to16
    // Saturate 18-bit signed to 16-bit signed
    // Used in error_calc -> DAC path
    // -------------------------------------------------------
    function automatic logic signed [15:0] sat_18to16;
        input logic signed [17:0] x;
        if (x > 18'sh7FFF)
            sat_18to16 = 16'sh7FFF;
        else if (x < -18'sh8000)
            sat_18to16 = -16'sh8000;
        else
            sat_18to16 = x[15:0];
    endfunction

    // -------------------------------------------------------
    // sat_signed_40to16
    // Saturate 40-bit accumulator to 16-bit DAC output
    // Used in pi_controller accumulator -> output
    // -------------------------------------------------------
    function automatic logic signed [15:0] sat_40to16;
        input logic signed [39:0] x;
        if (x > 40'sh7FFF)
            sat_40to16 = 16'sh7FFF;
        else if (x < -40'sh8000)
            sat_40to16 = -16'sh8000;
        else
            sat_40to16 = x[15:0];
    endfunction

    // -------------------------------------------------------
    // sat_signed_40to18
    // Saturate 40-bit accumulator to 18-bit error width
    // Used internally in pi_controller
    // -------------------------------------------------------
    function automatic logic signed [17:0] sat_40to18;
        input logic signed [39:0] x;
        if (x > 40'sh1FFFF)
            sat_40to18 = 18'sh1FFFF;
        else if (x < -40'sh20000)
            sat_40to18 = -18'sh20000;
        else
            sat_40to18 = x[17:0];
    endfunction

endpackage// sat_math.sv
// Saturating arithmetic utilities
// All arithmetic is signed two's complement
// Width-documented, synthesizable, no implicit truncation

package sat_math_pkg;

    // -------------------------------------------------------
    // sat_signed
    // Saturate a wide signed value to a narrower signed output
    // IN_W  : input width  (wider)
    // OUT_W : output width (narrower)
    // -------------------------------------------------------
    function automatic logic signed [15:0] sat_signed_16;
        input logic signed [17:0] x;
        // max =  2^(16-1) - 1 =  32767
        // min = -2^(16-1)     = -32768
        if (x > 18'sh7FFF)
            sat_signed_16 = 16'sh7FFF;
        else if (x < -18'sh8000)
            sat_signed_16 = -16'sh8000;
        else
            sat_signed_16 = x[15:0];
    endfunction

    // -------------------------------------------------------
    // sat_signed_18to16
    // Saturate 18-bit signed to 16-bit signed
    // Used in error_calc -> DAC path
    // -------------------------------------------------------
    function automatic logic signed [15:0] sat_18to16;
        input logic signed [17:0] x;
        if (x > 18'sh7FFF)
            sat_18to16 = 16'sh7FFF;
        else if (x < -18'sh8000)
            sat_18to16 = -16'sh8000;
        else
            sat_18to16 = x[15:0];
    endfunction

    // -------------------------------------------------------
    // sat_signed_40to16
    // Saturate 40-bit accumulator to 16-bit DAC output
    // Used in pi_controller accumulator -> output
    // -------------------------------------------------------
    function automatic logic signed [15:0] sat_40to16;
        input logic signed [39:0] x;
        if (x > 40'sh7FFF)
            sat_40to16 = 16'sh7FFF;
        else if (x < -40'sh8000)
            sat_40to16 = -16'sh8000;
        else
            sat_40to16 = x[15:0];
    endfunction

    // -------------------------------------------------------
    // sat_signed_40to18
    // Saturate 40-bit accumulator to 18-bit error width
    // Used internally in pi_controller
    // -------------------------------------------------------
    function automatic logic signed [17:0] sat_40to18;
        input logic signed [39:0] x;
        if (x > 40'sh1FFFF)
            sat_40to18 = 18'sh1FFFF;
        else if (x < -40'sh20000)
            sat_40to18 = -18'sh20000;
        else
            sat_40to18 = x[17:0];
    endfunction

endpackage