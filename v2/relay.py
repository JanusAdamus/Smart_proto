"""Pi lectora: reenvia por TCP lo que llegue por el puerto serie.

No parsea nada a proposito. El telegrama ya es ASCII autodescriptivo y viene
con un CRC calculado por el medidor; reenviarlo intacto mantiene esa
proteccion valida hasta el consumidor final y deja un unico parser en todo el
sistema. Ademas hace que el mismo codigo sirva para un medidor real y para el
simulador, sin ninguna rama condicional.
"""

import socket
import threading
import time

from serial_link import BAUDRATE, wait_and_open

DOWNSTREAM_PORT = 4000


class RelayServer:
    def __init__(self, port=DOWNSTREAM_PORT):
        self.port = port
        self.clients = []
        self.lock = threading.Lock()
        self.running = True
        self.sock = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.listen(5)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                break
            conn.settimeout(5.0)
            with self.lock:
                self.clients.append(conn)

    def broadcast(self, data: bytes):
        with self.lock:
            dead = []
            for conn in self.clients:
                try:
                    conn.sendall(data)
                except OSError:
                    dead.append(conn)
            for conn in dead:
                self.clients.remove(conn)
                conn.close()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        with self.lock:
            for conn in self.clients:
                conn.close()
            self.clients.clear()


def serial_reader_loop(relay: RelayServer, serial_factory=None, retry_seconds=2.0):
    serial_factory = serial_factory or (lambda: wait_and_open(BAUDRATE))
    while True:
        try:
            ser = serial_factory()
        except OSError:
            time.sleep(retry_seconds)
            continue
        print(f"puerto serie conectado: {ser.port}", flush=True)
        try:
            while True:
                chunk = ser.read(ser.in_waiting or 1)
                if chunk:
                    relay.broadcast(chunk)
        except OSError:
            print("puerto serie perdido, reintentando", flush=True)
        finally:
            ser.close()
        time.sleep(retry_seconds)


def main():
    print("relay iniciando", flush=True)
    relay = RelayServer()
    relay.start()
    serial_reader_loop(relay)


if __name__ == "__main__":
    main()
