# Prototipo Smart Meter → Raspberry Pi → Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir los tres componentes del prototipo (simulador de medidor, relay de Raspberry Pi, dashboard) de forma que hoy quede todo probado localmente sin la Pi, y mañana solo falte el bring-up físico de la Pi.

**Architecture:** Repo plano en Python. Un módulo compartido `dsmr.py` (generación/parseo de telegramas DSMR + CRC16) y `discovery.py` (Zeroconf) son importados por los tres entry points (`meter_simulator.py`, `relay.py`, `dashboard.py`). Cada entry point se empaqueta o despliega por separado.

**Tech Stack:** Python 3.11+, `zeroconf` (discovery, sin depender de Bonjour/Avahi del SO), `tkinter` + `matplotlib` (UI), `pyinstaller` (empaquetado .exe), `pytest` (tests).

**Spec:** `docs/superpowers/specs/2026-08-13-smart-meter-prototype-design.md`

## Global Constraints

- Todo el proyecto vive en un único repo git **local** (sin remoto, sin GitHub) en `C:\Smart meter prototype`.
- Ningún componente escribe a disco, base de datos, ni persiste estado entre reinicios.
- Discovery usa exclusivamente la librería `zeroconf` vía su API de servicios (`ServiceInfo`/`ServiceBrowser`), nunca resolución de hostname `.local` del sistema operativo.
- Puertos fijos: simulador TCP 3000, relay TCP 4000.
- Tipos de servicio Zeroconf: `_metersim._tcp.local.` (simulador), `_smartmeter._tcp.local.` (relay).
- Checksum de telegramas: CRC16/ARC (polinomio `0xA001`, init `0x0000`) sobre los bytes desde `/` hasta `!` inclusive.
- PC1 y PC2 son `.exe` de doble clic (PyInstaller `--onefile --windowed`), sin instalación de Python visible al usuario final.

---

## Estructura de archivos

```
C:\Smart meter prototype\
  dsmr.py               # CRC16, MeterState, generate_telegram, parse_telegram, TelegramReader
  discovery.py           # get_local_ip, advertise_service, ServiceWaiter
  meter_simulator.py     # PC1: TCP server + UI + advertise _metersim
  relay.py               # Pi: cliente de meter + servidor TCP 4000 + advertise _smartmeter
  relay.service           # systemd unit para la Pi
  install_relay.sh        # script de instalación de la Pi (una sola vez)
  dashboard.py            # PC2: cliente de relay + parseo + UI en vivo
  requirements.txt
  build_windows.ps1       # empaqueta meter_simulator.exe y dashboard.exe
  test_dsmr.py
  test_discovery.py
  test_meter_simulator.py
  test_relay.py
  test_dashboard.py
```

---

### Task 1: Setup del repo + núcleo DSMR (`dsmr.py`)

**Files:**
- Create: `C:\Smart meter prototype\requirements.txt`
- Create: `C:\Smart meter prototype\dsmr.py`
- Test: `C:\Smart meter prototype\test_dsmr.py`

**Interfaces:**
- Produces:
  - `crc16_arc(data: bytes) -> int`
  - `class MeterState: __init__(self, kw=1.5, voltage=230.0, kwh=None)`, método `tick(self, dt_seconds=1.0) -> None`
  - `generate_telegram(state: MeterState, now=None) -> bytes`
  - `class InvalidTelegram(Exception)`
  - `parse_telegram(raw: bytes) -> dict` (claves `"kw"`, `"kwh"`, `"voltage"`, todos `float`)
  - `class TelegramReader: feed(self, chunk: bytes) -> list[bytes]`

- [ ] **Step 1: Inicializar el repo git local**

```bash
cd "/c/Smart meter prototype"
git init
```

- [ ] **Step 2: Crear `requirements.txt`**

```
zeroconf>=0.131
matplotlib>=3.8
pyinstaller>=6.3
pytest>=8.0
```

- [ ] **Step 3: Escribir tests que fallan para `crc16_arc` y `generate_telegram`**

```python
# test_dsmr.py
from dsmr import crc16_arc, MeterState, generate_telegram, parse_telegram, InvalidTelegram, TelegramReader


def test_crc16_arc_known_vector():
    # "123456789" es el vector de prueba estándar para CRC-16/ARC, resultado conocido: 0xBB3D
    assert crc16_arc(b"123456789") == 0xBB3D


def test_generate_telegram_has_valid_checksum():
    state = MeterState(kw=2.345, voltage=230.4, kwh=671.578)
    raw = generate_telegram(state, now=(2026, 8, 13, 12, 0, 0, 0, 0, 0))
    assert raw.startswith(b"/ISK5")
    assert raw.rstrip(b"\r\n").split(b"!")[-1] != b""
```

- [ ] **Step 4: Correr y confirmar que fallan**

Run: `pytest test_dsmr.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'dsmr'` (o `ImportError`)

- [ ] **Step 5: Implementar `crc16_arc` y `generate_telegram`**

```python
# dsmr.py
import random
import re
import time


def crc16_arc(data: bytes) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


class MeterState:
    def __init__(self, kw=1.5, voltage=230.0, kwh=None):
        self.kw = kw
        self.voltage = voltage
        self.kwh = kwh if kwh is not None else random.uniform(100.0, 5000.0)

    def tick(self, dt_seconds=1.0):
        self.kw = min(4.0, max(0.3, self.kw + random.gauss(0, 0.05)))
        self.voltage = min(235.0, max(225.0, self.voltage + random.gauss(0, 0.3)))
        self.kwh += self.kw * (dt_seconds / 3600.0)


def generate_telegram(state: MeterState, now=None) -> bytes:
    now = now or time.localtime()
    ts = time.strftime("%y%m%d%H%M%S", now) + "W"
    lines = [
        "/ISK5\\2MT382-1000",
        f"0-0:1.0.0({ts})",
        f"1-0:1.7.0({state.kw:07.3f}*kW)",
        f"1-0:1.8.1({state.kwh:010.3f}*kWh)",
        f"1-0:32.7.0({state.voltage:05.1f}*V)",
    ]
    body = "\r\n".join(lines) + "\r\n!"
    crc = crc16_arc(body.encode("ascii"))
    return (body + f"{crc:04X}" + "\r\n").encode("ascii")
```

Nota: `time.strftime` acepta una tupla de 9 elementos o `struct_time`; en el test usamos una tupla simple `(2026, 8, 13, 12, 0, 0, 0, 0, 0)` que Python convierte automáticamente.

- [ ] **Step 6: Correr y confirmar que pasan**

Run: `pytest test_dsmr.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Escribir tests que fallan para `parse_telegram` (válido e inválido)**

```python
# agregar a test_dsmr.py
def test_parse_telegram_roundtrip():
    state = MeterState(kw=2.345, voltage=230.4, kwh=671.578)
    raw = generate_telegram(state)
    fields = parse_telegram(raw)
    assert abs(fields["kw"] - 2.345) < 0.001
    assert abs(fields["voltage"] - 230.4) < 0.001
    assert abs(fields["kwh"] - 671.578) < 0.001


def test_parse_telegram_rejects_bad_checksum():
    state = MeterState(kw=1.0, voltage=230.0, kwh=10.0)
    raw = bytearray(generate_telegram(state))
    raw[-6] = ord("0") if chr(raw[-6]) != "0" else ord("1")  # altera un dígito del checksum
    try:
        parse_telegram(bytes(raw))
        assert False, "esperaba InvalidTelegram"
    except InvalidTelegram:
        pass
```

- [ ] **Step 8: Correr y confirmar que fallan**

Run: `pytest test_dsmr.py -v`
Expected: FAIL con `AttributeError`/`ImportError` sobre `parse_telegram`/`InvalidTelegram`

- [ ] **Step 9: Implementar `parse_telegram`**

```python
# agregar a dsmr.py
class InvalidTelegram(Exception):
    pass


_FIELD_PATTERNS = {
    "kw": re.compile(r"1-0:1\.7\.0\(([\d.]+)\*kW\)"),
    "kwh": re.compile(r"1-0:1\.8\.1\(([\d.]+)\*kWh\)"),
    "voltage": re.compile(r"1-0:32\.7\.0\(([\d.]+)\*V\)"),
}


def parse_telegram(raw: bytes) -> dict:
    text = raw.decode("ascii")
    if "!" not in text:
        raise InvalidTelegram("missing checksum marker")
    body, _, rest = text.rpartition("!")
    checksum_hex = rest.strip()[:4]
    try:
        expected = int(checksum_hex, 16)
    except ValueError:
        raise InvalidTelegram(f"bad checksum hex: {checksum_hex!r}")
    actual = crc16_arc((body + "!").encode("ascii"))
    if actual != expected:
        raise InvalidTelegram(f"checksum mismatch: got {actual:04X}, expected {expected:04X}")

    result = {}
    for key, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            raise InvalidTelegram(f"missing field: {key}")
        result[key] = float(match.group(1))
    return result
```

- [ ] **Step 10: Correr y confirmar que pasan**

Run: `pytest test_dsmr.py -v`
Expected: PASS (4 tests)

- [ ] **Step 11: Escribir test que falla para `TelegramReader` con framing partido entre chunks**

```python
# agregar a test_dsmr.py
def test_telegram_reader_handles_split_chunks():
    state = MeterState(kw=1.2, voltage=229.0, kwh=50.0)
    raw = generate_telegram(state)
    reader = TelegramReader()
    mid = len(raw) // 2
    assert reader.feed(raw[:mid]) == []
    telegrams = reader.feed(raw[mid:])
    assert len(telegrams) == 1
    assert parse_telegram(telegrams[0])["kw"] == 1.2


def test_telegram_reader_handles_two_telegrams_in_one_chunk():
    state = MeterState(kw=1.0, voltage=230.0, kwh=1.0)
    raw1 = generate_telegram(state)
    state.tick()
    raw2 = generate_telegram(state)
    reader = TelegramReader()
    telegrams = reader.feed(raw1 + raw2)
    assert len(telegrams) == 2
```

- [ ] **Step 12: Correr y confirmar que falla**

Run: `pytest test_dsmr.py -v`
Expected: FAIL con `AttributeError` sobre `TelegramReader`

- [ ] **Step 13: Implementar `TelegramReader`**

```python
# agregar a dsmr.py
_TELEGRAM_RE = re.compile(rb"/ISK5.*?\r\n!([0-9A-Fa-f]{4})\r\n", re.DOTALL)


class TelegramReader:
    def __init__(self):
        self._buffer = b""

    def feed(self, chunk: bytes) -> list:
        self._buffer += chunk
        telegrams = []
        while True:
            match = _TELEGRAM_RE.search(self._buffer)
            if not match:
                break
            telegrams.append(match.group(0))
            self._buffer = self._buffer[match.end():]
        return telegrams
```

- [ ] **Step 14: Correr todos los tests de `test_dsmr.py` y confirmar que pasan**

Run: `pytest test_dsmr.py -v`
Expected: PASS (6 tests)

- [ ] **Step 15: Commit**

```bash
git add requirements.txt dsmr.py test_dsmr.py
git commit -m "feat: nucleo DSMR (crc16, generacion y parseo de telegramas)"
```

---

### Task 2: Descubrimiento por Zeroconf (`discovery.py`)

**Files:**
- Create: `C:\Smart meter prototype\discovery.py`
- Test: `C:\Smart meter prototype\test_discovery.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces:
  - `get_local_ip() -> str`
  - `advertise_service(zc, service_type: str, instance_name: str, port: int) -> ServiceInfo`
  - `class ServiceWaiter: __init__(self, zc, service_type: str)`, `wait(self, timeout=None) -> tuple[str, int]` (lanza `TimeoutError` si no encuentra nada dentro del timeout)

- [ ] **Step 1: Escribir test que falla para `ServiceWaiter.wait` sin descubrir nada**

```python
# test_discovery.py
from discovery import ServiceWaiter


class _FakeZeroconf:
    pass


def test_service_waiter_times_out_when_nothing_found():
    waiter = ServiceWaiter(_FakeZeroconf(), "_nonexistent._tcp.local.")
    try:
        waiter.wait(timeout=0.2)
        assert False, "esperaba TimeoutError"
    except TimeoutError:
        pass
```

Nota: este test crea un `ServiceBrowser` real contra un `Zeroconf()` real y espera a que expire el timeout — usa la librería `zeroconf` de verdad (no mockeamos su protocolo interno), pero no depende de que exista hardware ni red externa, solo del socket multicast local. Ajustar `_FakeZeroconf` a instanciar `zeroconf.Zeroconf()` real si el fake no es aceptado por `ServiceBrowser`.

```python
# test_discovery.py (versión final, usa Zeroconf real)
from zeroconf import Zeroconf
from discovery import ServiceWaiter


def test_service_waiter_times_out_when_nothing_found():
    zc = Zeroconf()
    try:
        waiter = ServiceWaiter(zc, "_nonexistent._tcp.local.")
        try:
            waiter.wait(timeout=0.5)
            assert False, "esperaba TimeoutError"
        except TimeoutError:
            pass
    finally:
        zc.close()


def test_advertise_and_discover_roundtrip():
    zc = Zeroconf()
    try:
        from discovery import advertise_service
        info = advertise_service(zc, "_testmeter._tcp.local.", "test", 12345)
        waiter = ServiceWaiter(zc, "_testmeter._tcp.local.")
        ip, port = waiter.wait(timeout=5.0)
        assert port == 12345
    finally:
        zc.unregister_service(info)
        zc.close()
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `pytest test_discovery.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'discovery'`

- [ ] **Step 3: Implementar `discovery.py`**

```python
# discovery.py
import socket
import threading

from zeroconf import ServiceInfo, ServiceBrowser, Zeroconf


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def advertise_service(zc: Zeroconf, service_type: str, instance_name: str, port: int) -> ServiceInfo:
    ip = get_local_ip()
    info = ServiceInfo(
        service_type,
        f"{instance_name}.{service_type}",
        addresses=[socket.inet_aton(ip)],
        port=port,
    )
    zc.register_service(info)
    return info


class ServiceWaiter:
    """Bloquea hasta encontrar un servicio Zeroconf del tipo dado, retorna (ip, puerto)."""

    def __init__(self, zc: Zeroconf, service_type: str):
        self.zc = zc
        self.service_type = service_type
        self._found = threading.Event()
        self._address = None

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info and info.addresses and not self._found.is_set():
            ip = socket.inet_ntoa(info.addresses[0])
            self._address = (ip, info.port)
            self._found.set()

    def remove_service(self, zc, type_, name):
        pass

    def update_service(self, zc, type_, name):
        pass

    def wait(self, timeout=None) -> tuple:
        browser = ServiceBrowser(self.zc, self.service_type, self)
        found = self._found.wait(timeout=timeout)
        browser.cancel()
        if not found:
            raise TimeoutError(f"service {self.service_type} not found")
        return self._address
```

- [ ] **Step 4: Correr y confirmar que pasan**

Run: `pytest test_discovery.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add discovery.py test_discovery.py
git commit -m "feat: descubrimiento de servicios via Zeroconf"
```

---

### Task 3: Simulador de medidor (`meter_simulator.py`) — PC1

**Files:**
- Create: `C:\Smart meter prototype\meter_simulator.py`
- Test: `C:\Smart meter prototype\test_meter_simulator.py`

**Interfaces:**
- Consumes: `dsmr.MeterState`, `dsmr.generate_telegram`, `dsmr.TelegramReader`, `dsmr.parse_telegram`, `discovery.advertise_service`, `discovery.get_local_ip`
- Produces: `class MeterServer: __init__(self, port=3000)`, `start(self) -> None`, `client_count(self) -> int`, `stop(self) -> None`

- [ ] **Step 1: Escribir test que falla — un cliente TCP recibe telegramas válidos**

```python
# test_meter_simulator.py
import socket
import time

from meter_simulator import MeterServer
from dsmr import TelegramReader, parse_telegram


def test_meter_server_streams_valid_telegrams():
    server = MeterServer(port=13000)
    server.start()
    try:
        client = socket.create_connection(("127.0.0.1", 13000), timeout=5.0)
        reader = TelegramReader()
        telegrams = []
        deadline = time.time() + 5.0
        while len(telegrams) < 1 and time.time() < deadline:
            chunk = client.recv(4096)
            telegrams.extend(reader.feed(chunk))
        assert len(telegrams) >= 1
        fields = parse_telegram(telegrams[0])
        assert 0.0 <= fields["kw"] <= 5.0
        client.close()
    finally:
        server.stop()
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `pytest test_meter_simulator.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'meter_simulator'`

- [ ] **Step 3: Implementar `MeterServer` y `main()` con UI**

```python
# meter_simulator.py
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
```

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `pytest test_meter_simulator.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add meter_simulator.py test_meter_simulator.py
git commit -m "feat: simulador de medidor (PC1) con UI y advertise Zeroconf"
```

---

### Task 4: Relay de Raspberry Pi (`relay.py`)

**Files:**
- Create: `C:\Smart meter prototype\relay.py`
- Create: `C:\Smart meter prototype\relay.service`
- Create: `C:\Smart meter prototype\install_relay.sh`
- Test: `C:\Smart meter prototype\test_relay.py`

**Interfaces:**
- Consumes: `discovery.ServiceWaiter`, `discovery.advertise_service`
- Produces: `class RelayServer: __init__(self, port=4000)`, `start(self) -> None`, `broadcast(self, data: bytes) -> None`, `stop(self) -> None`; `connect_upstream(ip, port, retry_seconds=3.0) -> socket.socket`

- [ ] **Step 1: Escribir test que falla — `RelayServer.broadcast` reenvía a todos los clientes conectados**

```python
# test_relay.py
import socket
import time

from relay import RelayServer


def test_relay_server_broadcasts_to_all_clients():
    server = RelayServer(port=14000)
    server.start()
    try:
        c1 = socket.create_connection(("127.0.0.1", 14000), timeout=5.0)
        c2 = socket.create_connection(("127.0.0.1", 14000), timeout=5.0)
        time.sleep(0.2)  # deja que el accept loop registre ambos clientes
        server.broadcast(b"hola\r\n")
        assert c1.recv(100) == b"hola\r\n"
        assert c2.recv(100) == b"hola\r\n"
        c1.close()
        c2.close()
    finally:
        server.stop()
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `pytest test_relay.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'relay'`

- [ ] **Step 3: Implementar `relay.py`**

```python
# relay.py
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
```

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `pytest test_relay.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Crear `relay.service`**

```ini
[Unit]
Description=Smart Meter Relay
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/smartmeter/relay.py
WorkingDirectory=/opt/smartmeter
Restart=always
RestartSec=3
User=pi

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Crear `install_relay.sh`**

```bash
#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y python3-pip
sudo pip3 install zeroconf
sudo mkdir -p /opt/smartmeter
sudo cp relay.py discovery.py /opt/smartmeter/
sudo cp relay.service /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable --now relay
echo "Relay instalado y corriendo. Ver logs: journalctl -u relay -f"
```

- [ ] **Step 7: Commit**

```bash
git add relay.py test_relay.py relay.service install_relay.sh
git commit -m "feat: relay de Raspberry Pi con systemd service e instalador"
```

---

### Task 5: Dashboard en vivo (`dashboard.py`) — PC2

**Files:**
- Create: `C:\Smart meter prototype\dashboard.py`
- Test: `C:\Smart meter prototype\test_dashboard.py`

**Interfaces:**
- Consumes: `dsmr.TelegramReader`, `dsmr.parse_telegram`, `dsmr.InvalidTelegram`, `dsmr.generate_telegram`, `dsmr.MeterState`, `discovery.ServiceWaiter`
- Produces: `class DashboardState: update(self, fields: dict) -> None`, `snapshot(self) -> tuple[list, float, float, float]`

- [ ] **Step 1: Escribir test que falla — pipeline de parseo alimenta `DashboardState` correctamente, e ignora telegramas corruptos**

```python
# test_dashboard.py
from dashboard import DashboardState
from dsmr import TelegramReader, parse_telegram, InvalidTelegram, MeterState, generate_telegram


def test_dashboard_state_updates_from_valid_telegram():
    state = DashboardState()
    meter_state = MeterState(kw=1.8, voltage=231.0, kwh=99.0)
    raw = generate_telegram(meter_state)
    reader = TelegramReader()
    telegrams = reader.feed(raw)
    for t in telegrams:
        fields = parse_telegram(t)
        state.update(fields)
    values, voltage, kwh, last_update = state.snapshot()
    assert values[-1] == 1.8
    assert voltage == 231.0
    assert kwh == 99.0
    assert last_update > 0


def test_dashboard_ignores_corrupt_telegram():
    state = DashboardState()
    reader = TelegramReader()
    corrupt = b"/ISK5\\2MT382-1000\r\n0-0:1.0.0(260813120000W)\r\n!0000\r\n"
    telegrams = reader.feed(corrupt)
    for t in telegrams:
        try:
            fields = parse_telegram(t)
            state.update(fields)
        except InvalidTelegram:
            pass
    values, _voltage, _kwh, _last_update = state.snapshot()
    assert values == []
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `pytest test_dashboard.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'dashboard'`

- [ ] **Step 3: Implementar `dashboard.py`**

```python
# dashboard.py
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
                    except InvalidTelegram:
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
```

- [ ] **Step 4: Correr y confirmar que pasan**

Run: `pytest test_dashboard.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard.py test_dashboard.py
git commit -m "feat: dashboard en vivo (PC2)"
```

---

### Task 6: Empaquetado (.exe) y prueba end-to-end local, sin la Pi

**Files:**
- Create: `C:\Smart meter prototype\build_windows.ps1`

**Interfaces:**
- Consumes: `meter_simulator.py`, `dashboard.py`, `dsmr.py`, `discovery.py` (todo lo anterior)
- Produces: `dist\meter_simulator.exe`, `dist\dashboard.exe`

- [ ] **Step 1: Crear `build_windows.ps1`**

```powershell
pip install -r requirements.txt
pyinstaller --onefile --windowed --name meter_simulator meter_simulator.py
pyinstaller --onefile --windowed --name dashboard dashboard.py
Write-Host "Listo: dist\meter_simulator.exe y dist\dashboard.exe"
```

- [ ] **Step 2: Correr el build**

Run: `powershell -File build_windows.ps1`
Expected: se generan `dist\meter_simulator.exe` y `dist\dashboard.exe` sin errores

- [ ] **Step 3: Prueba manual end-to-end en la misma máquina (sin Pi física todavía)**

El dashboard busca el servicio `_smartmeter._tcp.local.`, que solo `relay.py` anuncia (el simulador anuncia `_metersim._tcp.local.`, uno distinto) — sin algo corriendo `relay.py` de por medio, el dashboard nunca encontrará al simulador. `relay.py` (Task 4) no tiene ninguna dependencia de hardware de la Pi (son sockets + zeroconf puros), así que para esta prueba corre como un tercer proceso local con Python normal, sin necesitar la Pi física:

1. Ejecutar `dist\meter_simulator.exe` — debe abrir una ventana "Simulando... 0 cliente(s) conectados". Si Windows Firewall pregunta, elegir "Permitir acceso".
2. En una terminal, `python relay.py` — proceso sin ventana; déjalo corriendo (si Windows Firewall pregunta, permitir acceso).
3. Ejecutar `dist\dashboard.exe` — debe mostrar "Buscando smart meter..." y, en unos segundos, cambiar a mostrar un número de kW en vivo y la gráfica moviéndose.
4. Confirmar que la ventana del simulador ahora dice "1 cliente(s) conectados" (el cliente es `relay.py`).
5. Cerrar `meter_simulator.exe` — el dashboard debe mostrar "Sin datos recientes" en menos de ~6s sin crashear, y `relay.py` no debe crashear (sigue reintentando encontrar al simulador).
6. Volver a abrir `meter_simulator.exe` — el dashboard debe recuperar datos en vivo sin reiniciar ni `relay.py` ni `dashboard.exe`.

Expected: los 6 puntos se cumplen. Si el paso 3 nunca encuentra el servicio, revisar que los tres procesos corran en la misma red/adaptador y que el firewall no esté bloqueando el tráfico UDP 5353 (mDNS) o los puertos TCP 3000/4000.

- [ ] **Step 4: Commit**

```bash
git add build_windows.ps1
git commit -m "build: empaquetado PyInstaller para PC1 y PC2"
```

---

### Task 7: Bring-up de la Raspberry Pi física (mañana, 2026-08-14)

**Files:** ninguno nuevo — usa `relay.py`, `discovery.py`, `relay.service`, `install_relay.sh` de Task 4.

Esta tarea es manual y depende de tener la Pi en mano. No es TDD porque no hay unidad de código nueva — es la validación de hardware real contra lo ya construido y probado en las Tasks 1–6.

- [ ] **Step 1: Flashear la SD**

Usar Raspberry Pi Imager con Raspberry Pi OS Lite (64-bit), preconfigurando WiFi (si aplica) y habilitando SSH desde el propio Imager.

- [ ] **Step 2: Primer boot y copia de archivos**

```bash
scp relay.py discovery.py relay.service install_relay.sh pi@<ip-de-la-pi>:~/
ssh pi@<ip-de-la-pi>
chmod +x install_relay.sh
./install_relay.sh
```

- [ ] **Step 3: Verificar que el relay encuentra al simulador**

Con `meter_simulator.exe` corriendo en PC1 (misma red que la Pi):

```bash
journalctl -u relay -f
```

Expected: logs muestran conexión al simulador sin excepciones no controladas (si `wait_for_meter` no encuentra el servicio, reintentará cada 3s indefinidamente — esperado hasta que ambos estén en la misma red).

- [ ] **Step 4: Prueba end-to-end completa con hardware real**

Levantar `dashboard.exe` en PC2 (misma red) y confirmar datos en vivo llegando a través de la Pi física.

- [ ] **Step 5: Prueba de resiliencia — caída de PC1**

Apagar `meter_simulator.exe` con la cadena completa corriendo. Confirmar (vía `journalctl -u relay -f` y el dashboard) que ni el relay ni el dashboard crashean, y que ambos se recuperan solos al volver a levantar el simulador.

- [ ] **Step 6: Prueba de resiliencia — Pi pierde red**

Desconectar el cable/WiFi de la Pi 10s y reconectar. Confirmar que el dashboard se recupera sin reiniciar `dashboard.exe`.

- [ ] **Step 7: Prueba en la red real de la oficina**

Repetir Steps 3–4 en el WiFi real que se usará en la demo (no un hotspot de laptop). Si el discovery falla aquí pero funcionó en una red doméstica, revisar "client isolation" en el AP — es la causa más común de que Zeroconf no funcione en redes de oficina.

- [ ] **Step 8: Commit de cualquier ajuste hecho durante el bring-up**

```bash
git add -A
git commit -m "fix: ajustes tras bring-up con Raspberry Pi fisica"
```

(Solo si Steps 1–7 requirieron cambios de código; si todo funcionó tal cual, no hay nada que commitear en este paso.)

---

## Self-Review

- **Cobertura del spec:** formato de telegrama (Task 1), simulador+advertise (Task 3), relay dumb pass-through+advertise (Task 4), dashboard+parseo+buffer en RAM (Task 5), descubrimiento Zeroconf sin hostname `.local` (Task 2), manejo de errores/reconexión (Tasks 4 y 5, probado en Task 7), empaquetado .exe (Task 6), plan de pruebas riguroso de mañana (Task 7) — todo cubierto.
- **Placeholders:** ninguno; todos los pasos incluyen código completo o comandos exactos.
- **Consistencia de tipos:** `MeterState`, `generate_telegram`, `parse_telegram`, `TelegramReader`, `ServiceWaiter`, `advertise_service`, `RelayServer`, `DashboardState` se usan con la misma firma en todas las tasks que los consumen.
