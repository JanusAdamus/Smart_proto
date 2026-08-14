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
