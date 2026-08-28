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

        # Safe output, in SIGNED CONTROLLER units.
        #
        # AUDIT FIX: this used to be an unsigned value "already in DAC
        # code space". That made its meaning depend on i_offset_binary:
        # a safe code of 0 is mid-scale in two's complement but NEGATIVE
        # FULL SCALE in offset binary, so the same register value parked
        # the actuator in two completely different places depending on a
        # separate configuration bit. Since the register defaults to 0,
        # switching the DAC to offset binary would silently turn the
        # fault behaviour into "slam to one rail".
        #
        # Keeping the safe value in controller units and running it
        # through the same clamp and encode path as the normal command
        # makes it encoding-independent: 0 always means mid-scale.
        self.i_safe_code = Signal(signed(controller_width))

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

        # AUDIT FIX: the fault and output-disable branches used to select
        # 0 here and then the sequential block below separately drove
        # i_safe_code, so this combinational path was dead in exactly the
        # cases that matter. Select the safe value itself, so the clamp
        # and encode path below applies to it too.
        with m.If(self.i_fault_active):
            m.d.comb += selected_command.eq(self.i_safe_code)

        with m.Elif(~self.i_output_enable):
            m.d.comb += selected_command.eq(self.i_safe_code)

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

        # AUDIT FIX (S2-9): the previous version assigned the signed
        # controller-space command straight into an UNSIGNED dac_width
        # signal with no converter-range clamp. i_min_code/i_max_code
        # are signed(controller_width) ACTUATOR limits, so software can
        # legally configure a range far wider than the DAC can express,
        # and the excess wrapped silently: a negative overflow became a
        # large positive code and drove the actuator to the opposite
        # rail.
        #
        # Clamp to the physical converter range, in the signed domain,
        # BEFORE encoding. The actuator clamp above and this converter
        # clamp are deliberately separate: the first enforces the
        # operator's configured limits, the second enforces what the
        # hardware can physically represent.
        dac_max_signed = (1 << (self.dac_width - 1)) - 1
        dac_min_signed = -(1 << (self.dac_width - 1))

        dac_clamped = Signal(signed(self.dac_width))

        with m.If(self._limited_command > dac_max_signed):
            m.d.comb += dac_clamped.eq(dac_max_signed)
        with m.Elif(self._limited_command < dac_min_signed):
            m.d.comb += dac_clamped.eq(dac_min_signed)
        with m.Else():
            m.d.comb += dac_clamped.eq(self._limited_command)

        with m.If(self.i_offset_binary):
            # dac_clamped + 2^(W-1) lies in [0, 2^W - 1] by construction.
            m.d.comb += self._formatted_code.eq(
                dac_clamped +
                (1 << (self.dac_width - 1))
            )

        with m.Else():
            m.d.comb += self._formatted_code.eq(
                dac_clamped
            )



        # ------------------------------------------------------
        # Sequential output register
        # ------------------------------------------------------
        with m.If(self.i_fault_active | (~self.i_output_enable)):
            # Safety output. _formatted_code already carries the encoded,
            # clamped safe value because selected_command selected it
            # above, so the safe path and the normal path share one
            # encoder and cannot disagree about the DAC format.
            m.d.sync += [
                self.o_dac.eq(self._formatted_code),
                self.o_current.eq(self._formatted_code),
                self._held_output.eq(self._formatted_code)
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