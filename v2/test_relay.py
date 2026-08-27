import socket
import threading
import time

from dsmr import MeterState, generate_telegram
from relay import RelayServer, serial_reader_loop


class SerialFalso:
    """Puerto serie de mentira: entrega trozos ya preparados y luego se cuelga."""

    def __init__(self, chunks):
        self.port = "fake0"
        self._chunks = list(chunks)
        self.in_waiting = 0
        self.closed = False

    def read(self, _n):
        if self._chunks:
            return self._chunks.pop(0)
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_relay_reenvia_los_bytes_a_un_cliente_conectado():
    relay = RelayServer(port=puerto_libre())
    relay.start()
    try:
        cliente = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
        # El accept ocurre en otro hilo; se espera a que lo registre.
        for _ in range(100):
            if relay.clients:
                break
            time.sleep(0.01)
        relay.broadcast(b"hola")
        cliente.settimeout(2)
        assert cliente.recv(16) == b"hola"
    finally:
        cliente.close()
        relay.stop()


def test_relay_reenvia_un_telegrama_intacto():
    """El CRC lo calcula el medidor y debe seguir siendo valido al otro lado:
    por eso la Pi lectora reenvia crudo y no reserializa nada."""
    telegrama = generate_telegram(MeterState())
    relay = RelayServer(port=puerto_libre())
    relay.start()
    try:
        cliente = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
        for _ in range(100):
            if relay.clients:
                break
            time.sleep(0.01)
        relay.broadcast(telegrama)
        cliente.settimeout(2)
        recibido = b""
        while len(recibido) < len(telegrama):
            recibido += cliente.recv(4096)
        assert recibido == telegrama
    finally:
        cliente.close()
        relay.stop()


def test_relay_descarta_un_cliente_muerto_sin_caerse():
    relay = RelayServer(port=puerto_libre())
    relay.start()
    try:
        cliente = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
        for _ in range(100):
            if relay.clients:
                break
            time.sleep(0.01)
        cliente.close()
        for _ in range(5):
            relay.broadcast(b"x" * 1024)
        assert relay.clients == []
    finally:
        relay.stop()


def test_serial_reader_loop_publica_lo_que_lee():
    relay = RelayServer(port=puerto_libre())
    relay.start()
    recibido = []
    original = relay.broadcast
    relay.broadcast = lambda data: recibido.append(data)
    hilo = threading.Thread(
        target=serial_reader_loop,
        args=(relay,),
        kwargs={"serial_factory": lambda: SerialFalso([b"abc", b"def"])},
        daemon=True,
    )
    hilo.start()
    try:
        for _ in range(200):
            if b"".join(recibido) == b"abcdef":
                break
            time.sleep(0.01)
        assert b"".join(recibido) == b"abcdef"
    finally:
        relay.broadcast = original
        relay.stop()
