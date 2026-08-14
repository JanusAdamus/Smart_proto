import socket
import time

from relay import RelayServer


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
