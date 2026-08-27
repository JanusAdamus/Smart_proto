#!/bin/bash
set -e

# Pi Zero haciendo de medidor: emite telegramas por el UART de los GPIO, que
# la Pi lectora lee como si vinieran del puerto P1 de un medidor real.
#
# Cableado (cruzado, y la masa es obligatoria):
#   Zero GPIO14 / TXD, pin 8  ->  lectora GPIO15 / RXD, pin 10
#   Zero GND,          pin 6  ->  lectora GND,          pin 6
# Nada de 5V ni 3V3 entre las dos: cada Pi con su propia alimentacion.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERIAL_PORT="${SMARTMETER_PORT:-/dev/serial0}"

source "$SCRIPT_DIR/uart_setup.sh"

sudo apt-get update
sudo apt-get install -y python3-serial

preparar_uart

id -u simulator >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin simulator
sudo usermod -aG dialout simulator
sudo mkdir -p /opt/smartmeter
sudo cp "$SCRIPT_DIR/meter_simulator.py" "$SCRIPT_DIR/dsmr.py" \
    "$SCRIPT_DIR/serial_link.py" /opt/smartmeter/

# El puerto se fija a mano: el simulador escribe y nadie le contesta, asi que
# no puede sondear cual es el bueno, y en la Zero no hay adaptador USB que
# delate cual es. Sin esto abriria todos los ttyS* que liste el sistema.
sudo tee /etc/default/smartmeter >/dev/null <<ENTORNO
SMARTMETER_PORT=$SERIAL_PORT
ENTORNO

sudo cp "$SCRIPT_DIR/systemd/simulator.service" /etc/systemd/system/simulator.service
sudo systemctl daemon-reload
sudo systemctl enable simulator

echo "Simulador instalado, emitiendo por $SERIAL_PORT."
echo "REINICIA la Pi: los cambios del arranque solo aplican al reiniciar."
echo "Despues: systemctl status simulator  /  journalctl -u simulator -f"
