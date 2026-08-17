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

from zeroconf import Zeroconf

from dsmr import TelegramReader, parse_telegram, InvalidTelegram
from discovery import ServiceWaiter, connect_to_service

SERVICE_TYPE = "_smartmeter._tcp.local."
DIRECT_HOST = "192.168.50.1"
DIRECT_PORT = 4000
BUFFER_SIZE = 300


class DashboardState:
    def __init__(self):
        self.kw = deque(maxlen=BUFFER_SIZE)
        self.voltage = 0.0
        self.kwh = 0.0
        self.last_update = 0.0
        self.received_count = 0
        self.received_log = deque(maxlen=20)
        self.connection_status = f"Connecting to {DIRECT_HOST}:{DIRECT_PORT}..."
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


def connect_to_meter(
    zc: Zeroconf,
    direct_host=DIRECT_HOST,
    direct_port=DIRECT_PORT,
    timeout=2.0,
):
    """Conecta directo a la Pi; Zeroconf queda solo como respaldo.

    La red plug-and-play siempre asigna 192.168.50.1 a la Pi. Depender de
    multicast como unica ruta hace que el dashboard falle con redes Public de
    Windows o reglas de firewall, aunque el relay TCP sea perfectamente
    alcanzable.
    """
    try:
        sock = socket.create_connection((direct_host, direct_port), timeout=timeout)
        return sock, f"{direct_host}:{direct_port}"
    except OSError as direct_error:
        print(f"direct connection failed: {direct_error}; trying Zeroconf")

    waiter = ServiceWaiter(zc, SERVICE_TYPE)
    ips, port = waiter.wait(timeout=3.0)
    sock = connect_to_service(ips, port, timeout=5.0)
    return sock, f"discovered service on port {port}"


def reader_thread(state: DashboardState, zc: Zeroconf):
    while True:
        state.set_connection_status(f"Connecting to {DIRECT_HOST}:{DIRECT_PORT}...")
        try:
            sock, endpoint = connect_to_meter(zc)
        except (OSError, TimeoutError) as e:
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
                        print(f"discarded telegram: {e}")
                        continue
                    state.update(fields)
        except OSError:
            pass
        finally:
            sock.close()
            state.set_connection_status("Connection lost; retrying...")


def main():
    try:
        zc = Zeroconf()
        state = DashboardState()
        threading.Thread(target=reader_thread, args=(state, zc), daemon=True).start()

        root = tk.Tk()
        root.title("Live Consumption")
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
