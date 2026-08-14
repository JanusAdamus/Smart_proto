import socket
import threading
import time

import serial

from relay import RelayServer, serial_reader_loop


def test_relay_server_broadcasts_to_all_clients():
    server = RelayServer(port=14000)
    server.start()
    try:
        c1 = socket.create_connection(("127.0.0.1", 14000), timeout=5.0)
        c2 = socket.create_connection(("127.0.0.1", 14000), timeout=5.0)
        time.sleep(0.2)  # deja que el accept loop registre ambos clientes
        server.broadcast(b"hola\r\n")
        assert c1.recv(100) == b"hola\r\n"
        assert c2.recv(100) == b"hola\r\n"
        c1.close()
        c2.close()
    finally:
        server.stop()


def test_serial_reader_loop_forwards_bytes_to_relay_clients():
    ser = serial.serial_for_url("loop://", timeout=1)
    relay = RelayServer(port=14001)
    relay.start()
    try:
        client = socket.create_connection(("127.0.0.1", 14001), timeout=5.0)
        time.sleep(0.2)
        threading.Thread(
            target=serial_reader_loop,
            args=(relay,),
            kwargs={"serial_factory": lambda: ser},
            daemon=True,
        ).start()
        ser.write(b"hola\r\n")
        assert client.recv(100) == b"hola\r\n"
        client.close()
    finally:
        relay.stop()
        ser.close()
