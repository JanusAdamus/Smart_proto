"""Pi con pantalla: consume el flujo de la lectora y sirve el dashboard."""

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dsmr import InvalidTelegram, TelegramReader, number, parse_telegram, parse_timestamp
from store import Store

READER_HOST = os.environ.get("SMARTMETER_READER_HOST", "192.168.7.1")
READER_PORT = int(os.environ.get("SMARTMETER_READER_PORT", "4000"))
DB_PATH = os.environ.get("SMARTMETER_DB", "/var/lib/smartmeter/readings.db")
HTTP_PORT = int(os.environ.get("SMARTMETER_HTTP_PORT", "8080"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TIMESTAMP_CODE = "0-0:1.0.0"

# Que codigo OBIS alimenta cada columna. Este mapa es la unica parte del
# sistema que conoce codigos concretos, y vive aqui a proposito: que mostrar
# es decision de la presentacion, no de la lectura.
FIELDS = {
    "power_in": ("1-0:1.7.0", 0),
    "power_out": ("1-0:2.7.0", 0),
    "energy_in_t1": ("1-0:1.8.1", 0),
    "energy_in_t2": ("1-0:1.8.2", 0),
    "energy_out_t1": ("1-0:2.8.1", 0),
    "energy_out_t2": ("1-0:2.8.2", 0),
    "voltage_l1": ("1-0:32.7.0", 0),
    "voltage_l2": ("1-0:52.7.0", 0),
    "voltage_l3": ("1-0:72.7.0", 0),
    "current_l1": ("1-0:31.7.0", 0),
    "current_l2": ("1-0:51.7.0", 0),
    "current_l3": ("1-0:71.7.0", 0),
}

# El canal M-Bus del gas depende del orden de instalacion (7.3 del estandar).
GAS_CHANNELS = (1, 2, 3, 4)


def extract(objects: dict) -> dict:
    """Codigos OBIS a columnas. Solo incluye lo que el medidor informa."""
    values = {}
    for name, (code, index) in FIELDS.items():
        value = number(objects, code, index)
        if value is not None:
            values[name] = value
    for channel in GAS_CHANNELS:
        gas = number(objects, f"0-{channel}:24.2.1", index=1)
        if gas is not None:
            values["gas"] = gas
            break
    return values


class MeterLink:
    """Cliente TCP hacia la Pi lectora, con el estado en vivo del medidor."""

    def __init__(self, store: Store, host=READER_HOST, port=READER_PORT):
        self.store = store
        self.host = host
        self.port = port
        self.running = True
        self._reader = TelegramReader()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._conn = None
        self._connected = False
        self._ts = None
        self._values = {}
        self._telegram = ""
        self.received = 0
        self.rejected = 0

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._prune_loop, daemon=True).start()

    def stop(self):
        self.running = False
        self._stop.set()
        with self._lock:
            conn = self._conn
            self._connected = False
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def consume(self, chunk: bytes) -> None:
        for raw in self._reader.feed(chunk):
            try:
                parsed = parse_telegram(raw)
            except InvalidTelegram:
                with self._lock:
                    self.rejected += 1
                continue
            objects = parsed["objects"]
            values = extract(objects)
            stamps = objects.get(TIMESTAMP_CODE) or []
            ts = parse_timestamp(stamps[0]) if stamps else None
            with self._lock:
                self.received += 1
                self._ts = ts
                self._values = values
                self._telegram = raw.decode("ascii", errors="replace")
            # Sin marca de tiempo no hay clave primaria. Se muestra en vivo
            # igual, pero no se persiste con una hora inventada.
            if ts is not None and values:
                try:
                    self.store.save(ts, values)
                except Exception as error:
                    # Disco lleno o base bloqueada no puede apagar el vivo.
                    print(f"no se pudo guardar la lectura: {error}", flush=True)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self._connected,
                "ts": self._ts,
                "values": dict(self._values),
                "telegram": self._telegram,
                "received": self.received,
                "rejected": self.rejected,
            }

    def _run(self):
        delay = 1.0
        while self.running:
            try:
                conn = socket.create_connection((self.host, self.port), timeout=10)
            except OSError as error:
                print(f"sin enlace con la lectora ({error}), reintentando", flush=True)
                if self._stop.wait(delay):
                    break
                delay = min(delay * 2, 30.0)
                continue
            if not self.running:
                conn.close()
                break
            delay = 1.0
            # Dos conexiones TCP no comparten continuidad: conservar una
            # trama incompleta mezclaria bytes viejos con el primer telegrama.
            self._reader = TelegramReader()
            with self._lock:
                self._conn = conn
                self._connected = True
            print(f"enlace establecido con {self.host}:{self.port}", flush=True)
            try:
                with conn:
                    conn.settimeout(30)
                    while self.running:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        self.consume(chunk)
            except OSError as error:
                if self.running:
                    print(f"enlace perdido: {error}", flush=True)
            finally:
                with self._lock:
                    self._conn = None
                    self._connected = False

    def _prune_loop(self):
        while not self._stop.wait(3600):
            try:
                self.store.prune(days=7)
            except Exception as error:
                print(f"no se pudo limpiar la base: {error}", flush=True)


class DashboardHandler(BaseHTTPRequestHandler):
    link = None  # lo inyecta make_server

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/":
            return self._send_file("index.html", "text/html; charset=utf-8")
        if route.path == "/api/latest":
            return self._send_json(self.link.snapshot())
        if route.path == "/api/history":
            return self._send_json(self._history(route.query))
        self.send_error(404)

    def _history(self, query):
        raw = parse_qs(query).get("minutes", ["60"])[0]
        try:
            minutes = int(raw)
        except ValueError:
            # Un parametro roto no debe tumbar el dashboard entero.
            return []
        return self.link.store.history(minutes=max(1, min(minutes, 60 * 24 * 7)))

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # El dashboard consulta cada segundo; una respuesta cacheada seria
        # un dashboard congelado.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name, content_type):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as handle:
                body = handle.read()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Silencio: una linea por peticion son 86.400 lineas de journal por
        dia por espectador, y ninguna dice nada."""


def make_server(link, port=HTTP_PORT):
    handler = type("BoundHandler", (DashboardHandler,), {"link": link})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    httpd.daemon_threads = True
    return httpd


def main():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    link = MeterLink(Store(DB_PATH))
    link.start()
    print(f"dashboard en http://0.0.0.0:{HTTP_PORT}", flush=True)
    make_server(link).serve_forever()


if __name__ == "__main__":
    main()
