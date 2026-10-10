# -*- coding: utf-8 -*-
# Detect the ESP32-S3-RLCD serial port (Espressif USB-Serial/JTAG, VID 0x303A).
# Prints the port name, or nothing if not found.
from serial.tools import list_ports

for p in list_ports.comports():
    if p.vid == 0x303A:
        print(p.device)
