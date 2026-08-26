# Register Map

## Global Registers (0x000 - 0x01C)
*(Existing documentation remains here...)*

## Slow Path Registers (0x100 - 0x124)
*(Existing documentation remains here...)*

## Trace Registers (0x180 - 0x1A0)
*(Existing documentation remains here...)*

## PDH Registers (0x200 - 0x210)

| Address | Name | R/W | Description |
|---------|------|-----|-------------|
| `0x200` | `PDH_CONTROL` | R/W | Bit 0: PDH Enable. 1=Enable PDH Demodulation, 0=Direct ADC. |
| `0x204` | `PDH_MOD_FREQ` | R/W | 32-bit phase increment per clock for the modulation NCO. |
| `0x208` | `PDH_MOD_AMP` | R/W | 16-bit Q2.14 modulation amplitude scaling factor. |
| `0x20C` | `PDH_DEMOD_PHASE` | R/W | 32-bit phase offset applied to the mixer reference. |
| `0x210` | `PDH_LPF_ALPHA` | R/W | 5-bit shift value for the IIR low-pass filter (controls bandwidth). |
