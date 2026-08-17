# discovery.py
import socket
import threading

import ifaddr
from zeroconf import ServiceInfo, ServiceBrowser, Zeroconf


def get_local_ips() -> list:
    """IPv4 de todas las interfaces menos loopback.

    No se puede preguntar "cual es MI ip" con un connect() a internet: sin ruta
    por defecto (WiFi apagada, cable directo) eso falla y el fallback devuelve
    127.0.1.1, que deja el servicio anunciado con una IP inalcanzable.
    """
    ips = [
        i.ip
        for a in ifaddr.get_adapters()
        for i in a.ips
        if i.is_IPv4 and not i.ip.startswith("127.")
    ]
    return ips or ["127.0.0.1"]


def advertise_service(zc: Zeroconf, service_type: str, instance_name: str, port: int) -> ServiceInfo:
    ips = get_local_ips()
    print(f"advertising {instance_name} on {ips} port {port}")
    info = ServiceInfo(
        service_type,
        f"{instance_name}.{service_type}",
        addresses=[socket.inet_aton(ip) for ip in ips],
        port=port,
    )
    zc.register_service(info)
    return info


def connect_to_service(ips, port, timeout=5.0) -> socket.socket:
    """Conecta a la primera IP anunciada que responda. Lanza OSError si ninguna."""
    last = None
    for ip in ips:
        try:
            sock = socket.create_connection((ip, port), timeout=timeout)
            print(f"connected to {ip}:{port}")
            return sock
        except OSError as e:
            print(f"cannot reach {ip}:{port}: {e}")
            last = e
    raise last or OSError("no addresses advertised")


class ServiceWaiter:
    """Bloquea hasta encontrar un servicio Zeroconf del tipo dado, retorna (ips, puerto).

    Devuelve todas las IPs anunciadas: el que se conecta prueba una por una,
    porque desde afuera no se sabe cual interfaz del servidor es alcanzable.
    """

    def __init__(self, zc: Zeroconf, service_type: str):
        self.zc = zc
        self.service_type = service_type
        self._found = threading.Event()
        self._address = None

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info and info.addresses and not self._found.is_set():
            ips = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]
            if not ips:
                return
            self._address = (ips, info.port)
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
