# test_discovery.py (versión final, usa Zeroconf real)
import socket

from zeroconf import Zeroconf
from discovery import ServiceWaiter, get_local_ip


def test_get_local_ip_falls_back_when_no_default_route(monkeypatch):
    class FakeSocket:
        def connect(self, addr):
            raise OSError("network unreachable")

        def getsockname(self):
            raise AssertionError("should not be called after connect fails")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSocket())
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "127.0.0.1")
    assert get_local_ip() == "127.0.0.1"


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
