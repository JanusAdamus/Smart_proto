import random
import socket
import threading
import time
import tkinter as tk

from zeroconf import Zeroconf

from dsmr import MeterState, generate_telegram
from discovery import advertise_service

PORT = 3000


class MeterServer:
    def __init__(self, port=PORT):
        self.port = port
        self.state = MeterState()
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
        threading.Thread(target=self._broadcast_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                break
            conn.settimeout(5.0)
            with self.lock:
                self.clients.append(conn)

    def _broadcast_loop(self):
        while self.running:
            self.state.tick(1.0)
            telegram = generate_telegram(self.state)
            with self.lock:
                dead = []
                for conn in self.clients:
                    try:
                        conn.sendall(telegram)
                    except OSError:
                        dead.append(conn)
                for conn in dead:
                    self.clients.remove(conn)
            time.sleep(1.0 + random.uniform(-0.1, 0.1))

    def client_count(self):
        with self.lock:
            return len(self.clients)

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()


def main():
    server = MeterServer()
    server.start()

    zc = Zeroconf()
    info = advertise_service(zc, "_metersim._tcp.local.", "meter", PORT)

    root = tk.Tk()
    root.title("Smart Meter Simulator")
    root.geometry("320x120")
    label = tk.Label(root, text="Simulando...", font=("Segoe UI", 14))
    label.pack(pady=20)

    def update_label():
        label.config(text=f"Simulando... {server.client_count()} cliente(s) conectados")
        root.after(1000, update_label)

    def on_close():
        server.stop()
        zc.unregister_service(info)
        zc.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    update_label()
    root.mainloop()


if __name__ == "__main__":
    main()
