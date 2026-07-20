from amaranth import *


class DACSlowFormatter(Elaboratable):
    """
    ============================================================
    DAC SLOW FORMATTER
    ============================================================

    Purpose:
        Convert the FPGA slow controller command into the
        external DAC input representation.

    This block ONLY performs formatting and safety handling.

    Latency:
        Exactly one synchronous clock cycle from accepted input
        decision to DAC output update.

    ============================================================
    """

    def __init__(self, controller_width=24, dac_width=16):

        # ------------------------------------------------------
        # Configuration constants
        # ------------------------------------------------------

        # Width of internal slow controller arithmetic.
        # This is intentionally wider than the physical DAC.
        # Slow recentering and bias calculations may require
        # additional numerical range.
        self.controller_width = controller_width

        # Physical DAC bus width.
        # DAC devices receive unsigned digital codes.
        self.dac_width = dac_width


        # ------------------------------------------------------
        # Inputs
        # ------------------------------------------------------

        # Signed slow-controller command.
        # This represents the physical actuator command before
        # DAC formatting. It is not yet in DAC code space.
        self.i_command = Signal(signed(controller_width))

        self.i_output_enable = Signal()
        self.i_hold = Signal()

        # Indicates a safety fault.
        # Fault always overrides normal operation.
        self.i_fault_active = Signal()

        # Safe DAC output code.
        # This is already in DAC code space because it represents
        # the physical safe electrical output.
        self.i_safe_code = Signal(dac_width)

        # Minimum allowed controller command.
        # These are signed actuator limits, NOT DAC bus limits.
        self.i_min_code = Signal(signed(controller_width))

        # Maximum allowed controller command.
        self.i_max_code = Signal(signed(controller_width))

        # Selects DAC encoding.
        # 0: two's complement output representation
        # 1: offset binary output representation
        self.i_offset_binary = Signal()

        # ------------------------------------------------------
        # Outputs
        # ------------------------------------------------------

        # Final DAC bus value.
        # Unsigned because the external DAC receives a binary code.
        self.o_dac = Signal(dac_width)

        # Current formatted output.
        # Mirrors the actual DAC command after the output
        # register. Useful for diagnostics and lock monitoring.
        self.o_current = Signal(dac_width)

        # Indicates that the controller command exceeded the
        # configured actuator range.
        self.o_clamped = Signal()

        # ------------------------------------------------------
        # Internal state
        # ------------------------------------------------------

        # Previous DAC output.
        # Required for hold behavior. Hold preserves the last
        # commanded safe actuator value.
        self._held_output = Signal(dac_width)


        # Internal signed command after clamp.
        # Keeping this signed avoids mixing DAC encoding with
        # controller coordinate space.
        self._limited_command = Signal(
            signed(controller_width)
        )

        # Converted DAC representation before output register.
        self._formatted_code = Signal(dac_width)


    def elaborate(self, platform):

        m = Module()

        # Priority:
        #
        # 1. Fault
        # 2. Output disabled
        # 3. Hold
        # 4. Normal command
        #
        # Fault has highest priority because actuator safety
        # must override all normal control requests.

        selected_command = Signal(signed(self.controller_width))

        with m.If(self.i_fault_active):
            m.d.comb += selected_command.eq(0)

        with m.Elif(~self.i_output_enable):
            m.d.comb += selected_command.eq(0)

        with m.Elif(self.i_hold):
            m.d.comb += selected_command.eq(0)

        with m.Else():
            m.d.comb += selected_command.eq(self.i_command)


        # ------------------------------------------------------
        # Signed actuator clamp
        # ------------------------------------------------------

        # Clamp BEFORE DAC formatting.
        with m.If(selected_command > self.i_max_code):
            m.d.comb += [
                self._limited_command.eq(self.i_max_code),
                self.o_clamped.eq(1)
            ]

        with m.Elif(selected_command < self.i_min_code):
            m.d.comb += [
                self._limited_command.eq(self.i_min_code),
                self.o_clamped.eq(1)
            ]

        with m.Else():
            m.d.comb += [
                self._limited_command.eq(selected_command),
                self.o_clamped.eq(0)
            ]


        # ------------------------------------------------------
        # DAC representation conversion
        # ------------------------------------------------------

        #
        # Two's complement:
        #
        # Keep the binary representation directly.
        #
        # Offset binary:
        #
        # Move zero from the middle of the numerical range to
        # the DAC midpoint.
        #
        # DAC_code =
        #       signed_value + 2^(DAC_WIDTH-1)
        #

        with m.If(self.i_offset_binary):
            m.d.comb += self._formatted_code.eq(
                self._limited_command +
                (1 << (self.dac_width - 1))
            )

        with m.Else():
            m.d.comb += self._formatted_code.eq(
                self._limited_command
            )



        # ------------------------------------------------------
        # Sequential output register
        # ------------------------------------------------------
        with m.If(self.i_fault_active | (~self.i_output_enable)):
            # Safety output.
            m.d.sync += [
                self.o_dac.eq(self.i_safe_code),
                self.o_current.eq(self.i_safe_code),
                self._held_output.eq(self.i_safe_code)
            ]

        with m.Elif(self.i_hold):
            # Preserve last driven safe value.
            m.d.sync += [
                self.o_dac.eq(self._held_output),
                self.o_current.eq(self._held_output)
            ]

        with m.Else():
            m.d.sync += [
                self.o_dac.eq(self._formatted_code),
                self.o_current.eq(self._formatted_code),
                self._held_output.eq(self._formatted_code)
            ]
        return m