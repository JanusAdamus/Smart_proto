#!/bin/bash
# Prepara el UART de los GPIO para hablar DSMR a 115200. Lo usan los dos
# extremos del cable, el que emite y el que lee, porque el problema es el
# mismo de los dos lados. Se incluye con `source`, no se ejecuta suelto.
#
# Tres cosas hay que tocar en el arranque, y las tres por separado:
#
# 1. enable_uart: sin esto el UART no existe.
# 2. disable-bt: en la Zero W y en la Pi 3 en adelante, el Bluetooth se queda
#    con el PL011 y serial0 cae en el mini UART, cuya velocidad sigue al reloj
#    del core. A 115200 eso deriva y el otro extremo ve basura intermitente,
#    que es peor que no ver nada porque parece ruido de cable.
# 3. La consola serie: systemd levanta un getty en serial0 y pelea por el
#    puerto. Los telegramas salen mezclados con el prompt de login.

preparar_uart() {
    local boot_config="/boot/firmware/config.txt"
    [ -f "$boot_config" ] || boot_config="/boot/config.txt"

    sudo grep -q "^enable_uart=1" "$boot_config" \
        || echo "enable_uart=1" | sudo tee -a "$boot_config" >/dev/null
    sudo grep -q "^dtoverlay=disable-bt" "$boot_config" \
        || echo "dtoverlay=disable-bt" | sudo tee -a "$boot_config" >/dev/null

    sudo systemctl disable --now serial-getty@ttyAMA0.service 2>/dev/null || true
    sudo systemctl disable --now serial-getty@ttyS0.service 2>/dev/null || true
    for cmdline in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
        [ -f "$cmdline" ] || continue
        sudo sed -i 's/console=serial0,[0-9]* //' "$cmdline"
    done

    echo "UART preparado en $boot_config (aplica al reiniciar)."
}
