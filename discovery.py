# discovery.py
import socket
import threading

from zeroconf import ServiceInfo, ServiceBrowser, Zeroconf


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
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
