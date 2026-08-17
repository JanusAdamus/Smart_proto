import os
import time

import serial
import serial.tools.list_ports

BAUDRATE = 115200
TELEGRAM_START = b"/"


def carries_telegrams(ser, probe_seconds=3.0) -> bool:
    """True si el puerto emite un inicio de telegrama DSMR dentro del plazo."""
    deadline = time.time() + probe_seconds
    while time.time() < deadline:
        if TELEGRAM_START in ser.read(256):
            return True
    return False


def open_meter_port(baudrate=BAUDRATE, probe_seconds=3.0):
    """Abre el puerto que realmente esta emitiendo telegramas, o None.

    Elegir por nombre o por orden no sirve: la Pi lista ttyAMA0, ttyS0 y los
    ttyUSB* que haya enchufados, y cual de esos es el medidor depende de como
    quedo el cableado. Se prueban todos y gana el primero que manda un '/'.
    SMARTMETER_PORT saltea la prueba cuando hace falta forzar uno a mano.
    """
    forced = os.environ.get("SMARTMETER_PORT")
    devices = [forced] if forced else [p.device for p in serial.tools.list_ports.comports()]
    for device in devices:
        try:
            ser = serial.Serial(device, baudrate=baudrate, timeout=0.5)
        except OSError as e:
            print(f"cannot open {device}: {e}")
            continue
        if forced or carries_telegrams(ser, probe_seconds):
            print(f"meter data found on {device}")
            ser.timeout = 5.0
            return ser
        print(f"no meter data on {device}")
        ser.close()
    return None


def wait_and_open(baudrate=BAUDRATE, poll_seconds=2.0, probe_seconds=3.0):
    """Lado receptor (la Pi): espera hasta encontrar el puerto que emite."""
    while True:
        ser = open_meter_port(baudrate, probe_seconds)
        if ser:
            return ser
        print("no serial port is sending telegrams, retrying")
        time.sleep(poll_seconds)


def port_still_present(port) -> bool:
    """True si el sistema todavia lista ese puerto.

    Windows no falla el write() de un adaptador desenchufado: los bytes se van
    al buffer del driver y el handle sigue valido, asi que un error de escritura
    nunca llega. Preguntar por la lista es la unica forma de enterarse.
    """
    return any(p.device == port for p in serial.tools.list_ports.comports())


def open_first_port(baudrate=BAUDRATE, poll_seconds=2.0):
    """Lado emisor (el generador): abre el primer puerto que se deje.

    Aca no se puede sondear: el generador escribe, no recibe, asi que no hay
    trafico entrante que delate cual es el correcto. SMARTMETER_PORT decide si
    la maquina tiene mas de un adaptador.
    """
    while True:
        forced = os.environ.get("SMARTMETER_PORT")
        devices = [forced] if forced else [p.device for p in serial.tools.list_ports.comports()]
        for device in devices:
            try:
                ser = serial.Serial(device, baudrate=baudrate, timeout=5.0)
                print(f"writing to {device}")
                return ser
            except OSError as e:
                print(f"cannot open {device}: {e}")
        time.sleep(poll_seconds)
