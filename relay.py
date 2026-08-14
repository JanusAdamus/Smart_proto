# relay.py
import socket
import threading
import time

from zeroconf import Zeroconf

from discovery import advertise_service
from serial_link import wait_and_open, BAUDRATE

DOWNSTREAM_SERVICE = "_smartmeter._tcp.local."
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

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()


def serial_reader_loop(relay: RelayServer, serial_factory=None, retry_seconds=2.0):
    serial_factory = serial_factory or (lambda: wait_and_open(BAUDRATE))
    while True:
        try:
            ser = serial_factory()
        except OSError:
            time.sleep(retry_seconds)
            continue
        print(f"puerto serie conectado: {ser.port}")
        try:
            while True:
                chunk = ser.read(4096)
                if not chunk:
                    continue
                relay.broadcast(chunk)
        except OSError:
            print("puerto serie perdido, reintentando")
        finally:
            ser.close()


def main():
    zc = Zeroconf()

    relay = RelayServer()
    relay.start()
    advertise_service(zc, DOWNSTREAM_SERVICE, "smartmeter", DOWNSTREAM_PORT)

    serial_reader_loop(relay)


if __name__ == "__main__":
    main()
