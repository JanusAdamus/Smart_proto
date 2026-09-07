import json
import os
import socket
import threading
import time
import urllib.request

from dsmr import TelegramReader, parse_telegram
from p1_source import stream_loop
from relay import RelayServer
from server import MeterLink, make_server
from store import Store

MUESTRA_P1METER_DEV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "p1meter_dev_sample.txt"
)


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


def test_el_simulador_tcp_sirve_telegramas_validos():
    relay = RelayServer(port=puerto_libre())
    relay.start()
    stop = threading.Event()
    threading.Thread(
        target=stream_loop, args=(relay,), kwargs={"interval": 0.2, "stop": stop}, daemon=True
    ).start()
    lector = TelegramReader()
    telegramas = []
    try:
        conexion = socket.create_connection(("127.0.0.1", relay.port), timeout=10)
        conexion.settimeout(10)
        while len(telegramas) < 2:
            trozo = conexion.recv(4096)
            assert trozo, "el simulador cerro la conexion"
            telegramas += lector.feed(trozo)
        conexion.close()
    finally:
        stop.set()
        relay.stop()
    # Si el parser los acepta, el simulador emite lo mismo que esperamos leer
    # de un medidor real.
    assert parse_telegram(telegramas[0])["objects"]["1-0:1.7.0"]


def test_el_simulador_gotea_el_telegrama_en_vez_de_mandarlo_de_golpe():
    """Un P1 real entrega a 115200 baudios; el consumidor rearma trozos."""
    relay = RelayServer(port=puerto_libre())
    relay.start()
    stop = threading.Event()
    threading.Thread(
        target=stream_loop, args=(relay,), kwargs={"interval": 0.2, "stop": stop}, daemon=True
    ).start()
    lector = TelegramReader()
    trozos = 0
    try:
        conexion = socket.create_connection(("127.0.0.1", relay.port), timeout=10)
        conexion.settimeout(10)
        while not lector.feed(conexion.recv(4096)):
            trozos += 1
            assert trozos < 500, "el telegrama nunca termino"
        conexion.close()
    finally:
        stop.set()
        relay.stop()
    assert trozos > 1, "llego entero en un solo recv, no hubo goteo"


def test_el_dashboard_consume_un_telegrama_de_p1meter_dev(tmp_path):
    """Telegrama capturado del stream publico de p1meter.dev, en Elixir.

    Sirve de control externo: lo genero otra implementacion, y trae 17 objetos
    en vez de los 37 nuestros. Sin tension por fase ni por corriente, que es lo
    que se ve en un medidor monofasico. Nada de eso puede tumbar la cadena: un
    campo ausente es normal, no un error.
    """
    with open(MUESTRA_P1METER_DEV, "rb") as archivo:
        telegrama = archivo.read()

    link = MeterLink(Store(tmp_path / "readings.db"), host="127.0.0.1", port=puerto_libre())
    httpd = make_server(link, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        link.consume(telegrama)
        with urllib.request.urlopen(base + "/api/latest", timeout=5) as r:
            ultimo = json.loads(r.read())
        assert ultimo["rejected"] == 0
        assert ultimo["values"]["power_in"] == 2.186
        assert ultimo["values"]["energy_in_t1"] == 2603.492
        assert ultimo["values"]["gas"] == 1229.854
        # Este medidor no informa las fases: el campo ni siquiera aparece, y
        # el dashboard tiene que omitir la tarjeta en vez de caerse.
        assert "voltage_l1" not in ultimo["values"]

        with urllib.request.urlopen(base + "/api/history?minutes=525600", timeout=5) as r:
            historia = json.loads(r.read())
        assert historia and historia[-1]["power_in"] == 2.186
        assert historia[-1]["voltage_l1"] is None
    finally:
        httpd.shutdown()
        httpd.server_close()
        link.stop()
        link.store.close()
