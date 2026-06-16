## POSM FPGA Laser-Lock Core
## Basys 3 (XC7A35T) Constraint File
## For use at M4 dev-board demo stage
## All non-essential pins are commented out
## Do NOT use this for lock_core_top synthesis until M4

## -------------------------------------------------------
## Clock — 100 MHz system clock
## -------------------------------------------------------
set_property PACKAGE_PIN W5 [get_ports clk]
    set_property IOSTANDARD LVCMOS33 [get_ports clk]
    create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk]

## -------------------------------------------------------
## Buttons — control inputs
## -------------------------------------------------------

## Center button — lock enable / arm command
set_property PACKAGE_PIN U18 [get_ports lock_enable]
    set_property IOSTANDARD LVCMOS33 [get_ports lock_enable]

## Top button — reset / fault clear
set_property PACKAGE_PIN T18 [get_ports rst]
    set_property IOSTANDARD LVCMOS33 [get_ports rst]

## Right button — hold command
set_property PACKAGE_PIN W19 [get_ports hold_cmd]
    set_property IOSTANDARD LVCMOS33 [get_ports hold_cmd]

## -------------------------------------------------------
## Switches — mode select and debug
## -------------------------------------------------------

## SW0 — scan enable
set_property PACKAGE_PIN V17 [get_ports scan_enable]
    set_property IOSTANDARD LVCMOS33 [get_ports scan_enable]

## SW1 — invert error polarity
set_property PACKAGE_PIN V16 [get_ports invert_error]
    set_property IOSTANDARD LVCMOS33 [get_ports invert_error]

## SW2 — integrator reset
set_property PACKAGE_PIN W16 [get_ports integrator_reset]
    set_property IOSTANDARD LVCMOS33 [get_ports integrator_reset]

## SW3 — fault clear
set_property PACKAGE_PIN W17 [get_ports fault_clear]
    set_property IOSTANDARD LVCMOS33 [get_ports fault_clear]

## SW4-SW7 — reserved for future debug/mode use
#set_property PACKAGE_PIN W15 [get_ports {sw[4]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {sw[4]}]
#set_property PACKAGE_PIN V15 [get_ports {sw[5]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {sw[5]}]
#set_property PACKAGE_PIN W14 [get_ports {sw[6]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {sw[6]}]
#set_property PACKAGE_PIN W13 [get_ports {sw[7]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {sw[7]}]

## -------------------------------------------------------
## LEDs — status and debug outputs
## -------------------------------------------------------

## LED0 — locked flag
set_property PACKAGE_PIN U16 [get_ports locked]
    set_property IOSTANDARD LVCMOS33 [get_ports locked]

## LED1 — saturation high flag
set_property PACKAGE_PIN E19 [get_ports sat_hi]
    set_property IOSTANDARD LVCMOS33 [get_ports sat_hi]

## LED2 — saturation low flag
set_property PACKAGE_PIN U19 [get_ports sat_lo]
    set_property IOSTANDARD LVCMOS33 [get_ports sat_lo]

## LED3 — fault flag
set_property PACKAGE_PIN V19 [get_ports fault]
    set_property IOSTANDARD LVCMOS33 [get_ports fault]

## LED4 — scan active
set_property PACKAGE_PIN W18 [get_ports scan_active]
    set_property IOSTANDARD LVCMOS33 [get_ports scan_active]

## LED5 — adc_valid (confirms ADC stream is live)
set_property PACKAGE_PIN U15 [get_ports adc_valid_dbg]
    set_property IOSTANDARD LVCMOS33 [get_ports adc_valid_dbg]

## LED6 — dac_valid (confirms DAC stream is live)
set_property PACKAGE_PIN U14 [get_ports dac_valid_dbg]
    set_property IOSTANDARD LVCMOS33 [get_ports dac_valid_dbg]

## LED7 — heartbeat (toggles every ~0.5s to confirm clock is running)
set_property PACKAGE_PIN V14 [get_ports heartbeat]
    set_property IOSTANDARD LVCMOS33 [get_ports heartbeat]

## LED8-15 — reserved for DAC command MSBs or error signal debug
#set_property PACKAGE_PIN V13 [get_ports {dbg[0]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[0]}]
#set_property PACKAGE_PIN V3 [get_ports {dbg[1]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[1]}]
#set_property PACKAGE_PIN W3 [get_ports {dbg[2]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[2]}]
#set_property PACKAGE_PIN U3 [get_ports {dbg[3]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[3]}]
#set_property PACKAGE_PIN P3 [get_ports {dbg[4]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[4]}]
#set_property PACKAGE_PIN N3 [get_ports {dbg[5]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[5]}]
#set_property PACKAGE_PIN P1 [get_ports {dbg[6]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[6]}]
#set_property PACKAGE_PIN L1 [get_ports {dbg[7]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {dbg[7]}]

## -------------------------------------------------------
## Pmod JA — SPI ADC input (future M4 hardware)
## Uncomment when connecting a PMOD ADC (e.g. PmodAD1)
## -------------------------------------------------------
#set_property PACKAGE_PIN J1 [get_ports adc_spi_cs]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_spi_cs]
#set_property PACKAGE_PIN L2 [get_ports adc_spi_miso]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_spi_miso]
#set_property PACKAGE_PIN J2 [get_ports adc_spi_sck]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_spi_sck]

## -------------------------------------------------------
## Pmod JB — SPI DAC output (future M4 hardware)
## Uncomment when connecting a PMOD DAC (e.g. PmodDA2)
## -------------------------------------------------------
#set_property PACKAGE_PIN A14 [get_ports dac_spi_cs]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_spi_cs]
#set_property PACKAGE_PIN A16 [get_ports dac_spi_mosi]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_spi_mosi]
#set_property PACKAGE_PIN B15 [get_ports dac_spi_sck]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_spi_sck]

## -------------------------------------------------------
## 7-segment display — reserved for future error readout
## -------------------------------------------------------
#set_property PACKAGE_PIN W7 [get_ports {seg[0]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {seg[0]}]
#set_property PACKAGE_PIN W6 [get_ports {seg[1]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {seg[1]}]
#set_property PACKAGE_PIN U8 [get_ports {seg[2]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {seg[2]}]
#set_property PACKAGE_PIN V8 [get_ports {seg[3]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {seg[3]}]
#set_property PACKAGE_PIN U5 [get_ports {seg[4]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {seg[4]}]
#set_property PACKAGE_PIN V5 [get_ports {seg[5]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {seg[5]}]
#set_property PACKAGE_PIN U7 [get_ports {seg[6]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {seg[6]}]
#set_property PACKAGE_PIN U2 [get_ports {an[0]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {an[0]}]
#set_property PACKAGE_PIN U4 [get_ports {an[1]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {an[1]}]
#set_property PACKAGE_PIN V4 [get_ports {an[2]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {an[2]}]
#set_property PACKAGE_PIN W4 [get_ports {an[3]}]
#    set_property IOSTANDARD LVCMOS33 [get_ports {an[3]}]
