import time

import serial

from meter_simulator import MeterSerialWriter
from dsmr import TelegramReader, parse_telegram


def test_meter_serial_writer_sends_valid_telegrams():
    ser = serial.serial_for_url("loop://", timeout=1)
    writer = MeterSerialWriter(serial_factory=lambda: ser)
    writer.start()
    try:
        reader = TelegramReader()
        telegrams = []
        deadline = time.time() + 5.0
        while len(telegrams) < 1 and time.time() < deadline:
            chunk = ser.read(4096)
            telegrams.extend(reader.feed(chunk))
        assert len(telegrams) >= 1
        fields = parse_telegram(telegrams[0])
        assert 0.0 <= fields["kw"] <= 5.0
    finally:
        writer.stop()


def test_meter_serial_writer_tracks_status_and_log():
    ser = serial.serial_for_url("loop://", timeout=1)
    writer = MeterSerialWriter(serial_factory=lambda: ser)
    writer.start()
    try:
        deadline = time.time() + 5.0
        port, count, log = writer.status()
        while count < 1 and time.time() < deadline:
            time.sleep(0.1)
            port, count, log = writer.status()
        assert count >= 1
        assert port == ser.port
        assert log[-1].startswith(f"Sent #{count}:")
    finally:
        writer.stop()
