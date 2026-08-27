#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHERNET_INTERFACE="${SMARTMETER_ETHERNET_INTERFACE:-eth0}"
ETHERNET_ADDRESS="${SMARTMETER_ETHERNET_ADDRESS:-192.168.7.1/24}"
CONNECTION_NAME="smartmeter-direct"

sudo apt-get update
sudo apt-get install -y python3-serial network-manager
sudo systemctl enable --now NetworkManager

if ! ip link show "$ETHERNET_INTERFACE" >/dev/null 2>&1; then
    echo "ERROR: no existe la interfaz $ETHERNET_INTERFACE. Disponibles:"
    ip -brief link
    echo "Reintenta indicando la correcta, por ejemplo:"
    echo "SMARTMETER_ETHERNET_INTERFACE=enx1234 bash install_reader.sh"
    exit 1
fi

# Direccion fija, sin DHCP ni descubrimiento: son dos equipos en un cable.
# No se usa 10.42.0.0/24 a proposito: es el rango del hotspot que levanta la
# Pi con pantalla, y tener dos rutas al mismo prefijo rompe el enrutamiento.
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
    ipv4.method manual \
    ipv4.addresses "$ETHERNET_ADDRESS" \
    ipv4.never-default yes \
    ipv6.method disabled

id -u relay >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin relay
# La pertenencia a un grupo solo aplica a procesos nuevos: si este script se
# reejecuta en una Pi encendida, hace falta el restart del final.
sudo usermod -aG dialout relay
sudo mkdir -p /opt/smartmeter
sudo cp "$SCRIPT_DIR/relay.py" "$SCRIPT_DIR/serial_link.py" /opt/smartmeter/
sudo cp "$SCRIPT_DIR/systemd/relay.service" /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable relay

# Sin cable conectado el perfil queda listo y NetworkManager lo levanta solo.
# No se aborta la instalacion por ese caso, que es normal.
if ! sudo nmcli --wait 15 connection up "$CONNECTION_NAME"; then
    echo "Ethernet sin enlace por ahora; se activara al conectar el cable."
fi

sudo systemctl restart relay
echo "Pi lectora instalada en $ETHERNET_ADDRESS, relay en TCP 4000."
echo "Estado: systemctl status relay"
echo "Datos:  journalctl -u relay -f"
