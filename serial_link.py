import time

import serial
import serial.tools.list_ports

BAUDRATE = 115200


def find_serial_port():
    ports = serial.tools.list_ports.comports()
    return ports[0].device if ports else None


def wait_and_open(baudrate=BAUDRATE, poll_seconds=2.0, timeout=5.0):
    while True:
        port = find_serial_port()
        if port:
            try:
                return serial.Serial(port, baudrate=baudrate, timeout=timeout)
            except OSError:
                pass
        time.sleep(poll_seconds)
