import random
import threading
import time
import tkinter as tk
from tkinter import messagebox
from collections import deque

from dsmr import MeterState, generate_telegram
from serial_link import open_all_ports, port_still_present, BAUDRATE

APP_VERSION = "4.0 P1"


class MeterSerialWriter:
    def __init__(self, serial_factory=None, baudrate=BAUDRATE):
        self.serial_factory = serial_factory or (lambda: open_all_ports(baudrate, on_log=self._note))
        self.state = MeterState()
        self.running = True
        self.ports = []
        self.watched = set()
        self.sent_count = 0
        self.log = deque(maxlen=20)
        self.latest_telegram = ""
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.running:
            self._tick()

    def _note(self, text):
        with self.lock:
            self.log.append(text)

    def _drop(self, ser):
        try:
            ser.close()
        except OSError:
            pass
        self.ports.remove(ser)
        self.watched.discard(ser.port)

    def _port_alive(self, ser) -> bool:
        """Ninguno de los dos chequeos alcanza solo.

        La lista del sistema no basta: al reconectarlo en otro USB, Windows
        puede darle el mismo nombre COM y el puerto "sigue estando" aunque el
        handle viejo apunte a un dispositivo muerto. El handle tampoco basta:
        el write() no falla porque los bytes se van al buffer del driver, pero
        in_waiting consulta el estado del dispositivo y eso si da error.
        """
        if ser.port in self.watched and not port_still_present(ser.port):
            return False
        try:
            ser.in_waiting
        except OSError:
            return False
        return True

    def _tick(self):
        if not self.ports:
            try:
                self.ports = self.serial_factory()
            except OSError:
                time.sleep(2.0)
                return
            # Solo se vigilan los puertos que el sistema realmente lista: los
            # virtuales de los tests (loop://) nunca figuran y no se desenchufan.
            self.watched = {s.port for s in self.ports if port_still_present(s.port)}
            for ser in self.ports:
                self._note(f"Opened {ser.port}")
        with self.lock:
            self.state.tick(1.0)
            telegram = generate_telegram(self.state)
            energy_kwh = self.state.kwh
        for ser in list(self.ports):
            if self._port_alive(ser):
                try:
                    ser.write(telegram)
                    continue
                except OSError:
                    pass
            self._note(f"{ser.port} unplugged, searching again")
            self._drop(ser)
        if not self.ports:
            return
        with self.lock:
            self.sent_count += 1
            self.log.append(f"Sent #{self.sent_count}: {energy_kwh:,.3f} kWh")
            self.latest_telegram = telegram.decode("ascii").rstrip("\r\n")
        time.sleep(1.0 + random.uniform(-0.1, 0.1))

    def status(self):
        with self.lock:
            return [ser.port for ser in list(self.ports)], self.sent_count, list(self.log)

    def latest_telegram_snapshot(self):
        with self.lock:
            return self.latest_telegram

    def reading_snapshot(self):
        with self.lock:
            return self.state.kwh, self.state.kw, self.state.voltage

    def stop(self):
        self.running = False
        for ser in list(self.ports):
            ser.close()


class SmartMeterGeneratorUI:
    def __init__(self, root, writer):
        self.root = root
        self.writer = writer
        root.title(f"Smart Meter Generator {APP_VERSION}")
        root.geometry("650x590")
        root.minsize(580, 520)
        self._build()
        self._refresh()

    def _build(self):
        tk.Label(
            self.root,
            text="Smart Meter P1 Generator",
            font=("Segoe UI", 18, "bold"),
        ).pack(pady=(16, 4))
        self.status_label = tk.Label(
            self.root,
            text="Searching for serial port...",
            fg="#9a6700",
            font=("Segoe UI", 11, "bold"),
        )
        self.status_label.pack(pady=(0, 12))

        values = tk.LabelFrame(self.root, text="Current meter values", font=("Segoe UI", 10))
        values.pack(fill=tk.X, padx=14, pady=(0, 10))
        for column in range(3):
            values.grid_columnconfigure(column, weight=1)
        self.energy_label = self._value(values, 0, "Accumulated energy", "-- kWh")
        self.power_label = self._value(values, 1, "Current power", "-- kW")
        self.voltage_label = self._value(values, 2, "Voltage", "-- V")

        log_frame = tk.LabelFrame(self.root, text="Transmission log", font=("Segoe UI", 10))
        log_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        self.log_box = tk.Listbox(log_frame, height=8, font=("Consolas", 9))
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=7, pady=7)

        telegram_frame = tk.LabelFrame(
            self.root,
            text="Latest complete telegram",
            font=("Segoe UI", 10),
        )
        telegram_frame.pack(fill=tk.X, padx=14, pady=(0, 10))
        self.telegram_box = tk.Text(
            telegram_frame,
            height=7,
            wrap=tk.NONE,
            font=("Consolas", 10),
            bg="white",
            state=tk.DISABLED,
        )
        self.telegram_box.pack(fill=tk.X, padx=7, pady=7)
        tk.Label(
            self.root,
            text="DSMR 5.0  |  Serial output  |  1 telegram per second",
            fg="#666666",
            font=("Segoe UI", 9),
        ).pack(pady=(0, 12))

    @staticmethod
    def _value(parent, column, title, initial):
        frame = tk.Frame(parent)
        frame.grid(row=0, column=column, sticky="nsew", padx=8, pady=9)
        tk.Label(frame, text=title, fg="#555555", font=("Segoe UI", 9)).pack()
        label = tk.Label(frame, text=initial, font=("Consolas", 15, "bold"))
        label.pack(pady=(3, 0))
        return label

    def _refresh(self):
        ports, _count, log = self.writer.status()
        if ports:
            self.status_label.configure(text=f"Sending on {', '.join(ports)}", fg="#237a36")
        else:
            self.status_label.configure(text="Searching for serial port...", fg="#9a6700")

        energy_kwh, power_kw, voltage_v = self.writer.reading_snapshot()
        self.energy_label.configure(text=f"{energy_kwh:,.3f} kWh")
        self.power_label.configure(text=f"{power_kw:.3f} kW")
        self.voltage_label.configure(text=f"{voltage_v:.1f} V")

        self.log_box.delete(0, tk.END)
        for entry in log:
            self.log_box.insert(tk.END, entry)
        self.log_box.yview_moveto(1.0)

        self.telegram_box.configure(state=tk.NORMAL)
        self.telegram_box.delete("1.0", tk.END)
        self.telegram_box.insert("1.0", self.writer.latest_telegram_snapshot().replace("\r\n", "\n"))
        self.telegram_box.configure(state=tk.DISABLED)
        self.root.after(1000, self._refresh)


def main():
    writer = None
    try:
        writer = MeterSerialWriter()
        writer.start()
        root = tk.Tk()
        SmartMeterGeneratorUI(root, writer)

        def on_close():
            writer.stop()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
    except Exception as error:
        if writer is not None:
            writer.stop()
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Smart Meter Generator - Error", str(error))
        raise


if __name__ == "__main__":
    main()
