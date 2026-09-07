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


def test_keep_advertised_reannounces_when_an_interface_appears(monkeypatch):
    # Al bootear, el relay puede arrancar antes que eth0 y anunciarse sin la IP
    # del cable; sin este re-anuncio nadie lo encuentra hasta reiniciarlo.
    import discovery

    class FakeZc:
        def __init__(self):
            self.registered = []
            self.unregistered = []
            self.closed = False

        def register_service(self, info):
            self.registered.append(info)

        def unregister_service(self, info):
            self.unregistered.append(info)

        def close(self):
            self.closed = True

    current = {"ips": ["192.168.0.9"]}
    monkeypatch.setattr(discovery, "get_local_ips", lambda: list(current["ips"]))

    rounds = {"n": 0}

    def fake_sleep(_seconds):
        rounds["n"] += 1
        if rounds["n"] == 1:
            current["ips"] = ["192.168.0.9", "192.168.50.1"]  # sube eth0
        else:
            raise KeyboardInterrupt  # corta el bucle infinito

    monkeypatch.setattr(discovery.time, "sleep", fake_sleep)

    instances = []

    def fake_zeroconf():
        zc = FakeZc()
        instances.append(zc)
        return zc

    try:
        discovery.keep_advertised(
            "_testmeter._tcp.local.",
            "test",
            4000,
            poll_seconds=0,
            zeroconf_factory=fake_zeroconf,
        )
    except KeyboardInterrupt:
        pass
    assert len(instances) == 2, "no recreo Zeroconf al aparecer eth0"
    assert instances[0].closed, "no cerro el Zeroconf atado a la interfaz vieja"
    assert instances[0].unregistered == instances[0].registered
    assert socket.inet_aton("192.168.50.1") in instances[-1].registered[0].addresses


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
