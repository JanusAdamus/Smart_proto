import socket
import threading
import time
import tkinter as tk
from collections import deque

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from zeroconf import Zeroconf

from dsmr import TelegramReader, parse_telegram, InvalidTelegram
from discovery import ServiceWaiter

SERVICE_TYPE = "_smartmeter._tcp.local."
BUFFER_SIZE = 300


class DashboardState:
    def __init__(self):
        self.kw = deque(maxlen=BUFFER_SIZE)
        self.voltage = 0.0
        self.kwh = 0.0
        self.last_update = 0.0
        self.lock = threading.Lock()

    def update(self, fields: dict):
        with self.lock:
            self.kw.append(fields["kw"])
            self.voltage = fields["voltage"]
            self.kwh = fields["kwh"]
            self.last_update = time.time()

    def snapshot(self):
        with self.lock:
            return list(self.kw), self.voltage, self.kwh, self.last_update


def reader_thread(state: DashboardState, zc: Zeroconf):
    while True:
        waiter = ServiceWaiter(zc, SERVICE_TYPE)
        try:
            ip, port = waiter.wait(timeout=3.0)
        except TimeoutError:
            continue
        try:
            sock = socket.create_connection((ip, port), timeout=5.0)
            sock.settimeout(None)
        except OSError:
            time.sleep(3.0)
            continue
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
                        print(f"telegrama descartado: {e}")
                        continue
                    state.update(fields)
        except OSError:
            pass
        finally:
            sock.close()


def main():
    zc = Zeroconf()
    state = DashboardState()
    threading.Thread(target=reader_thread, args=(state, zc), daemon=True).start()

    root = tk.Tk()
    root.title("Consumo en vivo")
    root.geometry("500x420")

    kw_label = tk.Label(root, text="-- kW", font=("Segoe UI", 32))
    kw_label.pack(pady=10)
    info_label = tk.Label(root, text="Buscando smart meter...", font=("Segoe UI", 11))
    info_label.pack()

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
            status = "Sin datos recientes" if stale else "En vivo"
            info_label.config(text=f"{status} · {voltage:.1f} V · {kwh:.2f} kWh hoy")
            line.set_data(range(len(values)), values)
            ax.set_xlim(0, max(len(values), 1))
            canvas.draw_idle()
        root.after(1000, refresh)

    refresh()
    root.mainloop()


if __name__ == "__main__":
    main()
