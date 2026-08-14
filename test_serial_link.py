class _FakePortInfo:
    def __init__(self, device):
        self.device = device


def test_find_serial_port_returns_none_when_no_ports(monkeypatch):
    import serial_link
    monkeypatch.setattr(serial_link.serial.tools.list_ports, "comports", lambda: [])
    assert serial_link.find_serial_port() is None


def test_find_serial_port_returns_first_device(monkeypatch):
    import serial_link
    fake_ports = [_FakePortInfo("COM7"), _FakePortInfo("COM8")]
    monkeypatch.setattr(serial_link.serial.tools.list_ports, "comports", lambda: fake_ports)
    assert serial_link.find_serial_port() == "COM7"


def test_wait_and_open_retries_until_port_found(monkeypatch):
    import serial_link

    calls = {"n": 0}

    def fake_find():
        calls["n"] += 1
        return None if calls["n"] < 3 else "COM9"

    monkeypatch.setattr(serial_link, "find_serial_port", fake_find)
    monkeypatch.setattr(serial_link.time, "sleep", lambda s: None)

    opened = {}

    def fake_serial(port, baudrate, timeout):
        opened["port"] = port
        opened["baudrate"] = baudrate
        return "FAKE_SERIAL_OBJECT"

    monkeypatch.setattr(serial_link.serial, "Serial", fake_serial)

    result = serial_link.wait_and_open(poll_seconds=0)
    assert result == "FAKE_SERIAL_OBJECT"
    assert opened["port"] == "COM9"
    assert opened["baudrate"] == serial_link.BAUDRATE
    assert calls["n"] == 3
