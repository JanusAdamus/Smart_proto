# test_integration_e2e.py
import socket
import threading
import time

import serial
from zeroconf import Zeroconf

from meter_simulator import MeterSerialWriter
from relay import RelayServer, serial_reader_loop
from discovery import ServiceWaiter, advertise_service, connect_to_service
from dsmr import TelegramReader, parse_telegram

TEST_DOWNSTREAM = "_smartmetertest._tcp.local."


def test_full_chain_serial_meter_to_relay_to_dashboard():
    shared_ser = serial.serial_for_url("loop://", timeout=1)

    writer = MeterSerialWriter(serial_factory=lambda: [shared_ser])
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
        ips, port = waiter.wait(timeout=10.0)
        client = connect_to_service(ips, port, timeout=2.0)
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
