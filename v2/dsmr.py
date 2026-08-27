"""Generacion, validacion y parseo de telegramas DSMR 5.0.2.

Referencia: DSMR 5.0.2 P1 Companion Standard, Netbeheer Nederland, 2016-02-26.
"""

import calendar
import re
from datetime import datetime, timedelta, timezone

_TST = re.compile(r"^(\d{12})([WS])$")


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


def parse_timestamp(value: str):
    """'YYMMDDhhmmssX' en hora holandesa -> segundos unix, o None.

    La bandera final trae el offset: S es verano (UTC+2) y W invierno (UTC+1).
    Por eso no hace falta una base de zonas horarias, y por eso la hora que se
    repite en el cambio de otono no es ambigua: la letra la distingue.
    """
    match = _TST.match(value)
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group(1), "%y%m%d%H%M%S")
    except ValueError:
        return None
    offset = 2 if match.group(2) == "S" else 1
    return int(naive.replace(tzinfo=timezone(timedelta(hours=offset))).timestamp())


def _last_march_or_october_sunday(year: int, month: int) -> datetime:
    """Ultimo domingo del mes a las 01:00 UTC (borde de cambio de horario)."""
    day = max(week[calendar.SUNDAY] for week in calendar.monthcalendar(year, month))
    return datetime(year, month, day, 1, tzinfo=timezone.utc)


def is_summer_time(moment_utc: datetime) -> bool:
    """Ventana de verano europea, definida por regla y no por tabla de datos."""
    year = moment_utc.year
    return (_last_march_or_october_sunday(year, 3) <= moment_utc
            < _last_march_or_october_sunday(year, 10))


def format_timestamp(moment_utc: datetime) -> str:
    """Inversa de parse_timestamp, para el simulador. Recibe un datetime UTC."""
    summer = is_summer_time(moment_utc)
    local = moment_utc + timedelta(hours=2 if summer else 1)
    return local.strftime("%y%m%d%H%M%S") + ("S" if summer else "W")


def meter_now() -> datetime:
    """Instante actual en UTC, para el generador. Reemplaza a METER_TZ."""
    return datetime.now(timezone.utc)


# Una linea OBIS es un codigo seguido de cero o mas valores entre parentesis.
# No se enumeran los codigos posibles a proposito: 6.13 del estandar advierte
# que ni el conjunto ni el orden son fijos, y varian entre fabricantes.
_OBIS_LINE = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+)((?:\([^)]*\))*)\s*$")
_VALUE = re.compile(r"\(([^)]*)\)")
_UNIT_VALUE = re.compile(r"^(-?\d+(?:\.\d+)?)\*[A-Za-z0-9]+$")


def parse_telegram(raw: bytes) -> dict:
    """Valida el CRC y devuelve la identificacion y todos los objetos OBIS.

    Los valores se devuelven como texto tal cual llegaron. Convertirlos aqui
    obligaria a saber que unidad tiene cada codigo, que es justo lo que no se
    puede saber de antemano.
    """
    text = raw.decode("ascii", errors="replace")
    body, marker, rest = text.rpartition("!")
    if not marker:
        raise InvalidTelegram("falta el marcador de checksum")
    checksum_hex = rest.strip()[:4]
    try:
        expected = int(checksum_hex, 16)
    except ValueError:
        raise InvalidTelegram(f"checksum no hexadecimal: {checksum_hex!r}")
    # El CRC cubre desde '/' hasta '!' inclusive (6.2 del estandar).
    actual = crc16_arc((body + "!").encode("ascii"))
    if actual != expected:
        raise InvalidTelegram(f"checksum no coincide: {actual:04X} != {expected:04X}")

    lines = body.split("\r\n")
    if not lines[0].startswith("/"):
        raise InvalidTelegram("el telegrama no empieza con '/'")
    objects = {}
    for line in lines[1:]:
        match = _OBIS_LINE.match(line)
        if match:
            objects[match.group(1)] = _VALUE.findall(match.group(2))
    return {"ident": lines[0][1:], "objects": objects}


def as_number(value: str):
    """'123.456*kWh' -> 123.456; '00002' -> 2.0; una marca de tiempo -> None."""
    match = _UNIT_VALUE.match(value)
    if match:
        return float(match.group(1))
    try:
        return float(value)
    except ValueError:
        return None


def number(objects: dict, code: str, index: int = 0):
    """Valor numerico de un objeto OBIS, o None si no esta.

    Devolver None en vez de fallar es deliberado: un medidor monofasico no
    informa L2 ni L3, y un medidor sin gas no informa 24.2.1. La ausencia de
    un campo es normal, no un error.
    """
    values = objects.get(code)
    if not values or index >= len(values):
        return None
    return as_number(values[index])
