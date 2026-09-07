import time

import pytest

from dsmr import TelegramReader, parse_telegram
from meter_simulator import MeterSerialWriter


class PuertoFalso:
    def __init__(self, nombre="fake0"):
        self.port = nombre
        self.escrito = b""
        self.in_waiting = 0
        self.closed = False

    def write(self, data):
        self.escrito += data

    def close(self):
        self.closed = True


def esperar(condicion, plazo=5.0):
    limite = time.time() + plazo
    while time.time() < limite:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def test_escribe_telegramas_validos_en_el_puerto():
    puerto = PuertoFalso()
    writer = MeterSerialWriter(serial_factory=lambda: [puerto])
    writer.start()
    try:
        assert esperar(lambda: TelegramReader().feed(puerto.escrito))
        telegrama = TelegramReader().feed(puerto.escrito)[0]
        # Si el parser lo acepta, el simulador emite el mismo formato que
        # esperamos leer de un medidor real.
        assert parse_telegram(telegrama)["objects"]["1-3:0.2.8"] == ["50"]
    finally:
        writer.stop()


def test_escribe_en_todos_los_puertos_a_la_vez():
    """El generador no puede sondear: escribe y nadie le contesta, asi que no
    sabe cual adaptador es el cable. Elegir uno seria adivinar."""
    puertos = [PuertoFalso("a"), PuertoFalso("b"), PuertoFalso("c")]
    writer = MeterSerialWriter(serial_factory=lambda: puertos)
    writer.start()
    try:
        assert esperar(lambda: all(p.escrito for p in puertos))
    finally:
        writer.stop()


def test_reading_snapshot_expone_los_campos_del_dashboard():
    writer = MeterSerialWriter(serial_factory=lambda: [PuertoFalso()])
    lectura = writer.reading_snapshot()
    for clave in ("power_in", "energy_in_t2", "voltage_l1", "gas"):
        assert clave in lectura


def test_stop_cierra_los_puertos():
    puerto = PuertoFalso()
    writer = MeterSerialWriter(serial_factory=lambda: [puerto])
    writer.start()
    assert esperar(lambda: puerto.escrito)
    writer.stop()
    assert esperar(lambda: puerto.closed)


def test_se_puede_importar_sin_tkinter(monkeypatch):
    """La Pi Zero headless no trae python3-tk y ahi no hay ventana que abrir."""
    import importlib
    import sys

    for nombre in list(sys.modules):
        if nombre == "tkinter" or nombre.startswith("tkinter."):
            monkeypatch.delitem(sys.modules, nombre, raising=False)
    monkeypatch.setitem(sys.modules, "tkinter", None)
    modulo = importlib.reload(importlib.import_module("meter_simulator"))
    try:
        assert modulo.tk is None
        assert callable(modulo.run_headless)
    finally:
        monkeypatch.undo()
        importlib.reload(modulo)
