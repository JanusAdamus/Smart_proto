# test_discovery.py (versión final, usa Zeroconf real)
import socket

from zeroconf import Zeroconf
from discovery import ServiceWaiter, get_local_ips


def test_get_local_ips_never_returns_loopback():
    # Sin ruta por defecto el metodo viejo devolvia 127.0.1.1 y el servicio
    # quedaba anunciado con una IP a la que nadie se puede conectar.
    ips = get_local_ips()
    assert ips
    assert not any(ip.startswith("127.") for ip in ips)


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
        ips, port = waiter.wait(timeout=5.0)
        assert port == 12345
        assert ips and not any(ip.startswith("127.") for ip in ips)
    finally:
        zc.unregister_service(info)
        zc.close()
