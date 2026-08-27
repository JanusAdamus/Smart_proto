import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest

import server
from dsmr import MeterState, generate_telegram
from server import DashboardHandler, MeterLink, extract, make_server
from store import Store


@pytest.fixture
def link(tmp_path):
    enlace = MeterLink(Store(tmp_path / "readings.db"), host="127.0.0.1", port=1)
    yield enlace
    enlace.stop()
    enlace.store.close()


@pytest.fixture
def servidor(link):
    httpd = make_server(link, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def pedir(base, ruta):
    with urllib.request.urlopen(base + ruta, timeout=5) as respuesta:
        return respuesta.status, respuesta.read()


def esperar(condicion, timeout=2.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.01)
    return condicion()


def test_extract_mapea_los_codigos_obis_a_columnas():
    from dsmr import parse_telegram
    objetos = parse_telegram(generate_telegram(MeterState()))["objects"]
    valores = extract(objetos)
    assert valores["power_in"] is not None
    assert valores["voltage_l1"] is not None
    assert valores["current_l3"] is not None
    assert valores["energy_in_t2"] is not None


def test_extract_encuentra_el_gas_en_cualquier_canal_mbus():
    """El canal del gas depende del orden de instalacion de los dispositivos
    M-Bus (7.3 del estandar): puede ser 1, 2, 3 o 4. Fijarlo en 1 haria que
    un medidor perfectamente normal apareciera sin gas."""
    assert extract({"0-3:24.2.1": ["101209112500W", "999.500*m3"]})["gas"] == 999.5


def test_extract_omite_lo_que_el_medidor_no_informa():
    valores = extract({"1-0:1.7.0": ["01.000*kW"]})
    assert valores == {"power_in": 1.0}


def test_consume_guarda_y_actualiza_el_estado(link):
    telegrama = generate_telegram(MeterState())
    link.consume(telegrama)
    estado = link.snapshot()
    assert estado["received"] == 1
    assert estado["rejected"] == 0
    assert estado["values"]["power_in"] is not None
    assert estado["telegram"].startswith("/")
    assert link.store.history(minutes=60, now=estado["ts"])


def test_consume_reensambla_telegramas_partidos(link):
    telegrama = generate_telegram(MeterState())
    for inicio in range(0, len(telegrama), 13):
        link.consume(telegrama[inicio:inicio + 13])
    assert link.snapshot()["received"] == 1


def test_consume_cuenta_los_crc_invalidos_sin_guardarlos(link):
    """Con un medidor real, este contador subiendo es la primera senal de un
    cable con ruido o demasiado largo."""
    telegrama = generate_telegram(MeterState())
    link.consume(telegrama[:-6] + b"0000\r\n")
    estado = link.snapshot()
    assert estado["rejected"] == 1
    assert estado["received"] == 0
    assert link.store.history(minutes=60) == []


def test_consume_ignora_un_telegrama_sin_marca_de_tiempo(link):
    """Sin marca de tiempo no hay clave primaria. Se refleja en vivo pero no
    se persiste, en vez de inventar una hora."""
    from dsmr import crc16_arc
    cuerpo = "/ISK5\r\n\r\n1-0:1.7.0(01.000*kW)\r\n"
    crc = crc16_arc((cuerpo + "!").encode("ascii"))
    link.consume((cuerpo + f"!{crc:04X}\r\n").encode("ascii"))
    estado = link.snapshot()
    assert estado["received"] == 1
    assert estado["values"]["power_in"] == 1.0
    assert link.store.history(minutes=60) == []


def test_snapshot_arranca_desconectado(link):
    estado = link.snapshot()
    assert estado["connected"] is False
    assert estado["values"] == {}
    assert estado["telegram"] == ""


def test_start_consume_por_tcp_y_stop_desconecta(link):
    """stop debe liberar el socket aunque recv este esperando otros bytes."""
    with socket.socket() as lectora:
        lectora.bind(("127.0.0.1", 0))
        lectora.listen()
        lectora.settimeout(2)
        link.port = lectora.getsockname()[1]
        link.start()
        conexion, _ = lectora.accept()
        try:
            assert esperar(lambda: link.snapshot()["connected"])
            conexion.sendall(generate_telegram(MeterState()))
            assert esperar(lambda: link.snapshot()["received"] == 1)
            time.sleep(0.05)
            link.stop()
            assert esperar(lambda: not link.snapshot()["connected"])
        finally:
            conexion.close()


def test_reconexion_descarta_el_fragmento_de_la_conexion_anterior(link):
    telegrama = generate_telegram(MeterState())
    with socket.socket() as lectora:
        lectora.bind(("127.0.0.1", 0))
        lectora.listen()
        lectora.settimeout(2)
        link.port = lectora.getsockname()[1]
        link.start()

        primera, _ = lectora.accept()
        primera.sendall(telegrama[:len(telegrama) // 2])
        primera.close()

        segunda, _ = lectora.accept()
        try:
            segunda.sendall(telegrama)
            assert esperar(
                lambda: link.snapshot()["received"] + link.snapshot()["rejected"] == 1
            )
            estado = link.snapshot()
            assert estado["received"] == 1
            assert estado["rejected"] == 0
        finally:
            segunda.close()


def test_latest_devuelve_json_con_el_estado(servidor, link):
    link.consume(generate_telegram(MeterState()))
    estado, cuerpo = pedir(servidor, "/api/latest")
    assert estado == 200
    datos = json.loads(cuerpo)
    assert datos["received"] == 1
    assert datos["values"]["power_in"] is not None
    assert datos["telegram"].startswith("/")


def test_latest_funciona_con_el_enlace_caido(servidor):
    """El dashboard tiene que poder decir 'sin enlace' en vez de no cargar."""
    estado, cuerpo = pedir(servidor, "/api/latest")
    assert estado == 200
    datos = json.loads(cuerpo)
    assert datos["connected"] is False
    assert datos["values"] == {}


def test_history_devuelve_las_lecturas_guardadas(servidor, link):
    link.consume(generate_telegram(MeterState()))
    estado, cuerpo = pedir(servidor, "/api/history?minutes=60")
    assert estado == 200
    filas = json.loads(cuerpo)
    assert len(filas) == 1
    assert filas[0]["power_in"] is not None


def test_history_acepta_minutes_invalido_sin_reventar(servidor):
    estado, cuerpo = pedir(servidor, "/api/history?minutes=no-es-un-numero")
    assert estado == 200
    assert json.loads(cuerpo) == []


def test_history_acota_minutes_entre_un_minuto_y_siete_dias(
        servidor, link, monkeypatch):
    consultas = []

    def history(minutes):
        consultas.append(minutes)
        return []

    monkeypatch.setattr(link.store, "history", history)
    pedir(servidor, "/api/history?minutes=0")
    pedir(servidor, "/api/history?minutes=99999")
    assert consultas == [1, 60 * 24 * 7]


@pytest.mark.parametrize("ruta", ["/api/latest", "/api/history"])
def test_json_no_se_cachea(servidor, ruta):
    with urllib.request.urlopen(servidor + ruta, timeout=5) as respuesta:
        assert respuesta.headers["Cache-Control"] == "no-store"


def test_raiz_sirve_el_dashboard(servidor):
    estado, cuerpo = pedir(servidor, "/")
    assert estado == 200
    assert b"<html" in cuerpo.lower()


def test_ruta_desconocida_da_404(servidor):
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as error:
        pedir(servidor, "/no-existe")
    assert error.value.code == 404


@pytest.mark.parametrize("error", [ConnectionResetError, BrokenPipeError])
def test_handler_ignora_si_el_cliente_se_desconecta(monkeypatch, error):
    def desconectar(_handler):
        raise error

    monkeypatch.setattr(BaseHTTPRequestHandler, "handle", desconectar)
    DashboardHandler.handle(object.__new__(DashboardHandler))


def test_handler_no_oculta_otros_errores(monkeypatch):
    def fallar(_handler):
        raise RuntimeError("fallo interno")

    monkeypatch.setattr(BaseHTTPRequestHandler, "handle", fallar)
    with pytest.raises(RuntimeError, match="fallo interno"):
        DashboardHandler.handle(object.__new__(DashboardHandler))


@pytest.mark.parametrize("error", [None, KeyboardInterrupt])
def test_main_cierra_todos_los_recursos(monkeypatch, tmp_path, error):
    eventos = []

    class StoreDoble:
        def __init__(self, path):
            eventos.append(("store", path))

        def close(self):
            eventos.append("store.close")

    class LinkDoble:
        def __init__(self, store):
            self.store = store
            eventos.append("link")

        def start(self):
            eventos.append("link.start")

        def stop(self):
            eventos.append("link.stop")

    class ServidorDoble:
        def serve_forever(self):
            eventos.append("serve_forever")
            if error is not None:
                raise error

        def server_close(self):
            eventos.append("server_close")

    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "readings.db"))
    monkeypatch.setattr(server, "Store", StoreDoble)
    monkeypatch.setattr(server, "MeterLink", LinkDoble)
    monkeypatch.setattr(server, "make_server", lambda link: ServidorDoble())

    if error is None:
        server.main()
    else:
        with pytest.raises(error):
            server.main()

    assert eventos[-3:] == ["server_close", "link.stop", "store.close"]
