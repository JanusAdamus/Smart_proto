import random
import threading
import time
import tkinter as tk
from tkinter import messagebox
from collections import deque

from dsmr import MeterState, generate_telegram
from serial_link import wait_and_open, BAUDRATE


class MeterSerialWriter:
    def __init__(self, serial_factory=None, baudrate=BAUDRATE):
        self.serial_factory = serial_factory or (lambda: wait_and_open(baudrate))
        self.state = MeterState()
        self.running = True
        self.ser = None
        self.sent_count = 0
        self.log = deque(maxlen=20)
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.running:
            if self.ser is None:
                try:
                    self.ser = self.serial_factory()
                except OSError:
                    time.sleep(2.0)
                    continue
            self.state.tick(1.0)
            telegram = generate_telegram(self.state)
            try:
                self.ser.write(telegram)
            except OSError:
                try:
                    self.ser.close()
                except OSError:
                    pass
                self.ser = None
                continue
            with self.lock:
                self.sent_count += 1
                self.log.append(f"Sent #{self.sent_count}: {self.state.kw:.3f} kW")
            time.sleep(1.0 + random.uniform(-0.1, 0.1))

    def status(self):
        with self.lock:
            ser = self.ser
            port = ser.port if ser else None
            return port, self.sent_count, list(self.log)

    def stop(self):
        self.running = False
        if self.ser:
            self.ser.close()


def main():
    try:
        writer = MeterSerialWriter()
        writer.start()

        root = tk.Tk()
        root.title("Smart Meter Simulator")
        root.geometry("340x320")
        status_label = tk.Label(root, text="Searching for serial port...", font=("Segoe UI", 12))
        status_label.pack(pady=10)
        log_box = tk.Listbox(root, height=14, font=("Consolas", 9))
        log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        def refresh():
            port, _count, log = writer.status()
            status_label.config(text=f"Connected on {port}" if port else "Searching for serial port...")
            log_box.delete(0, tk.END)
            for line in log:
                log_box.insert(tk.END, line)
            root.after(1000, refresh)

        def on_close():
            writer.stop()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        refresh()
        root.mainloop()
    except Exception as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Smart Meter Simulator - Error", str(e))
        raise


if __name__ == "__main__":
    main()
