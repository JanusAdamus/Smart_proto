import os
import time

import serial
import serial.tools.list_ports

BAUDRATE = 115200
TELEGRAM_START = b"/"


def candidate_devices(prefer_usb=False) -> list:
    """Puertos a probar. SMARTMETER_PORT saltea toda la eleccion.

    prefer_usb solo para el generador, que no puede sondear: comports() en
    Windows tambien lista puertos Bluetooth y virtuales que abren sin quejarse
    y se tragan cualquier escritura, y los adaptadores USB reales son los
    unicos con vid/pid. El receptor no lo usa: ahi los prueba todos porque la
    sonda distingue sola, y en la Pi el medidor puede colgar del UART interno
    (/dev/ttyS0), que no tiene vid y quedaria descartado.
    """
    forced = os.environ.get("SMARTMETER_PORT")
    if forced:
        return [forced]
    ports = serial.tools.list_ports.comports()
    if prefer_usb:
        usb = [p.device for p in ports if p.vid is not None]
        if usb:
            return usb
    return [p.device for p in ports]


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
    for device in candidate_devices():
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


def open_all_ports(baudrate=BAUDRATE, poll_seconds=2.0, on_log=print):
    """Lado emisor (el generador): abre todos los puertos USB, no uno.

    Aca no se puede sondear: el generador escribe y nadie le contesta, asi que
    no hay forma de saber cual de los adaptadores es el cable al medidor.
    Elegir el primero es adivinar, y adivinar mal deja la cadena muda sin un
    solo error visible. Escribir en todos cuesta ~200 bytes por segundo por
    puerto, y el receptor ya se queda con el unico que trae telegramas.
    SMARTMETER_PORT sigue mandando cuando hace falta forzar uno.

    write_timeout evita que un COM que enumera pero no drena (Bluetooth sin
    par, adaptador colgado) bloquee el write y frene los otros puertos.

    on_log recibe los fallos de apertura. Importa: el .exe se compila sin
    consola, asi que un print aca no lo lee nadie y un puerto que se enumera
    pero no abre (driver bloqueado, adaptador en uso) queda como un silencio.
    """
    said = set()
    while True:
        opened = []
        for device in candidate_devices(prefer_usb=True):
            try:
                opened.append(serial.Serial(device, baudrate=baudrate, timeout=5.0, write_timeout=1.0))
            except OSError as e:
                if device not in said:
                    on_log(f"Cannot open {device}: {e}")
                    said.add(device)
        if opened:
            return opened
        if "none" not in said:
            on_log("No serial ports found")
            said.add("none")
        time.sleep(poll_seconds)
