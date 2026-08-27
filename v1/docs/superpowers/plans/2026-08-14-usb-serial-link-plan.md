# Enlace Serie USB Real: Generador → Raspberry Pi — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el tramo TCP/Zeroconf simulado entre `meter_simulator.py` y `relay.py` por un puerto serie USB real, con detección automática de puerto en ambos extremos, y agregar un indicador visual de mensajes enviados/recibidos en las dos ventanas.

**Architecture:** Módulo compartido nuevo `serial_link.py` centraliza la detección/apertura de puerto serie (usado por `meter_simulator.py` y `relay.py`). `meter_simulator.py` pasa de servidor TCP a escritor de puerto serie. `relay.py` pasa de cliente Zeroconf/TCP (tramo generador) a lector de puerto serie, sin tocar su servidor TCP downstream hacia el dashboard. `dashboard.py` y `dsmr.py` no cambian su lógica, solo se agrega un log visual en el dashboard.

**Tech Stack:** Python 3.11+, `pyserial` (nuevo), `zeroconf`, `tkinter`, `pytest`. Tests de puerto serie usan `serial.serial_for_url("loop://")` (loopback en memoria de pyserial), sin hardware real.

**Spec:** `docs/superpowers/specs/2026-08-14-usb-serial-link-design.md` (y el spec original `docs/superpowers/specs/2026-08-13-smart-meter-prototype-design.md` para todo lo que no cambia).

## Global Constraints

- Baudrate del puerto serie: `115200`, 8N1 (default de pyserial). Constante `BAUDRATE = 115200` en `serial_link.py`.
- Detección de puerto: `serial.tools.list_ports.comports()`. Si hay exactamente uno o más de uno, se usa el primero de la lista devuelta. Si no hay ninguno, reintentar cada `2.0` segundos indefinidamente. Sin configuración manual (ni variable de entorno, ni CLI, ni UI de selección) — decisión explícita del spec.
- `relay.py` sigue sin parsear el contenido de los telegramas en el tramo generador→Pi — pasa bytes crudos tal cual los lee del puerto serie a `RelayServer.broadcast()`, igual que antes hacía con `upstream.recv()`. No se agrega `TelegramReader` de este lado.
- El tramo Pi↔dashboard (`RelayServer`, `advertise_service` de `_smartmeter._tcp.local.`, puerto TCP 4000, `dashboard.py` completo) no cambia.
- `dsmr.py` no cambia — `TelegramReader`, `generate_telegram`, `parse_telegram`, `crc16_arc` se reusan tal cual.
- Nueva dependencia en `requirements.txt`: `pyserial>=3.5`.
- Tests de puerto serie: `serial.serial_for_url("loop://", timeout=1)`, nunca mockear el módulo `serial` para el comportamiento de lectura/escritura — solo se monkeypatchea la función de *detección* de puertos (`comports`), que si depende de hardware real.
- Ningún componente persiste estado en disco — mismo constraint del spec original.

---

### Task 1: Módulo compartido de puerto serie (`serial_link.py`)

**Files:**
- Create: `C:\Smart meter prototype\serial_link.py`
- Test: `C:\Smart meter prototype\test_serial_link.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces:
  - `BAUDRATE = 115200` (constante)
  - `find_serial_port() -> str | None`
  - `wait_and_open(baudrate=BAUDRATE, poll_seconds=2.0, timeout=5.0) -> serial.Serial`

- [ ] **Step 1: Escribir tests que fallan para `find_serial_port`**

```python
# test_serial_link.py
class _FakePortInfo:
    def __init__(self, device):
        self.device = device


def test_find_serial_port_returns_none_when_no_ports(monkeypatch):
    import serial_link
    monkeypatch.setattr(serial_link.serial.tools.list_ports, "comports", lambda: [])
    assert serial_link.find_serial_port() is None


def test_find_serial_port_returns_first_device(monkeypatch):
    import serial_link
    fake_ports = [_FakePortInfo("COM7"), _FakePortInfo("COM8")]
    monkeypatch.setattr(serial_link.serial.tools.list_ports, "comports", lambda: fake_ports)
    assert serial_link.find_serial_port() == "COM7"
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `pytest test_serial_link.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'serial_link'`

- [ ] **Step 3: Instalar `pyserial` y agregarlo a `requirements.txt`**

```
# agregar a requirements.txt
pyserial>=3.5
```

Run: `pip install pyserial>=3.5` (o `pip install -r requirements.txt` luego de agregar la línea)

- [ ] **Step 4: Implementar `find_serial_port`**

```python
# serial_link.py
import time

import serial
import serial.tools.list_ports

BAUDRATE = 115200


def find_serial_port():
    ports = serial.tools.list_ports.comports()
    return ports[0].device if ports else None
```

- [ ] **Step 5: Correr y confirmar que pasan**

Run: `pytest test_serial_link.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Escribir test que falla para `wait_and_open` (reintenta hasta encontrar puerto)**

```python
# agregar a test_serial_link.py
def test_wait_and_open_retries_until_port_found(monkeypatch):
    import serial_link

    calls = {"n": 0}

    def fake_find():
        calls["n"] += 1
        return None if calls["n"] < 3 else "COM9"

    monkeypatch.setattr(serial_link, "find_serial_port", fake_find)
    monkeypatch.setattr(serial_link.time, "sleep", lambda s: None)

    opened = {}

    def fake_serial(port, baudrate, timeout):
        opened["port"] = port
        opened["baudrate"] = baudrate
        return "FAKE_SERIAL_OBJECT"

    monkeypatch.setattr(serial_link.serial, "Serial", fake_serial)

    result = serial_link.wait_and_open(poll_seconds=0)
    assert result == "FAKE_SERIAL_OBJECT"
    assert opened["port"] == "COM9"
    assert opened["baudrate"] == serial_link.BAUDRATE
    assert calls["n"] == 3
```

- [ ] **Step 7: Correr y confirmar que falla**

Run: `pytest test_serial_link.py -v`
Expected: FAIL con `AttributeError: module 'serial_link' has no attribute 'wait_and_open'`

- [ ] **Step 8: Implementar `wait_and_open`**

```python
# agregar a serial_link.py
def wait_and_open(baudrate=BAUDRATE, poll_seconds=2.0, timeout=5.0):
    while True:
        port = find_serial_port()
        if port:
            try:
                return serial.Serial(port, baudrate=baudrate, timeout=timeout)
            except OSError:
                pass
        time.sleep(poll_seconds)
```

- [ ] **Step 9: Correr todos los tests de `test_serial_link.py` y confirmar que pasan**

Run: `pytest test_serial_link.py -v`
Expected: PASS (3 tests)

- [ ] **Step 10: Commit**

```bash
git add requirements.txt serial_link.py test_serial_link.py
git commit -m "feat: modulo compartido de deteccion/apertura de puerto serie"
```

---

### Task 2: Generador por puerto serie (`meter_simulator.py`)

**Files:**
- Modify: `C:\Smart meter prototype\meter_simulator.py` (reemplaza completo)
- Test: `C:\Smart meter prototype\test_meter_simulator.py` (reemplaza completo)

**Interfaces:**
- Consumes: `dsmr.MeterState`, `dsmr.generate_telegram`, `dsmr.TelegramReader`, `dsmr.parse_telegram` (de Task 1 del plan original), `serial_link.wait_and_open`, `serial_link.BAUDRATE` (de Task 1 de este plan)
- Produces: `class MeterSerialWriter: __init__(self, serial_factory=None, baudrate=BAUDRATE)`, `start(self) -> None`, `status(self) -> tuple[str|None, int, list[str]]` (puerto actual o `None`, cantidad enviada, últimas líneas de log), `stop(self) -> None`

- [ ] **Step 1: Escribir test que falla — el escritor manda telegramas válidos por el puerto serie**

```python
# test_meter_simulator.py
import time

import serial

from meter_simulator import MeterSerialWriter
from dsmr import TelegramReader, parse_telegram


def test_meter_serial_writer_sends_valid_telegrams():
    ser = serial.serial_for_url("loop://", timeout=1)
    writer = MeterSerialWriter(serial_factory=lambda: ser)
    writer.start()
    try:
        reader = TelegramReader()
        telegrams = []
        deadline = time.time() + 5.0
        while len(telegrams) < 1 and time.time() < deadline:
            chunk = ser.read(4096)
            telegrams.extend(reader.feed(chunk))
        assert len(telegrams) >= 1
        fields = parse_telegram(telegrams[0])
        assert 0.0 <= fields["kw"] <= 5.0
    finally:
        writer.stop()


def test_meter_serial_writer_tracks_status_and_log():
    ser = serial.serial_for_url("loop://", timeout=1)
    writer = MeterSerialWriter(serial_factory=lambda: ser)
    writer.start()
    try:
        deadline = time.time() + 5.0
        port, count, log = writer.status()
        while count < 1 and time.time() < deadline:
            time.sleep(0.1)
            port, count, log = writer.status()
        assert count >= 1
        assert port == ser.port
        assert log[-1].startswith(f"Enviado #{count}:")
    finally:
        writer.stop()
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `pytest test_meter_simulator.py -v`
Expected: FAIL — `meter_simulator` todavía tiene la clase vieja `MeterServer`, no `MeterSerialWriter`

- [ ] **Step 3: Reemplazar `meter_simulator.py` completo**

```python
# meter_simulator.py
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
                self.ser.close()
                self.ser = None
                continue
            with self.lock:
                self.sent_count += 1
                self.log.append(f"Enviado #{self.sent_count}: {self.state.kw:.3f} kW")
            time.sleep(1.0 + random.uniform(-0.1, 0.1))

    def status(self):
        with self.lock:
            port = self.ser.port if self.ser else None
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
        status_label = tk.Label(root, text="Buscando puerto serie...", font=("Segoe UI", 12))
        status_label.pack(pady=10)
        log_box = tk.Listbox(root, height=14, font=("Consolas", 9))
        log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        def refresh():
            port, _count, log = writer.status()
            status_label.config(text=f"Conectado en {port}" if port else "Buscando puerto serie...")
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
```

- [ ] **Step 4: Correr y confirmar que pasan**

Run: `pytest test_meter_simulator.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add meter_simulator.py test_meter_simulator.py
git commit -m "feat: generador escribe telegramas por puerto serie en vez de TCP"
```

---

### Task 3: Relay lee del puerto serie (`relay.py`)

**Files:**
- Modify: `C:\Smart meter prototype\relay.py` (se elimina `wait_for_meter`/`connect_upstream`, se agrega `serial_reader_loop`; `RelayServer` no cambia)
- Test: `C:\Smart meter prototype\test_relay.py` (se agrega un test nuevo, el existente de `RelayServer` no cambia)

**Interfaces:**
- Consumes: `serial_link.wait_and_open`, `serial_link.BAUDRATE` (Task 1), `discovery.advertise_service` (ya existente)
- Produces: `serial_reader_loop(relay: RelayServer, serial_factory=None, retry_seconds=2.0) -> None` (bucle infinito, no retorna)
- `class RelayServer` sin cambios: `__init__(self, port=4000)`, `start(self)`, `broadcast(self, data: bytes)`, `stop(self)`

- [ ] **Step 1: Escribir test que falla — `serial_reader_loop` reenvía bytes leídos del puerto serie a los clientes del relay**

```python
# agregar a test_relay.py
import threading

import serial

from relay import serial_reader_loop


def test_serial_reader_loop_forwards_bytes_to_relay_clients():
    ser = serial.serial_for_url("loop://", timeout=1)
    relay = RelayServer(port=14001)
    relay.start()
    try:
        client = socket.create_connection(("127.0.0.1", 14001), timeout=5.0)
        time.sleep(0.2)
        threading.Thread(
            target=serial_reader_loop,
            args=(relay,),
            kwargs={"serial_factory": lambda: ser},
            daemon=True,
        ).start()
        ser.write(b"hola\r\n")
        assert client.recv(100) == b"hola\r\n"
        client.close()
    finally:
        relay.stop()
        ser.close()
```

(`import socket` y `import time` ya están al inicio de `test_relay.py` desde el task original.)

- [ ] **Step 2: Correr y confirmar que falla**

Run: `pytest test_relay.py -v`
Expected: FAIL con `ImportError: cannot import name 'serial_reader_loop' from 'relay'`

- [ ] **Step 3: Reemplazar la sección de conexión upstream en `relay.py`**

Reemplazar todo el archivo `relay.py` por:

```python
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
```

- [ ] **Step 4: Correr y confirmar que pasan**

Run: `pytest test_relay.py -v`
Expected: PASS (2 tests: el de `RelayServer.broadcast` existente + el nuevo de `serial_reader_loop`)

- [ ] **Step 5: Commit**

```bash
git add relay.py test_relay.py
git commit -m "feat: relay lee del generador por puerto serie en vez de TCP/Zeroconf"
```

---

### Task 4: Log visual de recepción en el dashboard (`dashboard.py`)

**Files:**
- Modify: `C:\Smart meter prototype\dashboard.py`
- Test: `C:\Smart meter prototype\test_dashboard.py` (se agrega un test nuevo, los dos existentes no cambian)

**Interfaces:**
- Consumes: nada nuevo (mismas dependencias que ya tenía)
- Produces: `DashboardState.log_snapshot(self) -> list[str]` (nuevo método; `update`, `snapshot` mantienen su firma actual)

- [ ] **Step 1: Escribir test que falla — `update` registra una línea de log legible**

```python
# agregar a test_dashboard.py
def test_dashboard_state_logs_received_messages():
    state = DashboardState()
    meter_state = MeterState(kw=1.8, voltage=231.0, kwh=99.0)
    raw = generate_telegram(meter_state)
    reader = TelegramReader()
    for t in reader.feed(raw):
        fields = parse_telegram(t)
        state.update(fields)
    log = state.log_snapshot()
    assert log == ["Recibido #1: 1.800 kW"]
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `pytest test_dashboard.py -v`
Expected: FAIL con `AttributeError: 'DashboardState' object has no attribute 'log_snapshot'`

- [ ] **Step 3: Modificar `DashboardState` y la UI en `dashboard.py`**

Reemplazar la clase `DashboardState` por:

```python
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
            self.received_log.append(f"Recibido #{self.received_count}: {fields['kw']:.3f} kW")

    def snapshot(self):
        with self.lock:
            return list(self.kw), self.voltage, self.kwh, self.last_update

    def log_snapshot(self):
        with self.lock:
            return list(self.received_log)
```

Agregar el widget de log a la UI en `main()` (después de crear `info_label` y antes de crear `fig`):

```python
    log_box = tk.Listbox(root, height=8, font=("Consolas", 9))
    log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
```

Y dentro de `refresh()`, después de actualizar `info_label` (dentro del `if values:`), agregar:

```python
            log_box.delete(0, tk.END)
            for line in state.log_snapshot():
                log_box.insert(tk.END, line)
```

- [ ] **Step 4: Correr y confirmar que pasan**

Run: `pytest test_dashboard.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard.py test_dashboard.py
git commit -m "feat: log visual de mensajes recibidos en el dashboard"
```

---

### Task 5: Instalador de la Pi, empaquetado y prueba end-to-end

**Files:**
- Modify: `C:\Smart meter prototype\install_relay.sh`
- Modify: `C:\Smart meter prototype\test_integration_e2e.py` (reemplaza completo)
- No crea archivos nuevos — reconstruye `dist\meter_simulator.exe` y `dist\dashboard.exe` con `build_windows.ps1` (sin cambios al script).

**Interfaces:**
- Consumes: `meter_simulator.MeterSerialWriter`, `relay.RelayServer`, `relay.serial_reader_loop`, `discovery.ServiceWaiter`, `discovery.advertise_service`, `dsmr.TelegramReader`, `dsmr.parse_telegram` (todo lo anterior de este plan)
- Produces: nada nuevo que otras tasks consuman — es la task final.

- [ ] **Step 1: Actualizar `install_relay.sh` — instalar `pyserial` y copiar `serial_link.py`**

```bash
#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y python3-zeroconf python3-serial
sudo mkdir -p /opt/smartmeter
sudo cp relay.py discovery.py serial_link.py /opt/smartmeter/
sudo cp relay.service /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable --now relay
echo "Relay instalado y corriendo. Ver logs: journalctl -u relay -f"
```

- [ ] **Step 2: Reemplazar `test_integration_e2e.py` — cadena completa con puerto serie simulado (`loop://`)**

```python
# test_integration_e2e.py
import socket
import threading
import time

import serial
from zeroconf import Zeroconf

from meter_simulator import MeterSerialWriter
from relay import RelayServer, serial_reader_loop
from discovery import ServiceWaiter, advertise_service
from dsmr import TelegramReader, parse_telegram

TEST_DOWNSTREAM = "_smartmetertest._tcp.local."


def test_full_chain_serial_meter_to_relay_to_dashboard():
    shared_ser = serial.serial_for_url("loop://", timeout=1)

    writer = MeterSerialWriter(serial_factory=lambda: shared_ser)
    writer.start()

    zc = Zeroconf()
    relay = RelayServer(port=24000)
    relay.start()
    relay_info = advertise_service(zc, TEST_DOWNSTREAM, "smartmeter-e2e", 24000)

    threading.Thread(
        target=serial_reader_loop,
        args=(relay,),
        kwargs={"serial_factory": lambda: shared_ser},
        daemon=True,
    ).start()

    try:
        waiter = ServiceWaiter(zc, TEST_DOWNSTREAM)
        ip, port = waiter.wait(timeout=10.0)
        client = socket.create_connection((ip, port), timeout=10.0)
        reader = TelegramReader()
        telegrams = []
        deadline = time.time() + 10.0
        while len(telegrams) < 1 and time.time() < deadline:
            chunk = client.recv(4096)
            telegrams.extend(reader.feed(chunk))
        assert len(telegrams) >= 1
        fields = parse_telegram(telegrams[0])
        assert 0.0 <= fields["kw"] <= 5.0
        client.close()
    finally:
        writer.stop()
        relay.stop()
        zc.unregister_service(relay_info)
        zc.close()
```

Run: `pytest test_integration_e2e.py -v`
Expected: PASS (1 test) — el generador escribe telegramas al puerto serie compartido en memoria, el relay los lee de ahí y los reenvía por TCP/Zeroconf, y un cliente estilo-dashboard los recibe y parsea correctamente. Cubre el pipeline completo con la nueva arquitectura, sin hardware real.

- [ ] **Step 3: Reconstruir los ejecutables**

Run: `pip install -r requirements.txt` (para que `pyserial` esté instalado antes del build)
Run: `powershell -File build_windows.ps1`
Expected: se regeneran `dist\meter_simulator.exe` y `dist\dashboard.exe` sin errores. `meter_simulator.exe` ahora empaqueta `pyserial` — si `pip install -r requirements.txt` no lo tenía instalado en el entorno de build, este paso falla con `ModuleNotFoundError: No module named 'serial'` en tiempo de build o de ejecución del exe; confirmar que el paso anterior corrió sin errores antes de este.

- [ ] **Step 4: Correr toda la suite de tests**

Run: `pytest -v`
Expected: PASS — todos los tests del proyecto, incluidos los de `test_serial_link.py`, `test_meter_simulator.py`, `test_relay.py`, `test_dashboard.py`, `test_integration_e2e.py` y los ya existentes de `test_dsmr.py`/`test_discovery.py` sin cambios.

- [ ] **Step 5: Commit**

```bash
git add install_relay.sh test_integration_e2e.py
git commit -m "build: puerto serie en el instalador de la Pi + prueba e2e con puerto serie simulado"
```

---

## Self-Review

- **Cobertura del spec:** detección automática de puerto serie con reintento indefinido (Task 1), generador escribe por puerto serie con indicador visual de enviados (Task 2), relay lee por puerto serie sin parsear y sigue sirviendo TCP/Zeroconf downstream sin cambios (Task 3), dashboard con indicador visual de recibidos (Task 4), instalador de la Pi actualizado + empaquetado + prueba end-to-end con la nueva arquitectura (Task 5) — todo lo del spec `2026-08-14-usb-serial-link-design.md` cubierto. Lo que el spec marca "fuera de alcance" (selección manual de puerto, desambiguación por VID/PID, cambios al tramo Pi↔dashboard, configuración de red Ethernet) correctamente no tiene task.
- **Placeholders:** ninguno; todos los pasos tienen código completo o comandos exactos.
- **Consistencia de tipos:** `MeterSerialWriter` (Task 2) y `serial_reader_loop` (Task 3) comparten el mismo patrón de `serial_factory` inyectable definido en Task 1 (`wait_and_open`); `RelayServer.broadcast(self, data: bytes)` se usa igual en Task 3 y Task 5; `DashboardState.log_snapshot()` (Task 4) no colisiona con `snapshot()` existente. Todo consistente entre tasks.
