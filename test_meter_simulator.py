import time

import serial

import meter_simulator
from meter_simulator import MeterSerialWriter
from dsmr import TelegramReader, parse_telegram


class _FakeSerial:
    """Puerto USB tal como se comporta en Windows: el write() sigue andando
    aunque lo desenchufes, porque los bytes se van al buffer del driver."""

    def __init__(self, port):
        self.port = port

    def write(self, data):
        return len(data)

    def close(self):
        pass


def test_writer_switches_port_when_unplugged_and_moved(monkeypatch):
    present = ["COM3"]
    monkeypatch.setattr(meter_simulator, "port_still_present", lambda p: p in present)
    monkeypatch.setattr(meter_simulator.time, "sleep", lambda s: None)

    writer = MeterSerialWriter(serial_factory=lambda: _FakeSerial(present[0]))
    writer._tick()
    writer._tick()
    assert writer.sent_count == 2

    present[0] = "COM5"  # el usuario lo pasa al otro puerto USB
    writer._tick()
    assert writer.ser is None, "no solto el puerto desenchufado"
    writer._tick()
    assert writer.ser.port == "COM5"


def test_writer_does_not_drop_a_virtual_port(monkeypatch):
    # loop:// nunca figura en comports(); vigilarlo lo tiraria en bucle.
    monkeypatch.setattr(meter_simulator, "port_still_present", lambda p: False)
    monkeypatch.setattr(meter_simulator.time, "sleep", lambda s: None)

    writer = MeterSerialWriter(serial_factory=lambda: _FakeSerial("loop://"))
    writer._tick()
    writer._tick()
    assert writer.sent_count == 2


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
