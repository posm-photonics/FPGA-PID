## POSM FPGA MTS Laser-Lock Core
## Basys 3 (XC7A35T) Constraint File — updated for v3
## Board: Basys 3
## Target: M4 dev-board demo
## Do NOT use for lock_core_top synthesis until M4

## -------------------------------------------------------
## Clock — 100 MHz system clock
## -------------------------------------------------------
set_property PACKAGE_PIN W5 [get_ports clk]
    set_property IOSTANDARD LVCMOS33 [get_ports clk]
    create_clock -add -name sys_clk_pin -period 10.00 \
        -waveform {0 5} [get_ports clk]

## -------------------------------------------------------
## Buttons — manual control inputs
## -------------------------------------------------------

## Center — lock enable / arm command
set_property PACKAGE_PIN U18 [get_ports lock_enable]
    set_property IOSTANDARD LVCMOS33 [get_ports lock_enable]

## Top — reset / fault clear
set_property PACKAGE_PIN T18 [get_ports rst]
    set_property IOSTANDARD LVCMOS33 [get_ports rst]

## Right — hold command
set_property PACKAGE_PIN W19 [get_ports hold_cmd]
    set_property IOSTANDARD LVCMOS33 [get_ports hold_cmd]

## -------------------------------------------------------
## Switches — mode select and debug
## -------------------------------------------------------

## SW0 — scan enable
set_property PACKAGE_PIN V17 [get_ports scan_enable]
    set_property IOSTANDARD LVCMOS33 [get_ports scan_enable]

## SW1 — error polarity invert (new in v3 — slope sign matters)
set_property PACKAGE_PIN V16 [get_ports invert_error]
    set_property IOSTANDARD LVCMOS33 [get_ports invert_error]

## SW2 — integrator reset
set_property PACKAGE_PIN W16 [get_ports integrator_reset]
    set_property IOSTANDARD LVCMOS33 [get_ports integrator_reset]

## SW3 — fault clear
set_property PACKAGE_PIN W17 [get_ports fault_clear]
    set_property IOSTANDARD LVCMOS33 [get_ports fault_clear]

## SW4 — slow recenter enable (new in v3)
set_property PACKAGE_PIN W15 [get_ports slow_recenter_enable]
    set_property IOSTANDARD LVCMOS33 [get_ports slow_recenter_enable]

## SW5 — autolock enable (new in v3)
set_property PACKAGE_PIN V15 [get_ports autolock_enable]
    set_property IOSTANDARD LVCMOS33 [get_ports autolock_enable]

## SW6-7 reserved
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

## LED1 — fast DAC saturation high (new in v3 — fast path specific)
set_property PACKAGE_PIN E19 [get_ports fast_sat_hi]
    set_property IOSTANDARD LVCMOS33 [get_ports fast_sat_hi]

## LED2 — fast DAC saturation low (new in v3)
set_property PACKAGE_PIN U19 [get_ports fast_sat_lo]
    set_property IOSTANDARD LVCMOS33 [get_ports fast_sat_lo]

## LED3 — fault flag
set_property PACKAGE_PIN V19 [get_ports fault]
    set_property IOSTANDARD LVCMOS33 [get_ports fault]

## LED4 — scan active
set_property PACKAGE_PIN W18 [get_ports scan_active]
    set_property IOSTANDARD LVCMOS33 [get_ports scan_active]

## LED5 — adc_valid (ADC stream live)
set_property PACKAGE_PIN U15 [get_ports adc_valid_dbg]
    set_property IOSTANDARD LVCMOS33 [get_ports adc_valid_dbg]

## LED6 — slow DAC saturation (new in v3 — slow path specific)
set_property PACKAGE_PIN U14 [get_ports slow_sat_dbg]
    set_property IOSTANDARD LVCMOS33 [get_ports slow_sat_dbg]

## LED7 — heartbeat (toggles every ~0.5s confirms clock running)
set_property PACKAGE_PIN V14 [get_ports heartbeat]
    set_property IOSTANDARD LVCMOS33 [get_ports heartbeat]

## LED8-15 reserved for debug signals
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
## Pmod JA — ADC3664 dual channel ADC (new in v3)
## ADC_CH0: demodulated MTS error signal
## ADC_CH1: raw RF monitor signal
## Uncomment when connecting ADC3664 at M4
## -------------------------------------------------------
#set_property PACKAGE_PIN J1 [get_ports adc_ch0_p]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_ch0_p]
#set_property PACKAGE_PIN L2 [get_ports adc_ch0_n]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_ch0_n]
#set_property PACKAGE_PIN J2 [get_ports adc_ch1_p]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_ch1_p]
#set_property PACKAGE_PIN G2 [get_ports adc_ch1_n]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_ch1_n]
#set_property PACKAGE_PIN H1 [get_ports adc_valid]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_valid]
#set_property PACKAGE_PIN K2 [get_ports adc_ch0_overrange]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_ch0_overrange]
#set_property PACKAGE_PIN H2 [get_ports adc_ch1_overrange]
#    set_property IOSTANDARD LVCMOS33 [get_ports adc_ch1_overrange]

## -------------------------------------------------------
## Pmod JB — AD9117 dual DAC (new in v3)
## DAC_FAST: fast correction to CTL200 AC/HF modulation input
## DAC_SLOW: slow scan and recentering to CTL200 DC input
## Uncomment when connecting AD9117 at M4
## -------------------------------------------------------
#set_property PACKAGE_PIN A14 [get_ports dac_fast_spi_cs]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_fast_spi_cs]
#set_property PACKAGE_PIN A16 [get_ports dac_fast_spi_mosi]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_fast_spi_mosi]
#set_property PACKAGE_PIN B15 [get_ports dac_fast_spi_sck]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_fast_spi_sck]
#set_property PACKAGE_PIN B16 [get_ports dac_slow_spi_cs]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_slow_spi_cs]
#set_property PACKAGE_PIN A15 [get_ports dac_slow_spi_mosi]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_slow_spi_mosi]
#set_property PACKAGE_PIN A17 [get_ports dac_slow_spi_sck]
#    set_property IOSTANDARD LVCMOS33 [get_ports dac_slow_spi_sck]

## -------------------------------------------------------
## Pmod JC — AD9959 DDS SPI control (new in v3)
## AD9959 controls EOM drive and mixer LO phase
## Uncomment when connecting AD9959 at M4
## -------------------------------------------------------
#set_property PACKAGE_PIN K17 [get_ports ad9959_spi_cs]
#    set_property IOSTANDARD LVCMOS33 [get_ports ad9959_spi_cs]
#set_property PACKAGE_PIN M18 [get_ports ad9959_spi_mosi]
#    set_property IOSTANDARD LVCMOS33 [get_ports ad9959_spi_mosi]
#set_property PACKAGE_PIN N17 [get_ports ad9959_spi_sck]
#    set_property IOSTANDARD LVCMOS33 [get_ports ad9959_spi_sck]
#set_property PACKAGE_PIN P18 [get_ports ad9959_io_update]
#    set_property IOSTANDARD LVCMOS33 [get_ports ad9959_io_update]
#set_property PACKAGE_PIN L17 [get_ports ad9959_reset]
#    set_property IOSTANDARD LVCMOS33 [get_ports ad9959_reset]

## -------------------------------------------------------
## 7-segment display — reserved for error code readout
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
