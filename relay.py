import socket
import threading
import time

from zeroconf import Zeroconf

from discovery import ServiceWaiter, advertise_service

UPSTREAM_SERVICE = "_metersim._tcp.local."
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
            conn.settimeout(5.0)  # Fix: prevent hung clients from blocking broadcasts
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


def connect_upstream(ip, port, retry_seconds=3.0):
    while True:
        try:
            return socket.create_connection((ip, port), timeout=5.0)
        except OSError:
            time.sleep(retry_seconds)


def wait_for_meter(zc):
    while True:
        waiter = ServiceWaiter(zc, UPSTREAM_SERVICE)
        try:
            return waiter.wait(timeout=3.0)
        except TimeoutError:
            continue


def main():
    zc = Zeroconf()

    relay = RelayServer()
    relay.start()
    advertise_service(zc, DOWNSTREAM_SERVICE, "smartmeter", DOWNSTREAM_PORT)

    while True:
        upstream_ip, upstream_port = wait_for_meter(zc)
        upstream = connect_upstream(upstream_ip, upstream_port)
        try:
            while True:
                chunk = upstream.recv(4096)
                if not chunk:
                    break
                relay.broadcast(chunk)
        except OSError:
            pass
        finally:
            upstream.close()


if __name__ == "__main__":
    main()
