import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox
from collections import deque

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import ifaddr

from dsmr import TelegramReader, parse_telegram, InvalidTelegram

APP_VERSION = "3.1 Plug and Play"
METER_PORT = 4000
KNOWN_HOSTS = (
    "192.168.50.1",   # subred que configura install_relay.sh
    "169.254.50.1",   # respaldo link-local/APIPA
)
BUFFER_SIZE = 300


def candidate_endpoints(port=METER_PORT):
    """Direcciones donde puede estar la Pi, deducidas de lo que recibio Windows.

    No alcanza con fijar 192.168.50.1. La Pi reparte DHCP, y si NetworkManager
    no aplica la subred configurada usa la suya propia (10.42.0.x): el receptor
    obtiene una IP perfectamente valida y aun asi ninguna direccion fija
    responde, que es exactamente el WinError 10065 en una maquina recien
    conectada. La Pi siempre es el .1 de la subred que reparte, asi que se
    deduce de la direccion local en lugar de suponerla.
    """
    hosts = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            # 169.254.x.x es /16: el .1 del /24 no significa nada ahi y solo
            # agrega esperas. Esa red la cubre la constante de KNOWN_HOSTS.
            if not ip.is_IPv4 or ip.ip.startswith(("127.", "169.254.")):
                continue
            gateway = ip.ip.rsplit(".", 1)[0] + ".1"
            if gateway != ip.ip and gateway not in hosts:
                hosts.append(gateway)
    for known in KNOWN_HOSTS:
        if known not in hosts:
            hosts.append(known)
    return tuple((host, port) for host in hosts)


class DashboardState:
    def __init__(self):
        self.kw = deque(maxlen=BUFFER_SIZE)
        self.voltage = 0.0
        self.kwh = 0.0
        self.last_update = 0.0
        self.received_count = 0
        self.received_log = deque(maxlen=20)
        self.connection_status = "Connecting to the Pi automatically..."
        self.lock = threading.Lock()

    def update(self, fields: dict):
        with self.lock:
            self.kw.append(fields["kw"])
            self.voltage = fields["voltage"]
            self.kwh = fields["kwh"]
            self.last_update = time.time()
            self.received_count += 1
            self.received_log.append(f"Received #{self.received_count}: {fields['kw']:.3f} kW")

    def snapshot(self):
        with self.lock:
            return list(self.kw), self.voltage, self.kwh, self.last_update

    def log_snapshot(self):
        with self.lock:
            return list(self.received_log)

    def set_connection_status(self, text):
        with self.lock:
            self.connection_status = text

    def connection_status_snapshot(self):
        with self.lock:
            return self.connection_status


def connect_to_meter(endpoints=None, timeout=1.5):
    """Conecta a la Pi sin configurar nada en el receptor.

    Se prueban las direcciones deducidas de las interfaces locales y despues
    las conocidas. Todas son rutas salientes dentro de la red local: no hacen
    falta gateway, DNS, mDNS, reglas de firewall entrante ni permisos de
    administrador.
    """
    if endpoints is None:
        endpoints = candidate_endpoints()
    errors = []
    for host, port in endpoints:
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            return sock, f"{host}:{port}"
        except OSError as e:
            errors.append(f"{host}: {e}")
    raise OSError("Pi unreachable; tried " + ", ".join(errors))


def reader_thread(state: DashboardState):
    while True:
        state.set_connection_status("Connecting to the Pi automatically...")
        try:
            sock, endpoint = connect_to_meter()
        except OSError as e:
            state.set_connection_status(f"Meter unavailable; retrying ({e})")
            time.sleep(2.0)
            continue
        state.set_connection_status(f"Connected to {endpoint}; waiting for data...")
        sock.settimeout(30.0)
        reader = TelegramReader()
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                for raw in reader.feed(chunk):
                    try:
                        fields = parse_telegram(raw)
                    except InvalidTelegram as e:
                        state.set_connection_status(f"Invalid meter data: {e}")
                        continue
                    state.update(fields)
        except OSError:
            pass
        finally:
            sock.close()
            state.set_connection_status("Connection lost; retrying...")


def main():
    try:
        state = DashboardState()
        threading.Thread(target=reader_thread, args=(state,), daemon=True).start()

        root = tk.Tk()
        root.title(f"Smart Meter Dashboard {APP_VERSION}")
        root.geometry("500x420")

        kw_label = tk.Label(root, text="-- kW", font=("Segoe UI", 32))
        kw_label.pack(pady=10)
        info_label = tk.Label(root, text="Searching for smart meter...", font=("Segoe UI", 11))
        info_label.pack()

        log_box = tk.Listbox(root, height=8, font=("Consolas", 9))
        log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        fig = Figure(figsize=(5, 2.5))
        ax = fig.add_subplot(111)
        line, = ax.plot([], [])
        ax.set_ylim(0, 4.5)
        canvas = FigureCanvasTkAgg(fig, master=root)
        canvas.get_tk_widget().pack(pady=10)

        def refresh():
            values, voltage, kwh, last_update = state.snapshot()
            if values:
                kw_label.config(text=f"{values[-1]:.2f} kW")
                stale = (time.time() - last_update) > 5.0
                status = "No recent data" if stale else "Live"
                info_label.config(text=f"{status} · {voltage:.1f} V · {kwh:.2f} kWh today")
                log_box.delete(0, tk.END)
                for line_text in state.log_snapshot():
                    log_box.insert(tk.END, line_text)
                line.set_data(range(len(values)), values)
                ax.set_xlim(0, max(len(values), 1))
                canvas.draw_idle()
            else:
                info_label.config(text=state.connection_status_snapshot())
            root.after(1000, refresh)

        refresh()
        root.mainloop()
    except Exception as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Live Consumption - Error", str(e))
        raise


if __name__ == "__main__":
    main()
