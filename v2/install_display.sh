#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHERNET_INTERFACE="${SMARTMETER_ETHERNET_INTERFACE:-eth0}"
ETHERNET_ADDRESS="${SMARTMETER_ETHERNET_ADDRESS:-192.168.7.2/24}"
WIFI_INTERFACE="${SMARTMETER_WIFI_INTERFACE:-wlan0}"
AP_SSID="${SMARTMETER_AP_SSID:-smartmeter}"
CONNECTION_NAME="smartmeter-direct"
# A quien se conecta el enlace TCP. Por omision la Pi lectora; apuntandolo a
# otra maquina se consume el simulador de P1 (ver README, "Sin medidor real").
READER_HOST="${SMARTMETER_READER_HOST:-192.168.7.1}"

# Una clave fija en un repositorio no es una clave. Si no viene por entorno se
# genera una y se imprime al final, en vez de usar un valor por omision.
AP_PASSWORD="${SMARTMETER_AP_PASSWORD:-}"
if [ -z "$AP_PASSWORD" ]; then
    AP_PASSWORD="$(tr -dc 'a-z0-9' </dev/urandom | head -c 12)"
    GENERADA=1
fi

sudo apt-get update
sudo apt-get install -y python3 network-manager
sudo systemctl enable --now NetworkManager

# El kiosco es opcional: la pagina se sirve igual sin pantalla. En una imagen
# Lite no hay sesion grafica, y ahi Chromium serian centenares de MB que nunca
# van a dibujar nada. /etc/xdg/autostart solo existe con escritorio instalado.
CON_PANTALLA=""
if [ -d /etc/xdg/autostart ]; then
    CON_PANTALLA=1
    # El paquete cambio de nombre entre versiones de Raspberry Pi OS. Con
    # set -e, pedir el que no existe aborta la instalacion entera.
    sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
fi

if ! ip link show "$ETHERNET_INTERFACE" >/dev/null 2>&1; then
    echo "ERROR: no existe la interfaz $ETHERNET_INTERFACE. Disponibles:"
    ip -brief link
    exit 1
fi

# Enlace directo con la lectora. Ver la nota sobre 10.42.0.0/24 en
# install_reader.sh: ese rango es el del hotspot de abajo.
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

# AP para los espectadores. NetworkManager en modo compartido trae su propio
# DHCP, asi que no hacen falta hostapd ni dnsmasq configurados a mano.
sudo nmcli device wifi hotspot ifname "$WIFI_INTERFACE" \
    ssid "$AP_SSID" password "$AP_PASSWORD" || true
sudo nmcli connection modify Hotspot connection.autoconnect yes

id -u smartmeter >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin smartmeter
sudo mkdir -p /opt/smartmeter /var/lib/smartmeter
sudo cp "$SCRIPT_DIR/server.py" "$SCRIPT_DIR/store.py" "$SCRIPT_DIR/dsmr.py" \
    /opt/smartmeter/
sudo cp -r "$SCRIPT_DIR/static" /opt/smartmeter/
sudo chown -R smartmeter:smartmeter /var/lib/smartmeter

# server.py lee estas variables al arrancar; display.service toma el archivo.
# Escribirlo aqui es lo que permite cambiar de origen (Pi lectora o simulador)
# sin tocar la unidad ni el codigo.
sudo tee /etc/default/smartmeter >/dev/null <<ENTORNO
SMARTMETER_READER_HOST=$READER_HOST
SMARTMETER_READER_PORT=${SMARTMETER_READER_PORT:-4000}
ENTORNO

if [ -n "$CON_PANTALLA" ]; then
    sudo cp "$SCRIPT_DIR/kiosk.sh" /opt/smartmeter/kiosk.sh
    sudo chmod +x /opt/smartmeter/kiosk.sh
    sudo cp "$SCRIPT_DIR/systemd/smartmeter-kiosk.desktop" \
        /etc/xdg/autostart/smartmeter-kiosk.desktop
    # Oculta el puntero, que quieto sobre una pantalla de 3,5" tapa un digito.
    sudo apt-get install -y unclutter || true

    # Sin esto no hay nada automatico: el kiosco vive dentro de la sesion
    # grafica, y si la Pi arranca a consola y espera un login esa sesion no
    # existe. B4 es "Desktop Autologin" en raspi-config.
    if command -v raspi-config >/dev/null 2>&1; then
        sudo raspi-config nonint do_boot_behaviour B4 || \
            echo "No se pudo fijar el arranque al escritorio; hazlo con raspi-config."
    fi
fi

sudo cp "$SCRIPT_DIR/systemd/display.service" /etc/systemd/system/display.service
sudo systemctl daemon-reload
sudo systemctl enable display

if ! sudo nmcli --wait 15 connection up "$CONNECTION_NAME"; then
    echo "Ethernet sin enlace por ahora; se activara al conectar el cable."
fi

sudo systemctl restart display
echo "Pi de pantalla instalada en $ETHERNET_ADDRESS, leyendo de $READER_HOST."
echo "Dashboard: http://localhost:8080 (local) y por la red $AP_SSID."
if [ -n "$CON_PANTALLA" ]; then
    echo "Kiosco instalado: arranca solo con la sesion grafica (reinicia para verlo)."
else
    echo "Sin escritorio: no se instalo el kiosco. La pagina se sirve igual."
fi
if [ -n "$GENERADA" ]; then
    echo "Clave del AP generada: $AP_PASSWORD"
    echo "Guardala: no se vuelve a mostrar. Para fijar otra, reejecuta con"
    echo "SMARTMETER_AP_PASSWORD=... bash install_display.sh"
fi
echo "Estado: systemctl status display"
echo "Datos:  journalctl -u display -f"
