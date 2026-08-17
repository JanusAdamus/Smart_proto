import dashboard
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


def test_dashboard_connects_to_fixed_pi_before_using_zeroconf(monkeypatch):
    expected_socket = object()
    attempts = []

    def fake_create_connection(address, timeout):
        attempts.append((address, timeout))
        return expected_socket

    class ZeroconfMustNotRun:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("Zeroconf no debe usarse cuando la IP fija responde")

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(dashboard, "ServiceWaiter", ZeroconfMustNotRun)

    sock, endpoint = dashboard.connect_to_meter(
        object(), direct_host="192.168.50.1", direct_port=4000, timeout=1.0
    )

    assert sock is expected_socket
    assert endpoint == "192.168.50.1:4000"
    assert attempts == [(('192.168.50.1', 4000), 1.0)]


def test_dashboard_uses_zeroconf_when_fixed_pi_is_unreachable(monkeypatch):
    expected_socket = object()

    def direct_connection_fails(_address, timeout):
        assert timeout == 1.0
        raise OSError("fixed address unavailable")

    class FakeWaiter:
        def __init__(self, zc, service_type):
            assert zc == "zc"
            assert service_type == dashboard.SERVICE_TYPE

        def wait(self, timeout):
            assert timeout == 3.0
            return ["10.0.0.20"], 4567

    def fake_connect_to_service(ips, port, timeout):
        assert ips == ["10.0.0.20"]
        assert port == 4567
        assert timeout == 5.0
        return expected_socket

    monkeypatch.setattr(dashboard.socket, "create_connection", direct_connection_fails)
    monkeypatch.setattr(dashboard, "ServiceWaiter", FakeWaiter)
    monkeypatch.setattr(dashboard, "connect_to_service", fake_connect_to_service)

    sock, endpoint = dashboard.connect_to_meter(
        "zc", direct_host="192.168.50.1", direct_port=4000, timeout=1.0
    )

    assert sock is expected_socket
    assert endpoint == "discovered service on port 4567"
