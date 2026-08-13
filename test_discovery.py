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
