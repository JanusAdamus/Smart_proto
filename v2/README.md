# Prototipo 2 — puerto P1 real, dos Raspberry Pi

Lee el puerto P1 de un medidor holandés desde una Raspberry Pi y muestra el
consumo en vivo en una segunda Pi con pantalla, que además sirve el mismo
dashboard por WiFi a los teléfonos que se conecten a su red.

El código de este directorio es autónomo. `v1/` quedó congelado: nada de aquí
lo importa.

## Los tres planes

El software es el mismo en los tres. Lo único que cambia es de dónde salen los
bytes.

```
Plan A:  [Medidor P1 activo] --RJ12--> [cable P1 USB] --\
                                                          >--> Pi #1 "lectora"
Plan B:  [PC simulador] --USB-serie--> [cable serie] ----/     /dev/ttyUSB*
                                                                    |
                                                          TCP 4000, bytes crudos
                                                                    |
Plan C:  [PC con p1_source.py] --------------------------> TCP 4000, bytes crudos
                                                                    |
                                                    Ethernet directo 192.168.7.0/24
                                                                    |
                                                              Pi #2 "pantalla"
                                                   parser DSMR -> SQLite -> HTTP 8080
                                                            /                  \
                                                  navegador en kiosco      AP WiFi
                                                   (pantalla local)    (teléfonos, laptops)
```

- **Plan A** — medidor real, con un cable P1 comercial o con el inversor
  propio sobre los GPIO (ver «Advertencia de hardware»).
- **Plan B** — una computadora hace de medidor por USB-serie: `meter_simulator.py`
  escribe telegramas en el puerto, la Pi lectora los lee como si vinieran del
  medidor. Necesita dos adaptadores USB-serie y un cable entre ellos.
- **Plan B-UART** — una Pi Zero hace de medidor y le habla a la lectora por el
  UART de los GPIO, sin adaptadores USB de por medio. Es el Plan B con la
  computadora reemplazada por una Pi: `meter_simulator.py --headless`.
- **Plan C** — una computadora hace de medidor **y** de Pi lectora a la vez:
  `p1_source.py` sirve el flujo directamente por TCP 4000, goteando el
  telegrama a 115200 baudios como haría el cable. Sin cables ni adaptadores; la
  Pi de pantalla no distingue este caso del Plan A.

## Advertencia de hardware

El puerto P1 entrega una señal **invertida** de 5 V open collector y no
transmite nada hasta que la línea *Data Request* está en alto (§5.7 del
estándar). Conectar el RJ12 directo al GPIO de la Pi **no funciona**: hace
falta un inversor y mantener esa línea alta. Hay dos formas de resolverlo, y el
software es el mismo en las dos.

**A1 — cable P1 comercial.** Trae el inversor y el Data Request adentro y expone
un `/dev/ttyUSB*` corriente. Es de recepción: no sirve para simular un medidor,
para eso están el Plan B y el Plan C.

**A2 — inversor propio sobre los GPIO.** Un NPN en emisor común invierte `Data`
y entrega el UART ya en 3,3 V a GPIO15 (RXD). El Data Request queda atado a los
5 V de la Pi, que es lo mismo que hace el cable comercial: siempre alto, nunca
forzado a 0 V.

| P1 (RJ12) | Circuito | Pi lectora |
|---|---|---|
| pin 5 `Data` | 1 kΩ a la base, y 1 kΩ de pull-up a 3V3 | — |
| — | colector, con 1 kΩ de pull-up a 3V3 | GPIO15 / RXD — pin 10 |
| pin 2 `Data Request` | directo | 5 V — pin 2 |
| pines 3 y 6 `GND` | emisor | GND — pin 6 |

Los 5 V solo van al Data Request: al GPIO nunca le llega más de 3,3 V. El
medidor también ofrece +5 V en el pin 1; tomarlos de la Pi evita depender de él.

`install_reader.sh` ya deja el UART de los GPIO listo (es lo mismo que necesita
el Plan B-UART) y `relay.py` sondea todos los puertos, así que encuentra
`/dev/ttyAMA0` sin configurar nada. Hay que reiniciar después de instalar: los
cambios de `config.txt` solo aplican al arrancar.

Con el circuito armado y el medidor desconectado, la línea queda en bajo
permanente y el UART ve un *break* continuo: bytes nulos, ningún `/`, y el
sondeo sigue buscando. Es el mismo síntoma que un medidor mudo.

## Requisitos

| Dónde | Qué |
|---|---|
| Pi lectora | Raspberry Pi OS con NetworkManager, Python 3.11+, `pyserial` |
| Pi de pantalla | Raspberry Pi OS con NetworkManager, Python 3.11+, Chromium |
| PC simulador (Plan B) | Python 3.11+, `pyserial`, Tk (viene con Python) |
| Pi Zero simulador (Plan B-UART) | Raspberry Pi OS Lite, Python 3.11+, `pyserial`. Sin Tk: corre headless |
| PC simulador (Plan C) | Python 3.11+, `pyserial` (lo arrastra `relay.py`) |

La Pi de pantalla no necesita `pyserial`: solo consume TCP.

## Instalación

```bash
# En la Pi #1 (lectora)
bash install_reader.sh

# En la Pi #2 (pantalla)
bash install_display.sh
```

Ambos scripts fijan la IP del cable Ethernet, crean el usuario del servicio,
copian los módulos a `/opt/smartmeter` y dejan el servicio systemd habilitado.
`install_display.sh` además levanta el AP WiFi y el kiosco de Chromium, e
imprime al final la clave del AP si no se le pasó una.

Después, según el plan:

```bash
# Plan B, en la computadora
python meter_simulator.py

# Plan C, en la computadora
python p1_source.py
```

En Plan C hay que decirle a la Pi de pantalla dónde está esa computadora:

```bash
sudo SMARTMETER_READER_HOST=192.168.7.9 bash install_display.sh
```

o, sin reinstalar, editando `/etc/default/smartmeter` y reiniciando el servicio
con `sudo systemctl restart display`.

## Pi Zero como medidor, por UART

```bash
# En la Pi Zero
bash install_simulator.sh
sudo reboot
```

**Cableado, cruzado y con masa obligatoria.** Nada de 5 V ni 3V3 entre las dos:
cada Pi con su propia alimentación.

| Pi Zero (emite) | Pi lectora (lee) |
|---|---|
| GPIO14 / TXD — pin 8 | GPIO15 / RXD — pin 10 |
| GND — pin 6 | GND — pin 6 |

`install_simulator.sh` prepara el UART y deja el servicio `simulator` andando.
`install_reader.sh` hace la misma preparación del otro lado, así que la lectora
ya queda lista para recibir por GPIO además de por USB. Las dos necesitan
reiniciar: los cambios de `config.txt` y `cmdline.txt` solo aplican al arrancar.

Lo que esa preparación resuelve, y que si no muerde en silencio:

- El getty de la consola serie pelea por el mismo puerto y mezcla el prompt de
  login con los telegramas.
- En la Zero W el Bluetooth se queda con el PL011 y `serial0` cae en el mini
  UART, cuya velocidad sigue al reloj del core. A 115200 eso deriva y del otro
  lado se ven bytes rotos intermitentes, que parecen ruido de cable.

El simulador no puede sondear —escribe y nadie le contesta—, así que el puerto
va fijo en `/etc/default/smartmeter` (`SMARTMETER_PORT=/dev/serial0`). La
lectora sí sondea: prueba todos los puertos y se queda con el que manda un `/`.

## Conexión

Cable Ethernet entre las dos Pis. En la lectora, el cable P1 (Plan A), el
USB-serie (Plan B) o el UART de la Zero (Plan B-UART). El dashboard aparece
solo en la pantalla al arrancar.

**La dirección depende de por dónde llegues.** La Pi de pantalla tiene dos
redes y una IP en cada una:

| Desde dónde | URL |
|---|---|
| Su propia pantalla (kiosco) | `http://localhost:8080` |
| Un teléfono en la red WiFi `dashboard` | `http://10.42.0.1:8080` |
| La Pi lectora, por el cable Ethernet | `http://192.168.7.2:8080` |

`10.42.0.1` es la dirección que NetworkManager le da al hotspot en modo
compartido; `192.168.7.2` vive solo en el cable entre las dos Pis y desde el
WiFi no se llega. Si no estás seguro, `ip -brief addr` en la Pi de pantalla las
lista todas.

## La pantalla de 3,5" sobre los GPIO

El dashboard tiene dos diseños en un solo archivo. Por debajo de 560 px de
ancho (o 430 px de alto) pasa al modo compacto, pensado para la TFT SPI de
480×320 y de paso para un teléfono en vertical:

- El consumo actual queda fijo arriba, se mire la pestaña que se mire.
- Cuatro pestañas —Now, Energy, Phases, Telegram— de 44 px de alto, que es lo
  mínimo para acertarle con el dedo en una táctil **resistiva**, que perdona
  bastante menos que una capacitiva.
- Tocar el gráfico rota la ventana entre 15, 60 y 360 minutos. Sin teclado ni
  ratón es la única forma de cambiar el rango.
- Los números se reescriben en su sitio en vez de rehacer el HTML. Sobre SPI el
  refresco va a unos pocos fps, y repintar la página entera cada segundo se ve
  como un parpadeo continuo.

### El kiosco arranca solo

`install_display.sh` deja el arranque configurado al escritorio con autologin
(`raspi-config nonint do_boot_behaviour B4`) e instala `kiosk.sh` en
`/etc/xdg/autostart`. Al abrir la sesión gráfica el script espera a que el
dashboard responda y abre Chromium a pantalla completa, en la pantalla que
haya: no hay que decirle si es el HDMI o la TFT, porque para el navegador son
la misma pantalla y la página cambia de diseño sola según el tamaño.

Es un script de sesión y no un servicio del sistema a propósito: dentro de la
sesión ya vienen resueltos `DISPLAY` o `WAYLAND_DISPLAY` y `XDG_RUNTIME_DIR`.
Un servicio tendría que reconstruir ese entorno a mano y adivinar cuál de los
dos servidores gráficos está corriendo.

Si la pantalla queda negra, el motivo está en el journal:

```bash
journalctl -t smartmeter-kiosk -n 20
```

Anota la sesión que encontró y las salidas de vídeo conectadas, que es
justamente lo que no se puede deducir mirando una pantalla apagada.

Con el driver GPIO puesto **el HDMI deja de dar señal**, así que conviene
instalarlo cuando ya no haga falta el monitor grande. La pantalla es de 320×480
nativos: para usarla apaisada hay que rotarla (el driver del fabricante trae el
parámetro; si no, `display_rotate` en `config.txt`). Y la resistiva necesita
calibración una vez.

### Si la imagen se ve cortada

Tres causas, en orden de probabilidad. Las tres se ven igual desde fuera, y el
tamaño que anota el kiosco al arrancar las separa:

```bash
journalctl -t smartmeter-kiosk -n 20 --no-pager -o cat
```

**1. La ventana es más ancha que la pantalla.** Chromium no hace ventanas de
navegador de menos de unos 400 px, ni en modo kiosco: en un panel de 320 px se
pierde justo un quinto por la derecha. Por eso `kiosk.sh` abre una ventana de
aplicación (`--app`) del tamaño exacto del framebuffer en vez de un kiosco, y
solo cae al kiosco de siempre si no consigue leer ese tamaño. Si la línea del
log dice `ventana=kiosk` en una pantalla pequeña, ese es el problema y falta
`/sys/class/graphics/fb0/virtual_size`.

**2. El marco del panel tapa el borde.** Milímetros, no un quinto de pantalla.
La perilla es `--inset` al principio de `static/index.html`: 12 px por
omisión, subir si sigue comiéndose el borde, bajar si sobra hueco. Es una
medida física del ejemplar que tengas, no un valor de diseño.

**3. El escritorio se dibuja más grande que el panel.** Pasa con los montajes
que copian fb0 a fb1 con `fbcp`. La línea del log lo canta:
`pantallas= fb0:BCM2708 FB@1920x1080 fb1:fb_ili9486@480x320`. Si los dos
tamaños coinciden, no es esto. Se arregla fijando la resolución del escritorio
al tamaño del panel, con KMS al final de la línea única de
`/boot/firmware/cmdline.txt`:

```
video=HDMI-A-1:480x320@60D
```

o, si el vendedor del panel dejó el KMS comentado en `config.txt`, con la
versión antigua del mismo ajuste:

```
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt=480 320 60 1 0 0 0
```

Aparte de las tres: si el panel sale de pie —320 de ancho por 480 de alto— y
lo quieres apaisado, lo que falta es rotarlo, `rotate=90` en el `dtoverlay` del
driver o `display_rotate=1` en `config.txt`. Al revés también vale: el modo
compacto funciona igual en vertical, que es como viene de fábrica.

El resto del sistema no se entera: la pantalla es un asunto de la Pi de
pantalla y del navegador, no del servidor.

## Sin medidor real: p1meter.dev

[p1meter.dev](https://github.com/mijnverbruik/p1meter.dev) es un simulador de
puerto P1 escrito en Elixir que emite telegramas DSMR 5.0 por TCP crudo, con el
goteo de un cable a 115200 baudios. Sirve de control externo: lo escribió otra
gente, en otro lenguaje, y nuestro parser lo lee sin tocar una línea.

Contra la instancia pública:

```bash
SMARTMETER_READER_HOST=p1meter.dev SMARTMETER_READER_PORT=8080 python server.py
```

Contra una copia local (necesita Elixir, o Docker con el `Dockerfile` del
repositorio; sirve el TCP en el puerto 8080):

```bash
git clone https://github.com/mijnverbruik/p1meter.dev.git
cd p1meter.dev && mix setup && mix phx.server
```

Su medidor informa 17 objetos OBIS en vez de los 37 que emite el nuestro: sin
tensión ni corriente por fase, como un monofásico. El dashboard omite esas
tarjetas y sigue andando. `p1meter_dev_sample.txt` es un telegrama capturado de
ese flujo, y `test_p1_source.py` lo pasa por toda la cadena.

Dos cosas a tener en cuenta con él: su reloj va unas horas atrasado respecto al
real, y marca las horas de verano con la bandera `W`. Ninguna de las dos rompe
nada —la ventana del gráfico se ancla en la última lectura y no en el reloj del
sistema, justo por esto—, pero explican por qué la hora del telegrama no
coincide con la del reloj de pared.

`p1_source.py` hace lo mismo en Python y sin instalar Elixir, así que para el
uso diario es el camino corto; p1meter.dev vale como verificación contra una
implementación que no es nuestra.

## Variables de entorno

| Variable | Dónde | Por omisión | Para qué |
|---|---|---|---|
| `SMARTMETER_PORT` | Pi lectora, PC, Pi Zero | — | Fuerza un puerto serie en vez de sondear. En la Zero, `/dev/serial0` |
| `SMARTMETER_ETHERNET_INTERFACE` | instaladores | `eth0` | Interfaz del cable entre Pis |
| `SMARTMETER_ETHERNET_ADDRESS` | instaladores | `192.168.7.1/24` (lectora), `.2/24` (pantalla) | IP fija de ese cable |
| `SMARTMETER_WIFI_INTERFACE` | `install_display.sh` | `wlan0` | Interfaz del AP |
| `SMARTMETER_AP_SSID` | `install_display.sh` | `dashboard` | Nombre de la red WiFi |
| `SMARTMETER_AP_PASSWORD` | `install_display.sh` | se genera una | Clave del AP |
| `SMARTMETER_READER_HOST` | Pi de pantalla | `192.168.7.1` | A quién se conecta el enlace TCP |
| `SMARTMETER_READER_PORT` | Pi de pantalla | `4000` | Puerto de ese enlace |
| `SMARTMETER_HTTP_PORT` | Pi de pantalla | `8080` | Puerto del dashboard |
| `SMARTMETER_DB` | Pi de pantalla | `/var/lib/smartmeter/readings.db` | Ruta de la base SQLite |

En la Pi de pantalla, `display.service` lee `/etc/default/smartmeter`, que
`install_display.sh` deja escrito.

## Pruebas

```bash
cd v2 && python -m pytest -q
```

`test_integration_e2e.py` trae dos variantes de la cadena completa. La que usa
un pty como cable serie necesita `os.openpty` y **se salta en Windows**; hay que
correrla en la Pi antes de dar nada por terminado. La otra recorre la misma
cadena con un puerto serie de mentira y corre en cualquier plataforma.

## Diagnóstico

| Síntoma | Dónde mirar |
|---|---|
| El dashboard dice "No link to the reader Pi" | El cable Ethernet, y `systemctl status relay` en la lectora |
| El contador de rechazados sube | Ruido en el cable serie, o un cable demasiado largo |
| El relay no encuentra puerto | `journalctl -u relay -f` lista los puertos que probó |
| Plan A: el sondeo no encuentra el puerto | ¿Reiniciaste tras `install_reader.sh`? ¿Data Request a 5 V? Sin él el medidor calla |
| Por UART no llega nada | ¿Reiniciaste las dos Pis? ¿TX contra RX (cruzado) y las masas unidas? `journalctl -u simulator -f` en la Zero |
| Por UART llegan bytes rotos a ratos | El mini UART deriva: comprueba que `dtoverlay=disable-bt` quedó en `config.txt` y reinicia |
| La pantalla muestra un error en vez del dashboard | `systemctl status display` |
| La imagen se ve cortada en el panel | `journalctl -t smartmeter-kiosk`: la línea `ventana=`; ver "Si la imagen se ve cortada" |
| Al arrancar pide la contraseña del llavero | Ya no debería: `kiosk.sh` pasa `--password-store=basic`. Si insiste, `rm ~/.local/share/keyrings/login.keyring` y reiniciar |
| Llegan telegramas pero el gráfico está vacío | El medidor no informa ese campo; mirar el telegrama crudo al pie del dashboard |

## Alcance y limitaciones

- No es un producto certificado: es un prototipo de demostración.
- El dashboard no tiene autenticación. Quien esté en la red lo ve.
- El TCP entre las dos Pis va en texto plano, sobre un cable directo.
- Plan A no está verificado contra hardware real: no hubo acceso a un medidor
  con el puerto P1 activo.
- DSMR anterior a la versión 4 (9600 baudios, 7E1) queda fuera de alcance.
