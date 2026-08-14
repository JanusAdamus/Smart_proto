from dashboard import DashboardState
from dsmr import TelegramReader, parse_telegram, InvalidTelegram, MeterState, generate_telegram


def test_dashboard_state_updates_from_valid_telegram():
    state = DashboardState()
    meter_state = MeterState(kw=1.8, voltage=231.0, kwh=99.0)
    raw = generate_telegram(meter_state)
    reader = TelegramReader()
    telegrams = reader.feed(raw)
    for t in telegrams:
        fields = parse_telegram(t)
        state.update(fields)
    values, voltage, kwh, last_update = state.snapshot()
    assert values[-1] == 1.8
    assert voltage == 231.0
    assert kwh == 99.0
    assert last_update > 0


def test_dashboard_ignores_corrupt_telegram():
    state = DashboardState()
    reader = TelegramReader()
    corrupt = b"/ISK5\\2MT382-1000\r\n0-0:1.0.0(260813120000W)\r\n!0000\r\n"
    telegrams = reader.feed(corrupt)
    for t in telegrams:
        try:
            fields = parse_telegram(t)
            state.update(fields)
        except InvalidTelegram:
            pass
    values, _voltage, _kwh, _last_update = state.snapshot()
    assert values == []


def test_dashboard_state_logs_received_messages():
    state = DashboardState()
    meter_state = MeterState(kw=1.8, voltage=231.0, kwh=99.0)
    raw = generate_telegram(meter_state)
    reader = TelegramReader()
    for t in reader.feed(raw):
        fields = parse_telegram(t)
        state.update(fields)
    log = state.log_snapshot()
    assert log == ["Received #1: 1.800 kW"]
