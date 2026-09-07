"""La eleccion de puerto en la Pi lectora.

Es lo que hace que las tres fuentes se manejen igual: el medidor real por
RJ12-a-USB, la computadora por USB-a-serie y la Pi Zero por UART entran todas
como un tty mas, y gana el que manda telegramas. Sin ramas por tipo de fuente.
"""

import serial_link
from dsmr import MeterState, generate_telegram


class PuertoFalso:
    def __init__(self, device, emite):
        self.port = device
        self.timeout = 0.5
        self._emite = emite
        self.closed = False

    def read(self, _n=1):
        if not self._emite:
            return b""
        return generate_telegram(MeterState())

    def close(self):
        self.closed = True


class DispositivoListado:
    def __init__(self, device, vid=None):
        self.device = device
        self.vid = vid


def _montar(monkeypatch, listados, emisor):
    abiertos = {}

    def comports():
        return listados

    def fabricar(device, baudrate=None, timeout=None, write_timeout=None):
        puerto = PuertoFalso(device, device == emisor)
        abiertos[device] = puerto
        return puerto

    monkeypatch.setattr(serial_link.serial.tools.list_ports, "comports", comports)
    monkeypatch.setattr(serial_link.serial, "Serial", fabricar)
    monkeypatch.delenv("SMARTMETER_PORT", raising=False)
    return abiertos


def test_elige_el_puerto_que_emite_telegramas_y_no_el_primero(monkeypatch):
    """El orden de la lista no dice nada: gana el que manda un '/'."""
    listados = [
        DispositivoListado("/dev/ttyAMA0"),
        DispositivoListado("/dev/ttyS0"),
        DispositivoListado("/dev/ttyUSB0", vid=0x0403),
    ]
    abiertos = _montar(monkeypatch, listados, emisor="/dev/ttyUSB0")
    elegido = serial_link.open_meter_port(probe_seconds=0.2)
    assert elegido.port == "/dev/ttyUSB0"
    # Los mudos se cierran; dejarlos abiertos bloquearia el puerto para el
    # proximo intento si el cable se mueve de sitio.
    assert abiertos["/dev/ttyAMA0"].closed
    assert abiertos["/dev/ttyS0"].closed


def test_el_uart_de_los_gpio_gana_igual_que_un_usb(monkeypatch):
    """La Pi Zero por UART entra por el mismo camino que un adaptador USB.

    Es el caso que importa para el Plan B-UART: /dev/ttyAMA0 no tiene vid ni
    pid, asi que cualquier filtro por USB lo habria descartado.
    """
    listados = [
        DispositivoListado("/dev/ttyUSB0", vid=0x0403),
        DispositivoListado("/dev/ttyAMA0"),
    ]
    _montar(monkeypatch, listados, emisor="/dev/ttyAMA0")
    assert serial_link.open_meter_port(probe_seconds=0.2).port == "/dev/ttyAMA0"


def test_ningun_puerto_emite(monkeypatch):
    listados = [DispositivoListado("/dev/ttyS0")]
    _montar(monkeypatch, listados, emisor=None)
    assert serial_link.open_meter_port(probe_seconds=0.2) is None


def test_smartmeter_port_saltea_el_sondeo(monkeypatch):
    """Escotilla de emergencia: se abre ese y no se prueba nada mas.

    Hace falta cuando la fuente arranca despues que la lectora y todavia no
    manda nada que sondear.
    """
    _montar(monkeypatch, [], emisor=None)
    monkeypatch.setenv("SMARTMETER_PORT", "/dev/serial0")
    elegido = serial_link.open_meter_port(probe_seconds=0.2)
    assert elegido is not None and elegido.port == "/dev/serial0"
