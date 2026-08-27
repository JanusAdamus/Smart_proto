"""Simulador de puerto P1 sobre TCP: una computadora en lugar del medidor.

Ocupa el lugar del medidor y de la Pi lectora a la vez, sirviendo el mismo
flujo en el mismo puerto que `relay.py`. La Pi con pantalla no distingue una
cosa de la otra: se le cambia `SMARTMETER_READER_HOST` y nada mas.

La idea la copiamos de p1meter.dev (https://github.com/mijnverbruik/p1meter.dev),
que hace esto mismo en Elixir. Aca alcanza con la biblioteca estandar porque el
generador de telegramas (`dsmr.py`) y el servidor TCP (`relay.py`) ya estaban
escritos; lo unico que faltaba era pegarlos.
"""

import threading
import time

from dsmr import MeterState, generate_telegram
from relay import DOWNSTREAM_PORT, RelayServer
from serial_link import BAUDRATE

INTERVAL_SECONDS = 1.0


def stream_loop(relay, state=None, interval=INTERVAL_SECONDS, baudrate=BAUDRATE, stop=None):
    """Emite un telegrama por intervalo, linea a linea y a la velocidad del cable.

    El goteo importa: un P1 real entrega el telegrama a 115200 baudios, asi que
    el consumidor recibe trozos parciales y tiene que rearmarlos. Mandar el
    kilobyte de golpe seria un simulador mas facil de escribir y mas facil de
    aprobar, que es justo lo que no queremos de un simulador.
    """
    state = state or MeterState()
    # 8N1: cada byte viaja como 10 bits (arranque, ocho de datos, parada).
    seconds_per_byte = 10.0 / baudrate
    while stop is None or not stop.is_set():
        started = time.monotonic()
        state.tick(interval)
        for line in generate_telegram(state).splitlines(keepends=True):
            relay.broadcast(line)
            time.sleep(len(line) * seconds_per_byte)
        pending = interval - (time.monotonic() - started)
        if pending > 0:
            time.sleep(pending)


def main():
    relay = RelayServer(port=DOWNSTREAM_PORT)
    relay.start()
    print(f"simulador P1 en TCP {DOWNSTREAM_PORT}, un telegrama cada {INTERVAL_SECONDS:g} s", flush=True)
    stop = threading.Event()
    try:
        stream_loop(relay, stop=stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        relay.stop()


if __name__ == "__main__":
    main()
