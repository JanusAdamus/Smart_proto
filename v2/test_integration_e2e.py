import json
import os
import socket
import threading
import time
import urllib.request

import pytest

import serial

from dsmr import MeterState, generate_telegram
from relay import RelayServer, serial_reader_loop
from server import MeterLink, make_server
from store import Store


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar(condicion, plazo=15.0):
    limite = time.time() + plazo
    while time.time() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


def comprobar_cadena(link, base):
    """Las aserciones comunes a las dos variantes de la prueba."""
    assert esperar(lambda: link.snapshot()["received"] >= 3), "no llegaron telegramas"

    with urllib.request.urlopen(base + "/api/latest", timeout=5) as r:
        ultimo = json.loads(r.read())
    assert ultimo["connected"] is True
    assert ultimo["rejected"] == 0, "hubo telegramas con CRC invalido"
    assert ultimo["values"]["power_in"] is not None
    assert ultimo["values"]["voltage_l3"] is not None
    assert ultimo["values"]["gas"] is not None
    assert ultimo["telegram"].startswith("/")

    with urllib.request.urlopen(base + "/api/history?minutes=60", timeout=5) as r:
        historia = json.loads(r.read())
    assert historia, "nada llego a SQLite"
    assert historia[-1]["power_in"] is not None


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="requiere pty (POSIX)")
def test_del_simulador_al_dashboard_sin_hardware(tmp_path):
    """La cadena completa con un pty haciendo de cable serie.

    Simulador -> pty -> relay -> TCP -> parser -> SQLite -> HTTP.
    """
    maestro, esclavo = os.openpty()
    lectura_relay = serial.Serial(os.ttyname(esclavo), timeout=1)

    relay = RelayServer(port=puerto_libre())
    relay.start()
    threading.Thread(
        target=serial_reader_loop,
        args=(relay,),
        kwargs={"serial_factory": lambda: lectura_relay},
        daemon=True,
    ).start()

    link = MeterLink(
        Store(tmp_path / "readings.db"), host="127.0.0.1", port=relay.port
    )
    link.start()
    httpd = make_server(link, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    detener = threading.Event()

    def simulador():
        estado = MeterState()
        while not detener.is_set():
            estado.tick(1.0)
            os.write(maestro, generate_telegram(estado))
            time.sleep(0.2)

    threading.Thread(target=simulador, daemon=True).start()

    try:
        comprobar_cadena(link, base)
    finally:
        detener.set()
        httpd.shutdown()
        httpd.server_close()
        link.stop()
        link.store.close()
        relay.stop()
        lectura_relay.close()
        os.close(maestro)
        os.close(esclavo)


class SerieFalsa:
    """Puerto serie de mentira que va escupiendo telegramas.

    Existe porque Windows no tiene pty y la prueba de arriba se salta entera
    ahi. Una prueba que solo se salta no prueba nada, y el unico eslabon que
    esta variante no cubre es pyserial leyendo bytes de un dispositivo, que no
    es codigo nuestro.
    """

    port = "fake-p1"

    def __init__(self, intervalo=0.2):
        self.intervalo = intervalo
        self.estado = MeterState()
        self.siguiente = 0.0
        self.in_waiting = 0
        self.cerrado = False

    def read(self, _n):
        espera = self.siguiente - time.time()
        if espera > 0:
            time.sleep(espera)
        if self.cerrado:
            raise OSError("puerto cerrado")
        self.siguiente = time.time() + self.intervalo
        self.estado.tick(1.0)
        return generate_telegram(self.estado)

    def close(self):
        self.cerrado = True


def test_del_simulador_al_dashboard_sin_pty(tmp_path):
    """Misma cadena que arriba pero sin pty: corre en cualquier plataforma.

    Simulador -> relay -> TCP -> parser -> SQLite -> HTTP.
    """
    serie = SerieFalsa()
    relay = RelayServer(port=puerto_libre())
    relay.start()
    threading.Thread(
        target=serial_reader_loop,
        args=(relay,),
        kwargs={"serial_factory": lambda: serie},
        daemon=True,
    ).start()

    link = MeterLink(
        Store(tmp_path / "readings.db"), host="127.0.0.1", port=relay.port
    )
    link.start()
    httpd = make_server(link, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    try:
        comprobar_cadena(link, base)
    finally:
        httpd.shutdown()
        httpd.server_close()
        link.stop()
        link.store.close()
        relay.stop()
        serie.close()
