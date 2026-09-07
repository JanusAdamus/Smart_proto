import base64
import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import ifaddr

from dsmr import TelegramReader, parse_telegram, InvalidTelegram

APP_VERSION = "4.0 P1"
METER_PORT = 4000
KNOWN_HOSTS = (
    "192.168.50.1",   # subred que configura install_relay.sh
    "169.254.50.1",   # respaldo link-local/APIPA
)
BUFFER_SIZE = 300
GRAPH_MIN_KW = 0.5
GRAPH_MAX_KW = 2.5
GRAPH_TICKS_KW = (0.5, 1.0, 1.5, 2.0, 2.5)


def parse_ethernet_adapters_json(text):
    """Normaliza la salida JSON de PowerShell para uno o varios adaptadores."""
    if not text or not text.strip():
        return []
    raw = json.loads(text)
    if isinstance(raw, dict):
        raw = [raw]
    adapters = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        adapters.append(
            {
                "index": index,
                "name": str(item.get("name") or "Ethernet"),
                "description": str(item.get("description") or ""),
                "pnp_device_id": str(item.get("pnp_device_id") or ""),
            }
        )
    return adapters


def list_ethernet_adapters(runner=None):
    """Obtiene adaptadores Ethernet fisicos y conectados; no modifica Windows."""
    if runner is None:
        runner = subprocess.run
    script = r"""
$ErrorActionPreference = 'Stop'
Get-NetAdapter -Physical -ErrorAction Stop |
    Where-Object {
        $_.Status -eq 'Up' -and
        ("$($_.Name) $($_.InterfaceDescription)" -notmatch '(?i)wi-?fi|wireless|wlan|bluetooth|vpn|wireguard|openvpn')
    } |
    Select-Object @{N='index';E={$_.ifIndex}},
                  @{N='name';E={$_.Name}},
                  @{N='description';E={$_.InterfaceDescription}},
                  @{N='pnp_device_id';E={$_.PnPDeviceID}} |
    ConvertTo-Json -Compress
""".strip()
    completed = runner(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "error desconocido").strip()
        raise RuntimeError(f"Windows no pudo detectar el adaptador Ethernet: {detail}")
    return parse_ethernet_adapters_json(completed.stdout)


def choose_direct_cable_adapter(adapters):
    """Elige el USB-Ethernet de forma conservadora para no tocar otra red."""
    adapters = list(adapters)
    if not adapters:
        raise RuntimeError(
            "No hay un adaptador Ethernet conectado. Conecta el cable de la Pi "
            "al adaptador USB-Ethernet y vuelve a intentarlo."
        )

    usb_adapters = []
    usb_markers = ("usb\\", " usb ", "usb-", "usb ", "ax881", "rtl815")
    for adapter in adapters:
        signature = " ".join(
            (
                adapter.get("name", ""),
                adapter.get("description", ""),
                adapter.get("pnp_device_id", ""),
            )
        ).lower()
        if any(marker in signature for marker in usb_markers):
            usb_adapters.append(adapter)

    if len(usb_adapters) == 1:
        return usb_adapters[0]
    if not usb_adapters:
        names = ", ".join(adapter.get("name", "Ethernet") for adapter in adapters)
        raise RuntimeError(
            "No se encontró el adaptador USB-Ethernet conectado a la Pi "
            f"({names}). Revisa el adaptador y el cable."
        )

    names = ", ".join(adapter.get("name", "Ethernet") for adapter in adapters)
    raise RuntimeError(
        "Hay más de un adaptador Ethernet activo y no es seguro adivinar cuál "
        f"va a la Pi ({names}). Desconecta los otros cables Ethernet y reintenta."
    )


def _direct_cable_address_commands(index_expression):
    return f"""
$idx = [int]({index_expression})
Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Disabled -PolicyStore ActiveStore
$addresses = @(
    [PSCustomObject]@{{ IP = '192.168.50.2'; Prefix = 24 }},
    [PSCustomObject]@{{ IP = '169.254.50.2'; Prefix = 16 }}
)
foreach ($item in $addresses) {{
    $existing = Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -IPAddress $item.IP -ErrorAction SilentlyContinue
    if ($null -eq $existing) {{
        New-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -IPAddress $item.IP -PrefixLength $item.Prefix -PolicyStore ActiveStore | Out-Null
    }}
}}
""".strip()


def direct_cable_config_script(interface_index):
    """Crea la configuracion IP temporal para una interfaz ya identificada."""
    interface_index = int(interface_index)
    commands = _direct_cable_address_commands(str(interface_index))
    return "$ErrorActionPreference = 'Stop'\n" + commands


def automatic_direct_cable_config_script():
    """Detecta y configura el adaptador dentro del proceso elevado."""
    detection = r"""
$ErrorActionPreference = 'Stop'
$adapters = @(Get-NetAdapter -Physical -ErrorAction Stop | Where-Object {
    $_.Status -eq 'Up' -and
    ("$($_.Name) $($_.InterfaceDescription)" -notmatch '(?i)wi-?fi|wireless|wlan|bluetooth|vpn|wireguard|openvpn')
})
if ($adapters.Count -eq 0) { exit 20 }
$usbAdapters = @($adapters | Where-Object {
    "$($_.Name) $($_.InterfaceDescription) $($_.PnPDeviceID)" -match '(?i)USB\\|(^|[ _-])USB([ _-]|$)|AX881|RTL815'
})
if ($usbAdapters.Count -eq 0) { exit 20 }
if ($usbAdapters.Count -gt 1) { exit 21 }
$selected = $usbAdapters[0]
""".strip()
    return detection + "\n" + _direct_cable_address_commands("$selected.ifIndex")


def run_elevated_powershell(script, runner=None):
    """Ejecuta un script con UAC sin crear archivos temporales ni abrir consola."""
    if runner is None:
        runner = subprocess.run
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    environment = os.environ.copy()
    environment["SMART_METER_CABLE_SETUP"] = encoded
    launcher = (
        "$ErrorActionPreference = 'Stop'; "
        "$process = Start-Process -FilePath 'powershell.exe' -Verb RunAs "
        "-Wait -PassThru -WindowStyle Hidden "
        "-ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand',"
        "$env:SMART_METER_CABLE_SETUP); "
        "exit $process.ExitCode"
    )
    completed = runner(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", launcher],
        capture_output=True,
        text=True,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        if completed.returncode == 20:
            raise RuntimeError(
                "No se encontró el adaptador USB-Ethernet conectado a la Pi. "
                "Revisa el adaptador y el cable, y vuelve a intentarlo."
            )
        if completed.returncode == 21:
            raise RuntimeError(
                "Hay más de un adaptador Ethernet activo y no es seguro "
                "adivinar cuál va a la Pi. Desconecta los otros cables "
                "Ethernet y reintenta."
            )
        detail = (completed.stderr or completed.stdout or "").strip()
        if detail:
            detail = f" Detalle: {detail}"
        raise RuntimeError(
            "No se aplicó la configuración. Acepta el permiso de administrador "
            f"de Windows e inténtalo otra vez.{detail}"
        )


def configure_direct_cable():
    try:
        adapter = choose_direct_cable_adapter(list_ethernet_adapters())
    except (RuntimeError, json.JSONDecodeError):
        # Algunas politicas empresariales bloquean incluso la consulta de red
        # para procesos normales. En ese caso se detecta dentro del mismo UAC.
        run_elevated_powershell(automatic_direct_cable_config_script())
        return {"name": "adaptador USB-Ethernet"}
    run_elevated_powershell(direct_cable_config_script(adapter["index"]))
    return adapter


def format_energy_wh(kwh):
    return f"{kwh * 1000:,.0f} Wh"


def format_energy_kwh(kwh):
    return f"{kwh:,.3f} kWh"


def format_meter_timestamp(timestamp):
    if not timestamp or len(timestamp) < 12:
        return "--"
    try:
        parsed = time.strptime(timestamp[:12], "%y%m%d%H%M%S")
    except ValueError:
        return timestamp
    return time.strftime("%Y-%m-%d %H:%M:%S", parsed)


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
            prefix = getattr(ip, "network_prefix", 24)
            if (
                not ip.is_IPv4
                or ip.ip.startswith(("127.", "169.254."))
                or prefix >= 31
            ):
                continue
            gateway = ip.ip.rsplit(".", 1)[0] + ".1"
            if gateway != ip.ip and gateway not in hosts:
                hosts.append(gateway)
    for known in KNOWN_HOSTS:
        if known not in hosts:
            hosts.append(known)
    return tuple((host, port) for host in hosts)


def local_subnet_endpoints(port=METER_PORT):
    """Hosts del segmento local que pueden tener la Pi en una IP DHCP cualquiera.

    Se limita cada interfaz privada a un maximo de /24 para mantener el sondeo
    rapido y se excluyen loopback, link-local y adaptadores /31-/32. La ruta
    link-local fija sigue cubierta por KNOWN_HOSTS.
    """
    endpoints = []
    seen = set()
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            try:
                address = ipaddress.ip_address(ip.ip)
                prefix = int(getattr(ip, "network_prefix", 24))
            except ValueError:
                continue
            if (
                not address.is_private
                or address.is_loopback
                or address.is_link_local
                or prefix >= 31
            ):
                continue
            scan_prefix = max(prefix, 24)
            network = ipaddress.ip_network(f"{address}/{scan_prefix}", strict=False)
            for host in network.hosts():
                host_text = str(host)
                if host != address and host_text not in seen:
                    seen.add(host_text)
                    endpoints.append((host_text, port))
    return tuple(endpoints)


def source_bound_attempts(endpoints):
    """Combina cada destino con las IPv4 locales que tienen ruta directa."""
    local_networks = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            try:
                address = ipaddress.ip_address(ip.ip)
                prefix = int(getattr(ip, "network_prefix", 24))
                network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
            except ValueError:
                continue
            if not address.is_loopback:
                local_networks.append((address, network))

    attempts = []
    seen = set()
    for host, port in endpoints:
        try:
            target = ipaddress.ip_address(host)
        except ValueError:
            continue
        for source, network in local_networks:
            attempt = (host, port, str(source))
            if target in network and attempt not in seen:
                seen.add(attempt)
                attempts.append(attempt)
    return tuple(attempts)


def connect_to_any_endpoint(attempts, timeout):
    """Prueba destinos IP en paralelo, opcionalmente ligados a una interfaz."""
    attempts = tuple(attempts)
    if not attempts:
        raise OSError("no local addresses available to scan")

    def try_endpoint(attempt):
        host, port, *source = attempt
        try:
            if source:
                sock = socket.create_connection(
                    (host, port),
                    timeout=timeout,
                    source_address=(source[0], 0),
                )
            else:
                sock = socket.create_connection((host, port), timeout=timeout)
            return sock, (host, port, source[0] if source else None)
        except OSError:
            return None

    winner = None
    max_workers = min(64, len(attempts))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(try_endpoint, attempt) for attempt in attempts]
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue
            if winner is None:
                winner = result
            else:
                result[0].close()

    if winner is None:
        raise OSError(f"no meter found in {len(attempts)} IP/interface attempts")
    sock, (host, port, source) = winner
    route = f" via {source}" if source else ""
    return sock, f"{host}:{port}{route}"


class DashboardState:
    def __init__(self):
        self.kw = deque(maxlen=BUFFER_SIZE)
        self.voltage = 0.0
        self.kwh = 0.0
        self.last_update = 0.0
        self.received_count = 0
        self.received_log = deque(maxlen=20)
        self.latest_telegram = ""
        self.meter_timestamp = ""
        self.connection_status = "Connecting to the Pi automatically..."
        self.lock = threading.Lock()

    def update(self, fields: dict, raw=None):
        with self.lock:
            self.kw.append(fields["kw"])
            self.voltage = fields["voltage"]
            self.kwh = fields["kwh"]
            self.last_update = time.time()
            self.received_count += 1
            self.received_log.append(f"Received #{self.received_count}: {fields['kw']:.3f} kW")
            self.meter_timestamp = fields.get("timestamp", self.meter_timestamp)
            if raw is not None:
                self.latest_telegram = raw.decode("ascii").rstrip("\r\n")

    def snapshot(self):
        with self.lock:
            return list(self.kw), self.voltage, self.kwh, self.last_update

    def log_snapshot(self):
        with self.lock:
            return list(self.received_log)

    def latest_telegram_snapshot(self):
        with self.lock:
            return self.latest_telegram

    def metadata_snapshot(self):
        with self.lock:
            return self.received_count, list(self.received_log), self.meter_timestamp

    def set_connection_status(self, text):
        with self.lock:
            self.connection_status = text

    def connection_status_snapshot(self):
        with self.lock:
            return self.connection_status


def connect_to_meter(
    endpoints=None,
    timeout=0.8,
    scan_endpoints=None,
    scan_timeout=0.2,
    on_status=None,
):
    """Conecta por IP mediante el cable Ethernet directo.

    Prueba primero las rutas normales y despues liga cada intento a una IPv4
    local compatible. Esto fuerza a Windows a usar el adaptador USB-Ethernet
    correcto cuando hay varias interfaces 169.254.x.x.
    """
    automatic = endpoints is None
    if automatic:
        endpoints = candidate_endpoints()
    errors = []
    for host, port in endpoints:
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            return sock, f"{host}:{port}"
        except OSError as e:
            errors.append(f"{host}: {e}")

    if automatic:
        bound_endpoints = source_bound_attempts(endpoints)
        if on_status:
            on_status(
                f"Known IPs unreachable; trying {len(bound_endpoints)} direct-cable routes..."
            )
        if bound_endpoints:
            try:
                return connect_to_any_endpoint(bound_endpoints, timeout=timeout)
            except OSError as bound_error:
                errors.append(str(bound_error))

    should_scan = automatic or scan_endpoints is not None
    if should_scan:
        if scan_endpoints is None:
            scan_endpoints = source_bound_attempts(local_subnet_endpoints())
        if on_status:
            on_status(f"Scanning {len(scan_endpoints)} direct-cable IP routes...")
        try:
            return connect_to_any_endpoint(scan_endpoints, timeout=scan_timeout)
        except OSError as scan_error:
            errors.append(str(scan_error))

    raise OSError("Pi unreachable; " + "; ".join(errors))


def reader_thread(state: DashboardState):
    while True:
        state.set_connection_status("Connecting to the Pi automatically...")
        try:
            sock, endpoint = connect_to_meter(on_status=state.set_connection_status)
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
                    state.update(fields, raw)
        except OSError:
            pass
        finally:
            sock.close()
            state.set_connection_status("Connection lost; retrying...")


class SmartMeterDashboardUI:
    def __init__(self, root, state):
        self.root = root
        self.state = state
        root.title(f"Smart Meter Dashboard {APP_VERSION}")
        root.geometry("760x780")
        root.minsize(680, 700)
        self._build()
        self._refresh()

    def _build(self):
        tk.Label(
            self.root,
            text="Smart Meter Dashboard",
            font=("Segoe UI", 20, "bold"),
        ).pack(pady=(14, 2))
        self.status_label = tk.Label(
            self.root,
            text="Connecting to the Pi automatically...",
            fg="#9a6700",
            font=("Segoe UI", 11, "bold"),
            wraplength=710,
            justify=tk.CENTER,
        )
        self.status_label.pack(pady=(0, 12))

        summary = tk.LabelFrame(self.root, text="Current meter reading", font=("Segoe UI", 10))
        summary.pack(fill=tk.X, padx=14, pady=(0, 10))
        for column in range(4):
            summary.grid_columnconfigure(column, weight=1)
        self.energy_label = self._value(summary, 0, "Accumulated energy", "-- kWh")
        self.power_label = self._value(summary, 1, "Current power", "-- kW")
        self.voltage_label = self._value(summary, 2, "Voltage", "-- V")
        self.sequence_label = self._value(summary, 3, "Telegram", "#--")
        self.clock_label = tk.Label(
            summary,
            text="Meter time: --",
            fg="#555555",
            font=("Consolas", 9),
        )
        self.clock_label.grid(row=1, column=0, columnspan=4, pady=(0, 9))

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

        graph_frame = tk.LabelFrame(
            self.root,
            text="Power - last 60 seconds (kW)",
            font=("Segoe UI", 10),
        )
        graph_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        self.graph = tk.Canvas(
            graph_frame,
            height=190,
            bg="white",
            highlightthickness=1,
            highlightbackground="#b8b8b8",
        )
        self.graph.pack(fill=tk.BOTH, expand=True, padx=7, pady=7)

        log_frame = tk.LabelFrame(self.root, text="Reception log", font=("Segoe UI", 10))
        log_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        self.log_box = tk.Listbox(log_frame, height=6, font=("Consolas", 9))
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=7, pady=7)

        tk.Label(
            self.root,
            text="DSMR 5.0  |  Raspberry Pi TCP link  |  1-second update interval",
            fg="#666666",
            font=("Segoe UI", 9),
        ).pack(pady=(0, 12))

    @staticmethod
    def _value(parent, column, title, initial):
        frame = tk.Frame(parent)
        frame.grid(row=0, column=column, sticky="nsew", padx=7, pady=(9, 5))
        tk.Label(frame, text=title, fg="#555555", font=("Segoe UI", 9)).pack()
        label = tk.Label(frame, text=initial, font=("Consolas", 14, "bold"))
        label.pack(pady=(3, 0))
        return label

    def _draw_graph(self, values):
        self.graph.delete("all")
        width = max(self.graph.winfo_width(), 120)
        height = max(self.graph.winfo_height(), 100)
        left, right, top, bottom = 36, 10, 10, 24
        plot_width = width - left - right
        plot_height = height - top - bottom
        graph_range = GRAPH_MAX_KW - GRAPH_MIN_KW

        for value in GRAPH_TICKS_KW:
            y = top + (GRAPH_MAX_KW - value) * plot_height / graph_range
            self.graph.create_line(left, y, width - right, y, fill="#dddddd")
            self.graph.create_text(left - 8, y, text=f"{value:.1f}", anchor="e", fill="#666666")
        self.graph.create_line(left, top, left, height - bottom, fill="#777777")
        self.graph.create_line(left, height - bottom, width - right, height - bottom, fill="#777777")

        visible = values[-60:]
        if len(visible) < 2:
            return
        points = []
        for index, value in enumerate(visible):
            x = left + index * plot_width / (len(visible) - 1)
            bounded = min(GRAPH_MAX_KW, max(GRAPH_MIN_KW, value))
            y = top + (GRAPH_MAX_KW - bounded) * plot_height / graph_range
            points.extend((x, y))
        self.graph.create_line(*points, fill="#1f6fb2", width=2, smooth=True)
        x, y = points[-2], points[-1]
        self.graph.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#1f6fb2", outline="")

    def _refresh(self):
        values, voltage, kwh, last_update = self.state.snapshot()
        count, log, timestamp = self.state.metadata_snapshot()
        connection_status = self.state.connection_status_snapshot()
        self.status_label.configure(
            text=connection_status,
            fg="#237a36" if connection_status.startswith("Connected") else "#9a6700",
        )

        if values:
            self.energy_label.configure(text=format_energy_kwh(kwh))
            self.power_label.configure(text=f"{values[-1]:.3f} kW")
            self.voltage_label.configure(text=f"{voltage:.1f} V")
            self.sequence_label.configure(text=f"#{count:,}")
            self.clock_label.configure(text=f"Meter time: {format_meter_timestamp(timestamp)}")
            if time.time() - last_update > 5.0:
                self.status_label.configure(text="Connected, but no recent meter data", fg="#9a6700")

        self.log_box.delete(0, tk.END)
        for entry in log:
            self.log_box.insert(tk.END, entry)
        self.log_box.yview_moveto(1.0)

        self.telegram_box.configure(state=tk.NORMAL)
        self.telegram_box.delete("1.0", tk.END)
        self.telegram_box.insert("1.0", self.state.latest_telegram_snapshot().replace("\r\n", "\n"))
        self.telegram_box.configure(state=tk.DISABLED)
        self._draw_graph(values)
        self.root.after(1000, self._refresh)


def main():
    try:
        state = DashboardState()
        threading.Thread(target=reader_thread, args=(state,), daemon=True).start()
        root = tk.Tk()
        SmartMeterDashboardUI(root, state)
        root.mainloop()
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Smart Meter Dashboard - Error", str(error))
        raise


if __name__ == "__main__":
    main()
