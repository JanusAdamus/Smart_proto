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
BUFFER_SIZE = 300


class DashboardState:
    def __init__(self):
        self.kw = deque(maxlen=BUFFER_SIZE)
        self.voltage = 0.0
        self.kwh = 0.0
        self.last_update = 0.0
        self.received_count = 0
        self.received_log = deque(maxlen=20)
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


def reader_thread(state: DashboardState, zc: Zeroconf):
    while True:
        waiter = ServiceWaiter(zc, SERVICE_TYPE)
        try:
            ips, port = waiter.wait(timeout=3.0)
        except TimeoutError:
            print("no smart meter advertised yet")
            continue
        try:
            sock = connect_to_service(ips, port, timeout=5.0)
        except OSError:
            time.sleep(3.0)
            continue
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
