import socket
import threading
import time

from zeroconf import Zeroconf

from meter_simulator import MeterServer
from relay import RelayServer, connect_upstream
from discovery import ServiceWaiter, advertise_service
from dsmr import TelegramReader, parse_telegram

TEST_UPSTREAM = "_metersimtest._tcp.local."
TEST_DOWNSTREAM = "_smartmetertest._tcp.local."


def test_full_chain_simulator_relay_dashboard():
    zc = Zeroconf()
    meter = MeterServer(port=23000)
    meter.start()
    meter_info = advertise_service(zc, TEST_UPSTREAM, "meter-e2e", 23000)

    relay = RelayServer(port=24000)
    relay.start()
    relay_info = advertise_service(zc, TEST_DOWNSTREAM, "smartmeter-e2e", 24000)

    def bridge():
        waiter = ServiceWaiter(zc, TEST_UPSTREAM)
        ip, port = waiter.wait(timeout=10.0)
        upstream = connect_upstream(ip, port)
        while True:
            chunk = upstream.recv(4096)
            if not chunk:
                break
            relay.broadcast(chunk)

    threading.Thread(target=bridge, daemon=True).start()

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
        meter.stop()
        relay.stop()
        zc.unregister_service(meter_info)
        zc.unregister_service(relay_info)
        zc.close()
