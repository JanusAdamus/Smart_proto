import random
import re
import time


INITIAL_ENERGY_KWH = 3.47705
MIN_POWER_KW = 0.8
MAX_POWER_KW = 2.3
MIN_VOLTAGE_V = 227.0
MAX_VOLTAGE_V = 233.0
MAX_ACCUMULATED_ENERGY_KWH = 7.0


def crc16_arc(data: bytes) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


class MeterState:
    def __init__(self, kw=1.5, voltage=230.0, kwh=None):
        self.kw = kw
        self.voltage = voltage
        self.kwh = INITIAL_ENERGY_KWH if kwh is None else kwh
        self._limit_accumulated_energy = kwh is None

    def tick(self, dt_seconds=1.0):
        self.kw = min(MAX_POWER_KW, max(MIN_POWER_KW, self.kw + random.gauss(0, 0.05)))
        self.voltage = min(
            MAX_VOLTAGE_V,
            max(MIN_VOLTAGE_V, self.voltage + random.gauss(0, 0.3)),
        )
        next_kwh = self.kwh + self.kw * (dt_seconds / 3600.0)
        if self._limit_accumulated_energy:
            next_kwh = min(next_kwh, MAX_ACCUMULATED_ENERGY_KWH - 0.001)
        self.kwh = next_kwh


def generate_telegram(state: MeterState, now=None) -> bytes:
    now = now or time.localtime()
    ts = time.strftime("%y%m%d%H%M%S", now) + "W"
    lines = [
        "/ISK5\\2MT382-1000",
        f"0-0:1.0.0({ts})",
        f"1-0:1.7.0({state.kw:07.3f}*kW)",
        f"1-0:1.8.1({state.kwh:010.3f}*kWh)",
        f"1-0:32.7.0({state.voltage:05.1f}*V)",
    ]
    body = "\r\n".join(lines) + "\r\n!"
    crc = crc16_arc(body.encode("ascii"))
    return (body + f"{crc:04X}" + "\r\n").encode("ascii")


class InvalidTelegram(Exception):
    pass


_FIELD_PATTERNS = {
    "kw": re.compile(r"1-0:1\.7\.0\(([\d.]+)\*kW\)"),
    "kwh": re.compile(r"1-0:1\.8\.1\(([\d.]+)\*kWh\)"),
    "voltage": re.compile(r"1-0:32\.7\.0\(([\d.]+)\*V\)"),
}
_TIMESTAMP_PATTERN = re.compile(r"0-0:1\.0\.0\((\d{12}[WS])\)")


def parse_telegram(raw: bytes) -> dict:
    text = raw.decode("ascii")
    if "!" not in text:
        raise InvalidTelegram("missing checksum marker")
    body, _, rest = text.rpartition("!")
    checksum_hex = rest.strip()[:4]
    try:
        expected = int(checksum_hex, 16)
    except ValueError:
        raise InvalidTelegram(f"bad checksum hex: {checksum_hex!r}")
    actual = crc16_arc((body + "!").encode("ascii"))
    if actual != expected:
        raise InvalidTelegram(f"checksum mismatch: got {actual:04X}, expected {expected:04X}")

    result = {}
    for key, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            raise InvalidTelegram(f"missing field: {key}")
        result[key] = float(match.group(1))
    timestamp_match = _TIMESTAMP_PATTERN.search(text)
    if timestamp_match:
        result["timestamp"] = timestamp_match.group(1)
    return result


_TELEGRAM_RE = re.compile(rb"/ISK5.*?\r\n!([0-9A-Fa-f]{4})\r\n", re.DOTALL)


class TelegramReader:
    def __init__(self):
        self._buffer = b""

    def feed(self, chunk: bytes) -> list:
        self._buffer += chunk
        telegrams = []
        while True:
            match = _TELEGRAM_RE.search(self._buffer)
            if not match:
                break
            telegrams.append(match.group(0))
            self._buffer = self._buffer[match.end():]
        return telegrams
