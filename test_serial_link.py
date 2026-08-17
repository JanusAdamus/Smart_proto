import serial

import serial_link


class _FakePortInfo:
    def __init__(self, device):
        self.device = device


class _FakeSerial:
    """Puerto de mentira que devuelve `payload` en el primer read y nada despues."""

    def __init__(self, device, payload=b""):
        self.port = device
        self.timeout = 0.5
        self.closed = False
        self._payload = payload

    def read(self, n):
        chunk, self._payload = self._payload[:n], self._payload[n:]
        return chunk

    def close(self):
        self.closed = True


def _fake_bus(monkeypatch, ports: dict):
    """ports: {device: payload}. Instala comports() y serial.Serial falsos."""
    import serial_link
    monkeypatch.delenv("SMARTMETER_PORT", raising=False)
    monkeypatch.setattr(serial_link.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        serial_link.serial.tools.list_ports,
        "comports",
        lambda: [_FakePortInfo(d) for d in ports],
    )
    opened = []

    def fake_serial(device, baudrate, timeout):
        if device not in ports:
            raise OSError(f"no such port {device}")
        ser = _FakeSerial(device, ports[device])
        opened.append(ser)
        return ser

    monkeypatch.setattr(serial_link.serial, "Serial", fake_serial)
    return opened


def test_open_meter_port_skips_silent_ports(monkeypatch):
    # El caso real en la Pi: ttyAMA0 existe y esta mudo, el medidor esta en otro.
    import serial_link
    opened = _fake_bus(monkeypatch, {"/dev/ttyAMA0": b"", "/dev/ttyS0": b"/XMX5 telegram"})
    ser = serial_link.open_meter_port(probe_seconds=0.01)
    assert ser.port == "/dev/ttyS0"
    assert opened[0].closed


def test_open_meter_port_returns_none_when_nothing_talks(monkeypatch):
    import serial_link
    _fake_bus(monkeypatch, {"/dev/ttyAMA0": b"", "/dev/ttyS0": b"ruido sin telegrama"})
    assert serial_link.open_meter_port(probe_seconds=0.01) is None


def test_open_meter_port_returns_none_when_no_ports(monkeypatch):
    import serial_link
    _fake_bus(monkeypatch, {})
    assert serial_link.open_meter_port(probe_seconds=0.01) is None


def test_env_override_skips_the_probe(monkeypatch):
    import serial_link
    _fake_bus(monkeypatch, {"/dev/ttyUSB9": b""})
    monkeypatch.setenv("SMARTMETER_PORT", "/dev/ttyUSB9")
    ser = serial_link.open_meter_port(probe_seconds=0.01)
    assert ser.port == "/dev/ttyUSB9"


def test_wait_and_open_retries_until_a_port_talks(monkeypatch):
    import serial_link
    monkeypatch.setattr(serial_link.time, "sleep", lambda s: None)

    calls = {"n": 0}

    def fake_open(baudrate, probe_seconds):
        calls["n"] += 1
        return "FAKE_SERIAL" if calls["n"] == 3 else None

    monkeypatch.setattr(serial_link, "open_meter_port", fake_open)
    assert serial_link.wait_and_open(poll_seconds=0) == "FAKE_SERIAL"
    assert calls["n"] == 3


def test_open_first_port_does_not_probe(monkeypatch):
    # El generador escribe: si esperara datos entrantes no arrancaria nunca.
    import serial_link
    _fake_bus(monkeypatch, {"/dev/ttyS0": b""})
    assert serial_link.open_first_port(poll_seconds=0).port == "/dev/ttyS0"


def test_carries_telegrams_true_on_real_loopback():
    ser = serial.serial_for_url("loop://", timeout=0.1)
    ser.write(b"/XMX5LGBBFG10\r\n\r\n1-0:1.7.0(00.512*kW)\r\n!A1B2\r\n")
    assert serial_link.carries_telegrams(ser, probe_seconds=1.0)
    ser.close()
