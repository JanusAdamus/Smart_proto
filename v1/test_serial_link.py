import serial

import serial_link


class _FakePortInfo:
    def __init__(self, device, vid=0x067B):
        self.device = device
        self.vid = vid


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

    def fake_serial(device, baudrate, timeout, write_timeout=None):
        if device not in ports:
            raise OSError(f"no such port {device}")
        ser = _FakeSerial(device, ports[device])
        opened.append(ser)
        return ser

    monkeypatch.setattr(serial_link.serial, "Serial", fake_serial)
    return opened


def test_candidate_devices_prefers_usb_over_bluetooth(monkeypatch):
    # Los COM de Bluetooth abren bien y se tragan cualquier escritura sin
    # quejarse: si se prueban primero, el generador se queda pegado ahi.
    monkeypatch.delenv("SMARTMETER_PORT", raising=False)
    monkeypatch.setattr(
        serial_link.serial.tools.list_ports,
        "comports",
        lambda: [_FakePortInfo("COM3", vid=None), _FakePortInfo("COM7")],
    )
    assert serial_link.candidate_devices(prefer_usb=True) == ["COM7"]
    # El receptor los prueba todos: en la Pi el medidor cuelga del UART interno,
    # que no tiene vid y con prefer_usb quedaria descartado.
    assert serial_link.candidate_devices() == ["COM3", "COM7"]


def test_candidate_devices_falls_back_when_nothing_has_a_vid(monkeypatch):
    monkeypatch.delenv("SMARTMETER_PORT", raising=False)
    monkeypatch.setattr(
        serial_link.serial.tools.list_ports,
        "comports",
        lambda: [_FakePortInfo("/dev/ttyS0", vid=None)],
    )
    assert serial_link.candidate_devices(prefer_usb=True) == ["/dev/ttyS0"]


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


def test_open_all_ports_does_not_probe(monkeypatch):
    # El generador escribe: si esperara datos entrantes no arrancaria nunca.
    import serial_link
    _fake_bus(monkeypatch, {"/dev/ttyS0": b""})
    assert [s.port for s in serial_link.open_all_ports(poll_seconds=0)] == ["/dev/ttyS0"]


def test_open_all_ports_opens_every_adapter(monkeypatch):
    # El motivo del cambio: no hay forma de saber cual de los dos es el cable
    # al medidor, asi que se escribe en los dos y el receptor filtra.
    import serial_link
    _fake_bus(monkeypatch, {"COM3": b"", "COM7": b""})
    assert [s.port for s in serial_link.open_all_ports(poll_seconds=0)] == ["COM3", "COM7"]


def test_open_all_ports_keeps_the_ports_that_do_open(monkeypatch):
    # Un adaptador tomado por otro programa no puede tapar al que si sirve.
    import serial_link
    _fake_bus(monkeypatch, {"COM7": b""})
    monkeypatch.setattr(
        serial_link.serial.tools.list_ports,
        "comports",
        lambda: [_FakePortInfo("COM3"), _FakePortInfo("COM7")],
    )
    lines = []
    ports = serial_link.open_all_ports(poll_seconds=0, on_log=lines.append)
    assert [s.port for s in ports] == ["COM7"]
    assert any("COM3" in line for line in lines)


def test_open_all_ports_reports_a_port_that_will_not_open(monkeypatch):
    # El caso PL2303 clonado: el driver lo enumera pero rechaza abrirlo. Sin
    # este aviso el generador se queda en "Searching..." sin decir por que.
    import serial_link
    _fake_bus(monkeypatch, {})  # comports vacio -> Serial() falla para todo
    monkeypatch.setattr(
        serial_link.serial.tools.list_ports,
        "comports",
        lambda: [_FakePortInfo("COM4")],
    )
    lines = []

    def stop_after_one_round(_seconds):
        raise KeyboardInterrupt  # corta el reintento infinito

    monkeypatch.setattr(serial_link.time, "sleep", stop_after_one_round)
    try:
        serial_link.open_all_ports(poll_seconds=0, on_log=lines.append)
    except KeyboardInterrupt:
        pass
    assert any("COM4" in line for line in lines)


def test_carries_telegrams_true_on_real_loopback():
    ser = serial.serial_for_url("loop://", timeout=0.1)
    ser.write(b"/XMX5LGBBFG10\r\n\r\n1-0:1.7.0(00.512*kW)\r\n!A1B2\r\n")
    assert serial_link.carries_telegrams(ser, probe_seconds=1.0)
    ser.close()
