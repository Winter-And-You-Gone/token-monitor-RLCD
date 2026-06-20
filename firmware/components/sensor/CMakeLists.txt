idf_component_register(
    SRCS "shtc3.c" "battery.c"
    INCLUDE_DIRS "./"
    PRIV_REQUIRES esp_driver_i2c esp_adc esp_driver_usb_serial_jtag
)

target_link_libraries(${COMPONENT_LIB} INTERFACE "-u usb_serial_jtag_connection_monitor_include")
