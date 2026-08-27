#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHERNET_INTERFACE="${SMARTMETER_ETHERNET_INTERFACE:-eth0}"
ETHERNET_ADDRESS="${SMARTMETER_ETHERNET_ADDRESS:-192.168.50.1/24}"
LINK_LOCAL_ADDRESS="${SMARTMETER_LINK_LOCAL_ADDRESS:-169.254.50.1/16}"
CONNECTION_NAME="smartmeter-direct"

sudo apt-get update
sudo apt-get install -y python3-zeroconf python3-serial python3-ifaddr network-manager dnsmasq-base
sudo systemctl enable --now NetworkManager

if ! ip link show "$ETHERNET_INTERFACE" >/dev/null 2>&1; then
    echo "ERROR: no existe la interfaz $ETHERNET_INTERFACE. Interfaces disponibles:"
    ip -brief link
    echo "Vuelve a ejecutar indicando la correcta, por ejemplo:"
    echo "SMARTMETER_ETHERNET_INTERFACE=enx1234 bash install_relay.sh"
    exit 1
fi

# Red directa plug-and-play. NetworkManager conserva la ruta normal y una ruta
# link-local de respaldo. Si DHCP no responde, Windows se asigna solo una IP
# 169.254.x.x/16 y aun puede alcanzar 169.254.50.1 sin ninguna configuracion.
if sudo nmcli -t -f NAME connection show | grep -Fxq "$CONNECTION_NAME"; then
    sudo nmcli connection modify "$CONNECTION_NAME" \
        connection.interface-name "$ETHERNET_INTERFACE"
else
    sudo nmcli connection add type ethernet \
        ifname "$ETHERNET_INTERFACE" con-name "$CONNECTION_NAME"
fi
sudo nmcli connection modify "$CONNECTION_NAME" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    ipv4.method shared \
    ipv4.addresses "$ETHERNET_ADDRESS,$LINK_LOCAL_ADDRESS" \
    ipv4.never-default yes \
    ipv6.method disabled

id -u relay >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin relay
# Group membership only applies to new logins/process starts; if this script
# is re-run on an already-running Pi, `sudo systemctl restart relay` after.
sudo usermod -aG dialout relay
sudo mkdir -p /opt/smartmeter
sudo cp "$SCRIPT_DIR/relay.py" "$SCRIPT_DIR/discovery.py" \
    "$SCRIPT_DIR/serial_link.py" /opt/smartmeter/
sudo cp "$SCRIPT_DIR/relay.service" /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable relay

# Si no hay cable, el perfil queda listo y NetworkManager lo levantara al
# conectarlo. No se aborta la instalacion por ese caso normal.
if ! sudo nmcli --wait 15 connection up "$CONNECTION_NAME"; then
    echo "Ethernet sin enlace por ahora; se activara automaticamente al conectar el cable."
fi

sudo systemctl restart relay
echo "Smart meter instalado en modo plug-and-play."
echo "Pi: $ETHERNET_ADDRESS (DHCP) y $LINK_LOCAL_ADDRESS (respaldo automatico)."
echo "Receptor: Ethernet en modo automatico; no requiere IP manual."
echo "Ver estado: systemctl status relay"
echo "Ver datos:  journalctl -u relay -f"
