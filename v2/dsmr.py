"""Generacion, validacion y parseo de telegramas DSMR 5.0.2.

Referencia: DSMR 5.0.2 P1 Companion Standard, Netbeheer Nederland, 2016-02-26.
"""

import calendar
import random
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
    # Un byte corrupto se vuelve � al decodificar, y el CRC se calcula
    # re-codificando a ASCII, que no lo tolera. Rechazar aca es lo mismo que
    # rechazar por CRC: el telegrama llego dañado y no se puede confiar en el.
    # Importa porque el ruido de linea en un cable serie real es normal, y el
    # consumidor tiene que poder descartar uno y seguir, no caerse.
    if "�" in text:
        raise InvalidTelegram("bytes no ASCII en el telegrama")
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


# Un telegrama con el mensaje de texto de 1024 caracteres y un registro largo
# de cortes no pasa de unos pocos KB. 16 KB deja margen de sobra y sigue
# acotando la memoria si el cable mete ruido.
MAX_BUFFER = 16384

# Desde una '/' hasta el '!' con sus cuatro hexadecimales. El identificador
# del fabricante queda como comodin: es lo que rompia en v1.
_TELEGRAM = re.compile(rb"/[^\r\n]*\r\n.*?\r\n![0-9A-Fa-f]{4}\r\n", re.DOTALL)


class TelegramReader:
    """Separa telegramas de un flujo de bytes que llega en trozos arbitrarios."""

    def __init__(self, max_buffer=MAX_BUFFER):
        self._buffer = b""
        self._max = max_buffer
        self.dropped_bytes = 0

    def feed(self, chunk: bytes) -> list:
        self._buffer += chunk
        telegrams = []
        while True:
            match = _TELEGRAM.search(self._buffer)
            if not match:
                break
            telegrams.append(match.group(0))
            self._buffer = self._buffer[match.end():]
        if len(self._buffer) > self._max:
            start = self._buffer.rfind(b"/")
            # start == 0 significa que lo acumulado ya empieza con '/' y no es
            # un telegrama valido: recortar hasta ahi no liberaria nada.
            if start <= 0:
                self.dropped_bytes += len(self._buffer)
                self._buffer = b""
            else:
                self.dropped_bytes += start
                self._buffer = self._buffer[start:]
        return telegrams


IDENT = "ISk5\\2MT382-1000"
EQUIPMENT_ID = "4B384547303034303436333935353037"
GAS_EQUIPMENT_ID = "3232323241424344313233343536373839"
GAS_INTERVAL_SECONDS = 300


class MeterState:
    """Estado de un medidor domestico trifasico, con evolucion creible.

    Los valores son sinteticos, pero su forma importa: la energia solo
    crece, la potencia se mueve como una caminata aleatoria acotada y el gas
    avanza cada cinco minutos, que es como reporta un M-Bus real.
    """

    def __init__(self):
        self.energy_in_t1 = 3477.050
        self.energy_in_t2 = 4218.319
        self.energy_out_t1 = 0.0
        self.energy_out_t2 = 0.0
        self.power_in = 1.5
        self.power_out = 0.0
        self.tariff = 2
        self.voltage = [230.0, 229.4, 230.6]
        self.current = [2, 1, 3]
        self.phase_power = [0.5, 0.5, 0.5]
        self.gas = 12785.123
        # Se fija de entrada en vez de quedar en None: si no, la marca de
        # captura seria el instante de cada telegrama y avanzaria segundo a
        # segundo con el valor de gas quieto, hasta la primera captura real a
        # los 5 minutos. Un M-Bus nunca reporta eso: el valor y su marca
        # cambian juntos.
        self.gas_captured_at = meter_now()
        self._seconds_since_gas = 0.0

    def tick(self, dt_seconds=1.0, now=None):
        now = now or meter_now()
        self.power_in = min(3.5, max(0.2, self.power_in + random.gauss(0, 0.05)))
        share = self.power_in / 3.0
        self.phase_power = [
            round(max(0.0, share + random.gauss(0, 0.03)), 3) for _ in range(3)
        ]
        self.voltage = [
            round(min(235.0, max(225.0, v + random.gauss(0, 0.3))), 1)
            for v in self.voltage
        ]
        # 230 V por fase; la corriente se deduce de la potencia de esa fase.
        self.current = [
            int(round(p * 1000.0 / v)) for p, v in zip(self.phase_power, self.voltage)
        ]
        # Solo acumula la tarifa activa, como un medidor real con doble tarifa.
        delta = self.power_in * (dt_seconds / 3600.0)
        if self.tariff == 1:
            self.energy_in_t1 += delta
        else:
            self.energy_in_t2 += delta

        self._seconds_since_gas += dt_seconds
        if self._seconds_since_gas >= GAS_INTERVAL_SECONDS:
            self._seconds_since_gas = 0.0
            self.gas = round(self.gas + random.uniform(0.001, 0.02), 3)
            self.gas_captured_at = now


def generate_telegram(state: MeterState, now=None) -> bytes:
    """Telegrama DSMR 5.0.2 completo, con la estructura de 6.13 del estandar."""
    now = now or meter_now()
    gas_moment = state.gas_captured_at
    lines = [
        f"/{IDENT}",
        "",
        "1-3:0.2.8(50)",
        f"0-0:1.0.0({format_timestamp(now)})",
        f"0-0:96.1.1({EQUIPMENT_ID})",
        f"1-0:1.8.1({state.energy_in_t1:010.3f}*kWh)",
        f"1-0:1.8.2({state.energy_in_t2:010.3f}*kWh)",
        f"1-0:2.8.1({state.energy_out_t1:010.3f}*kWh)",
        f"1-0:2.8.2({state.energy_out_t2:010.3f}*kWh)",
        f"0-0:96.14.0({state.tariff:04d})",
        f"1-0:1.7.0({state.power_in:06.3f}*kW)",
        f"1-0:2.7.0({state.power_out:06.3f}*kW)",
        "0-0:96.7.21(00004)",
        "0-0:96.7.9(00002)",
        "1-0:99.97.0(0)(0-0:96.7.19)",
        "1-0:32.32.0(00002)",
        "1-0:52.32.0(00001)",
        "1-0:72.32.0(00000)",
        "1-0:32.36.0(00000)",
        "1-0:52.36.0(00003)",
        "1-0:72.36.0(00000)",
        "0-0:96.13.0()",
        f"1-0:32.7.0({state.voltage[0]:05.1f}*V)",
        f"1-0:52.7.0({state.voltage[1]:05.1f}*V)",
        f"1-0:72.7.0({state.voltage[2]:05.1f}*V)",
        f"1-0:31.7.0({state.current[0]:03d}*A)",
        f"1-0:51.7.0({state.current[1]:03d}*A)",
        f"1-0:71.7.0({state.current[2]:03d}*A)",
        f"1-0:21.7.0({state.phase_power[0]:06.3f}*kW)",
        f"1-0:41.7.0({state.phase_power[1]:06.3f}*kW)",
        f"1-0:61.7.0({state.phase_power[2]:06.3f}*kW)",
        "1-0:22.7.0(00.000*kW)",
        "1-0:42.7.0(00.000*kW)",
        "1-0:62.7.0(00.000*kW)",
        "0-1:24.1.0(003)",
        f"0-1:96.1.0({GAS_EQUIPMENT_ID})",
        f"0-1:24.2.1({format_timestamp(gas_moment)})({state.gas:09.3f}*m3)",
    ]
    body = "\r\n".join(lines) + "\r\n"
    crc = crc16_arc((body + "!").encode("ascii"))
    return (body + f"!{crc:04X}\r\n").encode("ascii")
