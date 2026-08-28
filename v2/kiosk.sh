#!/bin/bash
# Abre el dashboard a pantalla completa en la pantalla que haya.
#
# Lo lanza /etc/xdg/autostart al abrir la sesion grafica, y esa es la razon de
# que sea un script de sesion y no un servicio del sistema: dentro de la sesion
# ya vienen resueltos DISPLAY o WAYLAND_DISPLAY y XDG_RUNTIME_DIR. Levantarlo
# como servicio obligaria a reconstruir ese entorno a mano y a adivinar cual de
# los dos servidores graficos esta corriendo.
#
# No hay que distinguir monitor HDMI de la TFT SPI: para el navegador las dos
# son la misma pantalla, y la pagina cambia de diseño sola segun el tamaño.

[ -r /etc/default/smartmeter ] && . /etc/default/smartmeter
PORT="${SMARTMETER_HTTP_PORT:-8080}"
URL="http://localhost:$PORT"

anotar() { logger -t smartmeter-kiosk "$*" 2>/dev/null; echo "kiosk: $*"; }

# Que salidas de video hay conectadas. No cambia nada de como se dibuja; existe
# para que un arranque sin imagen deje dicho por que en el journal, en vez de
# una pantalla negra sin explicacion.
pantallas() {
    local encontradas=""
    for estado in /sys/class/drm/card*-*/status; do
        [ -e "$estado" ] || continue
        [ "$(cat "$estado" 2>/dev/null)" = "connected" ] || continue
        encontradas="$encontradas $(basename "$(dirname "$estado")")"
    done
    # La TFT sobre SPI no es una salida DRM: aparece como framebuffer suelto,
    # normalmente fb1 y con el nombre del driver (fb_ili9486 y parecidos).
    for fb in /sys/class/graphics/fb[0-9]*; do
        [ -r "$fb/name" ] || continue
        # Con el tamaño: si el escritorio se dibuja mas grande que el panel,
        # el panel enseña una esquina y la pagina se ve cortada. Ese numero
        # es la unica forma de distinguirlo de un problema de la pagina.
        encontradas="$encontradas $(basename "$fb"):$(cat "$fb/name" 2>/dev/null)"
        encontradas="$encontradas@$(tr ',' 'x' <"$fb/virtual_size" 2>/dev/null)"
    done
    echo "${encontradas# }"
}

# El binario se llama chromium-browser o chromium segun la version del sistema.
# Se resuelve al arrancar y no al instalar, porque una actualizacion puede
# cambiarlo por debajo y el kiosco quedaria mudo sin decir por que.
NAVEGADOR=$(command -v chromium-browser || command -v chromium)
if [ -z "$NAVEGADOR" ]; then
    anotar "no hay chromium instalado; instala chromium-browser o chromium"
    exit 1
fi

anotar "sesion=${WAYLAND_DISPLAY:-${DISPLAY:-ninguna}} pantallas=$(pantallas)"

# systemd arranca display.service y la sesion grafica a la vez, asi que el
# servidor puede no estar listo todavia. Sin esta espera Chromium abre una
# pagina de error y se queda ahi hasta que alguien la refresque a mano.
ESPERA="${SMARTMETER_KIOSK_WAIT:-60}"
listo=""
for _ in $(seq 1 "$ESPERA"); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then listo=1; break; fi
    sleep 1
done
[ -n "$listo" ] || anotar "el dashboard no respondio en $ESPERA s; abro igual"

# El puntero quieto encima de una pantalla de 3,5 pulgadas tapa un digito
# entero, y en una tactil no hace falta para nada.
command -v unclutter >/dev/null 2>&1 && unclutter -idle 0 &

# Chromium no hace ventanas de navegador mas angostas que unos 400 px, ni en
# modo kiosco. En la TFT de 320 eso dibuja mas ancho que la pantalla y se
# pierde un quinto por la derecha. Una ventana de aplicacion (--app) si acepta
# el tamaño que se le pide. El tamaño sale del framebuffer en vez de estar
# fijo aqui, para que en un monitor HDMI grande la ventana lo siga ocupando
# entero: virtual_size ya viene como "ancho,alto", que es el formato exacto
# que espera --window-size.
FB_SIZE="${SMARTMETER_FB_SIZE_FILE:-/sys/class/graphics/fb0/virtual_size}"
TAMANO=$(cat "$FB_SIZE" 2>/dev/null)
case "$TAMANO" in
    [0-9]*,[0-9]*) VENTANA="--app=$URL --window-size=$TAMANO --window-position=0,0" ;;
    *) anotar "sin tamaño en $FB_SIZE; abro en modo kiosco"
       VENTANA="--kiosk $URL" ;;
esac

anotar "abriendo $URL con $NAVEGADOR ventana=${TAMANO:-kiosk}"
# --password-store=basic: sin el, Chromium pide abrir el llavero en cada
# arranque. Con autologin nadie escribe una contraseña, PAM no desbloquea el
# llavero de login y gnome-keyring saca su dialogo encima del kiosco. No
# guardamos ninguna credencial: el llavero sobra.
exec "$NAVEGADOR" $VENTANA --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --disable-features=Translate \
    --password-store=basic \
    --check-for-update-interval=31536000
