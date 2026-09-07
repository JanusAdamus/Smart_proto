import base64

import dashboard
import pytest
from dashboard import DashboardState
from dsmr import TelegramReader, parse_telegram, InvalidTelegram, MeterState, generate_telegram


def test_parse_ethernet_adapters_accepts_one_or_many_adapters():
    one = dashboard.parse_ethernet_adapters_json(
        '{"index":12,"name":"Ethernet 2","description":"Realtek USB GbE",'
        '"pnp_device_id":"USB\\\\VID_0BDA"}'
    )
    many = dashboard.parse_ethernet_adapters_json(
        '[{"index":4,"name":"Ethernet","description":"Intel I219",'
        '"pnp_device_id":"PCI\\\\VEN_8086"},'
        '{"index":12,"name":"Ethernet 2","description":"Realtek USB GbE",'
        '"pnp_device_id":"USB\\\\VID_0BDA"}]'
    )

    assert one == [
        {
            "index": 12,
            "name": "Ethernet 2",
            "description": "Realtek USB GbE",
            "pnp_device_id": "USB\\VID_0BDA",
        }
    ]
    assert [adapter["index"] for adapter in many] == [4, 12]


def test_choose_direct_cable_adapter_prefers_usb_over_onboard_ethernet():
    adapters = [
        {
            "index": 4,
            "name": "Ethernet",
            "description": "Intel Ethernet Connection I219",
            "pnp_device_id": "PCI\\VEN_8086",
        },
        {
            "index": 12,
            "name": "Ethernet 2",
            "description": "Realtek USB GbE Family Controller",
            "pnp_device_id": "USB\\VID_0BDA",
        },
    ]

    assert dashboard.choose_direct_cable_adapter(adapters)["index"] == 12


def test_choose_direct_cable_adapter_refuses_to_guess_between_two_adapters():
    adapters = [
        {"index": 4, "name": "Ethernet", "description": "ASIX USB Ethernet"},
        {"index": 8, "name": "Ethernet 2", "description": "Realtek USB GbE"},
    ]

    with pytest.raises(RuntimeError, match="más de un adaptador Ethernet activo"):
        dashboard.choose_direct_cable_adapter(adapters)


def test_choose_direct_cable_adapter_never_changes_the_onboard_ethernet():
    adapters = [
        {
            "index": 4,
            "name": "Ethernet",
            "description": "Intel Ethernet Connection I219",
            "pnp_device_id": "PCI\\VEN_8086",
        }
    ]

    with pytest.raises(RuntimeError, match="No se encontró el adaptador USB-Ethernet"):
        dashboard.choose_direct_cable_adapter(adapters)


def test_direct_cable_config_is_temporary_and_does_not_change_gateway_or_firewall():
    script = dashboard.direct_cable_config_script(12)
    lowered = script.lower()

    assert "$idx = [int](12)" in script
    assert "192.168.50.2" in script
    assert "169.254.50.2" in script
    assert "ActiveStore" in script
    assert "gateway" not in lowered
    assert "dns" not in lowered
    assert "firewall" not in lowered


def test_run_elevated_powershell_passes_the_exact_script_as_utf16(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Completed()

    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    script = "Write-Output 'safe'"

    dashboard.run_elevated_powershell(script)

    encoded = captured["env"]["SMART_METER_CABLE_SETUP"]
    decoded = base64.b64decode(encoded).decode("utf-16le")
    assert decoded == script
    assert "RunAs" in captured["command"][-1]


def test_configure_direct_cable_falls_back_to_detection_inside_uac(monkeypatch):
    captured = {}

    def denied_query():
        raise RuntimeError("access denied")

    def fake_elevated(script):
        captured["script"] = script

    monkeypatch.setattr(dashboard, "list_ethernet_adapters", denied_query)
    monkeypatch.setattr(dashboard, "run_elevated_powershell", fake_elevated)

    adapter = dashboard.configure_direct_cable()

    assert adapter["name"] == "adaptador USB-Ethernet"
    assert "Get-NetAdapter -Physical" in captured["script"]
    assert "192.168.50.2" in captured["script"]
    assert "169.254.50.2" in captured["script"]


@pytest.mark.parametrize(
    ("returncode", "message"),
    [
        (20, "No se encontró el adaptador USB-Ethernet"),
        (21, "más de un adaptador Ethernet activo"),
    ],
)
def test_elevated_detection_reports_safe_adapter_errors(returncode, message):
    class Completed:
        stdout = ""
        stderr = ""

        def __init__(self, code):
            self.returncode = code

    with pytest.raises(RuntimeError, match=message):
        dashboard.run_elevated_powershell(
            "exit 0",
            runner=lambda *_args, **_kwargs: Completed(returncode),
        )


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
    count, _log, timestamp = state.metadata_snapshot()
    assert count == 1
    assert len(timestamp) == 13


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


def test_dashboard_state_keeps_the_complete_latest_telegram():
    state = DashboardState()
    meter_state = MeterState(kw=1.8, voltage=231.0, kwh=99.0)
    raw = generate_telegram(meter_state)

    state.update(parse_telegram(raw), raw)

    assert state.latest_telegram_snapshot() == raw.decode("ascii").rstrip("\r\n")
    assert "1-0:1.7.0(001.800*kW)" in state.latest_telegram_snapshot()
    assert state.latest_telegram_snapshot().endswith(raw.decode("ascii").strip().splitlines()[-1])


def test_dashboard_formats_accumulated_energy_as_wh():
    assert dashboard.format_energy_wh(3.47705) == "3,477 Wh"


def test_dashboard_formats_accumulated_energy_as_kwh():
    assert dashboard.format_energy_kwh(3.47705) == "3.477 kWh"
    assert dashboard.format_meter_timestamp("260825221931W") == "2026-08-25 22:19:31"


def test_dashboard_ui_has_no_cable_configuration_button():
    source = open(dashboard.__file__, encoding="utf-8").read()
    ui_source = source[source.index("class SmartMeterDashboardUI"):]
    assert "Configure Cable and Connect" not in ui_source
    assert "Raspberry Pi TCP link" in ui_source


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
    def __init__(self, ip, is_IPv4=True, network_prefix=24):
        self.ip = ip
        self.is_IPv4 = is_IPv4
        self.network_prefix = network_prefix


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


def test_local_subnet_endpoints_can_find_a_pi_that_is_not_dot_one(monkeypatch):
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [_FakeAdapter(_FakeIP("192.168.100.13", network_prefix=24))],
    )

    hosts = [host for host, _port in dashboard.local_subnet_endpoints()]

    assert "192.168.100.87" in hosts
    assert "192.168.100.13" not in hosts


def test_dashboard_scans_the_local_subnet_after_known_addresses_fail(monkeypatch):
    expected_socket = object()

    def fake_create_connection(address, timeout):
        if address == ("192.168.100.87", 4000):
            return expected_socket
        raise OSError(10061, "not the meter")

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    sock, endpoint = dashboard.connect_to_meter(
        endpoints=(("192.168.100.1", 4000),),
        scan_endpoints=(("192.168.100.2", 4000), ("192.168.100.87", 4000)),
        timeout=0.1,
        scan_timeout=0.05,
    )

    assert sock is expected_socket
    assert endpoint == "192.168.100.87:4000"


def test_dashboard_binds_link_local_connection_to_the_usb_ethernet_address(monkeypatch):
    expected_socket = object()
    attempts = []

    def fake_create_connection(address, timeout, source_address=None):
        attempts.append((address, timeout, source_address))
        return expected_socket

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    sock, endpoint = dashboard.connect_to_any_endpoint(
        (("169.254.50.1", 4000, "169.254.109.119"),),
        timeout=0.2,
    )

    assert sock is expected_socket
    assert endpoint == "169.254.50.1:4000 via 169.254.109.119"
    assert attempts == [
        (("169.254.50.1", 4000), 0.2, ("169.254.109.119", 0)),
    ]


def test_source_bound_connection_works_with_a_real_windows_socket():
    listener = dashboard.socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()

    client = accepted = None
    try:
        client, endpoint = dashboard.connect_to_any_endpoint(
            ((host, port, "127.0.0.1"),),
            timeout=0.5,
        )
        accepted, peer = listener.accept()

        assert endpoint == f"{host}:{port} via 127.0.0.1"
        assert peer[0] == "127.0.0.1"
    finally:
        if accepted:
            accepted.close()
        if client:
            client.close()
        listener.close()


def test_source_bound_attempts_only_use_an_interface_on_the_same_network(monkeypatch):
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [
            _FakeAdapter(_FakeIP("169.254.109.119", network_prefix=16)),
            _FakeAdapter(_FakeIP("192.168.100.13", network_prefix=24)),
        ],
    )

    attempts = dashboard.source_bound_attempts(
        (("169.254.50.1", 4000), ("192.168.100.87", 4000))
    )

    assert ("169.254.50.1", 4000, "169.254.109.119") in attempts
    assert ("192.168.100.87", 4000, "192.168.100.13") in attempts
    assert ("169.254.50.1", 4000, "192.168.100.13") not in attempts


def test_dashboard_has_no_zeroconf_runtime_dependency():
    assert not hasattr(dashboard, "Zeroconf")
    assert not hasattr(dashboard, "ServiceWaiter")


def test_automatic_discovery_finds_the_configured_192_address(monkeypatch):
    expected_socket = object()
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [_FakeAdapter(_FakeIP("192.168.50.22", network_prefix=24))],
    )

    def fake_create_connection(address, timeout, source_address=None):
        if address == ("192.168.50.1", 4000):
            return expected_socket
        raise OSError(10061, "not the Pi")

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    sock, endpoint = dashboard.connect_to_meter()

    assert sock is expected_socket
    assert endpoint == "192.168.50.1:4000"


def test_automatic_discovery_finds_networkmanager_shared_subnet(monkeypatch):
    expected_socket = object()
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [_FakeAdapter(_FakeIP("10.42.0.53", network_prefix=24))],
    )

    def fake_create_connection(address, timeout, source_address=None):
        if address == ("10.42.0.1", 4000):
            return expected_socket
        raise OSError(10061, "not the Pi")

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    sock, endpoint = dashboard.connect_to_meter()

    assert sock is expected_socket
    assert endpoint == "10.42.0.1:4000"


def test_automatic_discovery_finds_arbitrary_dhcp_ip_in_local_subnet(monkeypatch):
    expected_socket = object()
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [_FakeAdapter(_FakeIP("192.168.77.13", network_prefix=24))],
    )

    def fake_create_connection(address, timeout, source_address=None):
        if address == ("192.168.77.87", 4000) and source_address == ("192.168.77.13", 0):
            return expected_socket
        raise OSError(10061, "not the Pi")

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    sock, endpoint = dashboard.connect_to_meter()

    assert sock is expected_socket
    assert endpoint == "192.168.77.87:4000 via 192.168.77.13"


def test_automatic_discovery_tries_every_link_local_adapter(monkeypatch):
    expected_socket = object()
    monkeypatch.setattr(
        dashboard.ifaddr,
        "get_adapters",
        lambda: [
            _FakeAdapter(_FakeIP("169.254.10.20", network_prefix=16)),
            _FakeAdapter(_FakeIP("169.254.200.30", network_prefix=16)),
        ],
    )

    def fake_create_connection(address, timeout, source_address=None):
        if address == ("169.254.50.1", 4000) and source_address == ("169.254.200.30", 0):
            return expected_socket
        raise OSError(10065, "wrong cable route")

    monkeypatch.setattr(dashboard.socket, "create_connection", fake_create_connection)

    sock, endpoint = dashboard.connect_to_meter()

    assert sock is expected_socket
    assert endpoint == "169.254.50.1:4000 via 169.254.200.30"


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
