from dsmr import (
    MAX_ACCUMULATED_ENERGY_KWH,
    MAX_POWER_KW,
    MAX_VOLTAGE_V,
    MIN_POWER_KW,
    MIN_VOLTAGE_V,
    InvalidTelegram,
    MeterState,
    TelegramReader,
    crc16_arc,
    generate_telegram,
    parse_telegram,
)


def test_crc16_arc_known_vector():
    # "123456789" es el vector de prueba estándar para CRC-16/ARC, resultado conocido: 0xBB3D
    assert crc16_arc(b"123456789") == 0xBB3D


def test_generate_telegram_has_valid_checksum():
    state = MeterState(kw=2.345, voltage=230.4, kwh=671.578)
    raw = generate_telegram(state, now=(2026, 8, 13, 12, 0, 0, 0, 0, 0))
    assert raw.startswith(b"/ISK5")
    assert raw.rstrip(b"\r\n").split(b"!")[-1] != b""


def test_parse_telegram_roundtrip():
    state = MeterState(kw=2.345, voltage=230.4, kwh=671.578)
    raw = generate_telegram(state)
    fields = parse_telegram(raw)
    assert abs(fields["kw"] - 2.345) < 0.001
    assert abs(fields["voltage"] - 230.4) < 0.001
    assert abs(fields["kwh"] - 671.578) < 0.001
    assert len(fields["timestamp"]) == 13


def test_default_meter_values_have_compact_realistic_magnitudes():
    state = MeterState()
    previous_kwh = state.kwh

    for _ in range(120):
        state.tick()
        assert MIN_POWER_KW <= state.kw <= MAX_POWER_KW
        assert MIN_VOLTAGE_V <= state.voltage <= MAX_VOLTAGE_V
        assert previous_kwh <= state.kwh < MAX_ACCUMULATED_ENERGY_KWH
        previous_kwh = state.kwh

    assert state.kwh < 4.0


def test_parse_telegram_rejects_bad_checksum():
    state = MeterState(kw=1.0, voltage=230.0, kwh=10.0)
    raw = bytearray(generate_telegram(state))
    raw[-6] = ord("0") if chr(raw[-6]) != "0" else ord("1")  # altera un dígito del checksum
    try:
        parse_telegram(bytes(raw))
        assert False, "esperaba InvalidTelegram"
    except InvalidTelegram:
        pass


def test_telegram_reader_handles_split_chunks():
    state = MeterState(kw=1.2, voltage=229.0, kwh=50.0)
    raw = generate_telegram(state)
    reader = TelegramReader()
    mid = len(raw) // 2
    assert reader.feed(raw[:mid]) == []
    telegrams = reader.feed(raw[mid:])
    assert len(telegrams) == 1
    assert parse_telegram(telegrams[0])["kw"] == 1.2


def test_telegram_reader_handles_two_telegrams_in_one_chunk():
    state = MeterState(kw=1.0, voltage=230.0, kwh=1.0)
    raw1 = generate_telegram(state)
    state.tick()
    raw2 = generate_telegram(state)
    reader = TelegramReader()
    telegrams = reader.feed(raw1 + raw2)
    assert len(telegrams) == 2
