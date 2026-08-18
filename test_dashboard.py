import dashboard
import pytest
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


def test_dashboard_uses_primary_pi_address_first(monkeypatch):
    expected_socket = object()
    attempts = []

    def fake_create_connection(address, timeout):
        attempts.append((address, timeout))
        return expected_socket

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    endpoints = (("192.168.50.1", 4000), ("169.254.50.1", 4000))
    sock, endpoint = dashboard.connect_to_meter(endpoints=endpoints, timeout=1.0)

    assert sock is expected_socket
    assert endpoint == "192.168.50.1:4000"
    assert attempts == [(('192.168.50.1', 4000), 1.0)]


def test_dashboard_uses_link_local_when_dhcp_route_is_missing(monkeypatch):
    expected_socket = object()
    attempts = []

    def fake_create_connection(address, timeout):
        attempts.append((address, timeout))
        if address[0] == "192.168.50.1":
            raise OSError(10065, "unreachable host")
        return expected_socket

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    endpoints = (("192.168.50.1", 4000), ("169.254.50.1", 4000))
    sock, endpoint = dashboard.connect_to_meter(endpoints=endpoints, timeout=1.0)

    assert sock is expected_socket
    assert endpoint == "169.254.50.1:4000"
    assert attempts == [
        (("192.168.50.1", 4000), 1.0),
        (("169.254.50.1", 4000), 1.0),
    ]


def test_dashboard_reports_both_unreachable_automatic_routes(monkeypatch):
    def no_route(address, timeout):
        assert timeout == 1.0
        raise OSError(10065, f"unreachable {address[0]}")

    monkeypatch.setattr(dashboard.socket, "create_connection", no_route)

    endpoints = (("192.168.50.1", 4000), ("169.254.50.1", 4000))
    with pytest.raises(OSError) as error:
        dashboard.connect_to_meter(endpoints=endpoints, timeout=1.0)

    message = str(error.value)
    assert "192.168.50.1" in message
    assert "169.254.50.1" in message


class _FakeIP:
    def __init__(self, ip, is_IPv4=True):
        self.ip = ip
        self.is_IPv4 = is_IPv4


class _FakeAdapter:
    def __init__(self, *ips):
        self.ips = list(ips)


def test_candidate_endpoints_derives_the_pi_from_the_dhcp_lease(monkeypatch):
    # NetworkManager en modo shared usa su propia subred (10.42.0.x) si no
    # aplica la configurada. El receptor recibe una IP valida y aun asi
    # 192.168.50.1 no existe: ese es el WinError 10065 en maquinas nuevas.
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [_FakeAdapter(_FakeIP("10.42.0.53"), _FakeIP("::1", is_IPv4=False))],
    )
    hosts = [host for host, _port in dashboard.candidate_endpoints()]
    assert hosts[0] == "10.42.0.1", "no dedujo la Pi de la direccion recibida"
    assert "192.168.50.1" in hosts and "169.254.50.1" in hosts


def test_candidate_endpoints_skips_loopback_and_never_targets_itself(monkeypatch):
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [_FakeAdapter(_FakeIP("127.0.0.1"), _FakeIP("192.168.50.1"))],
    )
    hosts = [host for host, _port in dashboard.candidate_endpoints()]
    assert "127.0.0.1" not in hosts
    assert hosts.count("192.168.50.1") == 1


def test_dashboard_has_no_zeroconf_runtime_dependency():
    assert not hasattr(dashboard, "Zeroconf")
    assert not hasattr(dashboard, "ServiceWaiter")


def test_dashboard_accepts_real_telegram_split_after_first_byte():
    raw = (
        b"/ISK5\\2MT382-1000\r\n"
        b"0-0:1.0.0(260817170123W)\r\n"
        b"1-0:1.7.0(001.831*kW)\r\n"
        b"1-0:1.8.1(003477.050*kWh)\r\n"
        b"1-0:32.7.0(233.7*V)\r\n"
        b"!E9DA\r\n"
    )
    state = DashboardState()
    reader = TelegramReader()

    telegrams = reader.feed(raw[:1])
    telegrams.extend(reader.feed(raw[1:]))
    for telegram in telegrams:
        state.update(parse_telegram(telegram))

    values, voltage, kwh, _last_update = state.snapshot()
    assert values == [1.831]
    assert voltage == 233.7
    assert kwh == 3477.05
