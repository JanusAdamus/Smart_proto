"""Generacion, validacion y parseo de telegramas DSMR 5.0.2.

Referencia: DSMR 5.0.2 P1 Companion Standard, Netbeheer Nederland, 2016-02-26.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

METER_TZ = ZoneInfo("Europe/Amsterdam")


class InvalidTelegram(Exception):
    pass


def crc16_arc(data: bytes) -> int:
    """CRC16 segun 6.2 del estandar.

    Polinomio x^16+x^15+x^2+1 en su forma reflejada (0xA001), sin XOR de
    entrada ni de salida, bit menos significativo primero.
    """
    crc = 0x0000
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


_TST = re.compile(r"^(\d{12})([WS])$")


def parse_timestamp(value: str):
    """'YYMMDDhhmmssX' en hora holandesa -> segundos unix, o None.

    La bandera final distingue verano (S) de invierno (W). Importa una vez
    al ano: en el cambio de otono la misma hora local ocurre dos veces, y
    esa letra es lo unico que separa una ocurrencia de la otra. Se traduce
    a `fold`, que es como datetime representa la segunda ocurrencia.
    """
    match = _TST.match(value)
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group(1), "%y%m%d%H%M%S")
    except ValueError:
        return None
    fold = 0 if match.group(2) == "S" else 1
    return int(naive.replace(tzinfo=METER_TZ, fold=fold).timestamp())


def format_timestamp(moment: datetime) -> str:
    """Inversa de parse_timestamp, para el simulador."""
    flag = "S" if moment.dst() else "W"
    return moment.strftime("%y%m%d%H%M%S") + flag
