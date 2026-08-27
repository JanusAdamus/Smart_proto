# Prototipo 2: lectura de un puerto P1 real con dos Raspberry Pi

**Fecha:** 2026-08-27
**Estado:** Aprobado para implementación
**Ubicación del código:** `v2/` (el prototipo 1 queda congelado en `v1/` como referencia)
**Reemplaza a:** `v1/docs/superpowers/specs/2026-08-13-smart-meter-prototype-design.md`
y `v1/docs/superpowers/specs/2026-08-14-usb-serial-link-design.md`

## Contexto y objetivo

El prototipo 1 demostró la cadena completa con datos sintéticos: un generador en
Windows escribía telegramas propios por un adaptador USB a serie, una Raspberry
Pi los retransmitía por TCP y un dashboard Tkinter en una segunda computadora
Windows los mostraba.

El prototipo 2 cambia dos cosas de fondo. Primero, el origen deja de ser
necesariamente sintético: el sistema debe poder leer un puerto P1 real de un
medidor holandés, sin cambios de código. Segundo, la presentación se muda de una
aplicación de escritorio en Windows a una Raspberry Pi con pantalla, que además
sirve el mismo dashboard por WiFi a cualquier teléfono o laptop.

El objetivo es que Plan A (medidor real) y Plan B (simulador) sean el mismo
software corriendo sobre el mismo hardware, y que la única diferencia sea qué
cable está enchufado en el puerto USB de la Pi lectora.

### Hardware disponible

- Cable P1 comercial RJ12 a USB, con inversor y alimentación integrados.
- Dos Raspberry Pi, una de ellas con pantalla.
- Adaptadores USB a serie comunes, del prototipo 1.
- **No** hay acceso confirmado a un medidor con puerto P1 activo. Plan B es el
  escenario de trabajo; Plan A es la ruta que debe quedar lista para el día que
  haya acceso.

## Fundamentos del estándar

Todo lo que sigue proviene de *DSMR 5.0.2 P1 Companion Standard* (Netbeheer
Nederland, 26-02-2016) y condiciona el diseño.

**Conector y pines (§5.1).** El puerto P1 es un RJ12 hembra en el medidor. Pin 1
`+5V`, pin 2 `Data Request`, pin 3 `Data GND`, pin 4 sin conexión, pin 5 `Data`,
pin 6 `Power GND`.

**El medidor no habla si no se lo pide (§5.7.1).** El puerto se activa poniendo
`Data Request` en alto, entre 4,0 V y 5,5 V. Mientras el receptor mantenga esa
línea activada, el medidor transmite. Al soltarla, la transmisión se detiene de
inmediato. Un receptor no puede llevarla a 0 V: hay que dejarla en alta
impedancia.

**La línea de datos está invertida (§5.7.2 y §5.8).** `Data` es una salida open
collector, lógicamente invertida, con niveles de 0 V a 1 V para el estado bajo y
5 V para el alto. El UART de la Raspberry Pi trabaja a 3,3 V y sin inversión.
Conectar el RJ12 directo al GPIO no entrega datos: entrega silencio o basura.

**Velocidad y formato (§6.1).** 115200 baudios fijos, 8N1. El medidor emite un
telegrama completo cada segundo y debe terminar de transmitirlo dentro de ese
segundo.

**Estructura del telegrama (§6.2).** `/XXX5<Identificación> CR LF CR LF <Datos>
! <CRC> CR LF`. El CRC es un CRC16 con polinomio `x^16 + x^15 + x^2 + 1`, sin
XOR de entrada ni de salida, bit menos significativo primero, calculado sobre los
caracteres desde `/` hasta `!` inclusive, y representado como cuatro caracteres
hexadecimales.

**El orden y el conjunto de códigos OBIS no son fijos (§6.13).** El estándar dice
explícitamente que un dispositivo P1 debe interpretar los códigos que le lleguen
y no asumir cuáles ni en qué orden. Un bloque de datos individual puede llegar a
1024 caracteres y no está permitido partirlo.

### Consecuencia sobre el hardware

Como el cable P1 comercial ya trae el inversor, ata `Data Request` a +5 V por su
cuenta y expone un puerto serie estándar del lado USB, todo el problema eléctrico
queda resuelto por el cable. El diseño **no** incluye circuito inversor, ni
cambios en `config.txt`, ni `dtoverlay=disable-bt`, ni liberación de la consola
serie del GPIO. La Pi lectora ve un `/dev/ttyUSB*` corriente.

Ese cable es de recepción: su inversor es unidireccional y el TX del chip USB no
está conectado. No sirve para que una Pi se haga pasar por un medidor. Por eso el
simulador de Plan B usa adaptadores USB a serie comunes, como en el prototipo 1,
y el cable P1 queda reservado para Plan A.

## Arquitectura

```
Plan A:  [Medidor P1 activo] --RJ12--> [cable P1 USB] --\
                                                          >--> Pi #1 "lectora"
Plan B:  [PC simulador] --USB-serie--> [cable serie] ----/     /dev/ttyUSB*
                                                                    |
                                                          TCP 4000, bytes crudos
                                                                    |
                                                    Ethernet directo 192.168.7.0/24
                                                                    |
                                                              Pi #2 "pantalla"
                                                   parser DSMR -> SQLite -> HTTP 8080
                                                            /                  \
                                                  navegador en kiosco      AP WiFi
                                                   (pantalla local)    (teléfonos, laptops)
```

### Pi #1, la lectora

No sabe qué es DSMR. Sondea todos los puertos serie presentes, se queda con el
primero que emita un `/`, y retransmite los bytes tal cual a los clientes TCP
conectados en el puerto 4000.

Esto es `v1/relay.py` y `v1/serial_link.py` prácticamente sin cambios. La sonda
de `carries_telegrams` busca un `/` genérico, que es exactamente el primer
carácter de un telegrama DSMR real y también del simulador, así que la misma
detección automática sirve para los dos planes sin ninguna rama condicional.

Se elimina de esa copia la dependencia de `discovery.py`: con direcciones fijas
en el enlace directo, Zeroconf no aporta nada.

### Pi #2, la pantalla

Recibe el flujo TCP, enmarca telegramas, valida CRC, parsea, escribe en SQLite y
sirve una única página HTTP. La pantalla local abre esa misma página contra
`localhost`; los espectadores por WiFi la abren contra el AP. Un solo servidor y
un solo dashboard.

### Por qué la Pi #1 manda bytes crudos y no JSON

Parsear en el origen obligaría a tener el parser en las dos Pis, o a inventar un
formato de transporte propio que después hay que versionar cuando cambie. El
telegrama ya es ASCII autodescriptivo y ya viene protegido con un CRC calculado
por el medidor. Reenviarlo intacto mantiene esa protección válida hasta el
consumidor final y deja un único parser en todo el sistema.

### Por qué el servidor web vive en la Pi #2 y no en la Pi #1

El AP WiFi lo levanta la Pi con pantalla. Si el servidor viviera en la lectora,
la Pi #2 tendría que hacer de router entre `wlan0` y `eth0` con NAT para que los
teléfonos alcanzaran la web. Poniendo el servidor donde ya está el AP, los
clientes WiFi llegan directo y no hay nada que enrutar.

### Qué se hereda de v1 y qué se descarta

Sobrevive `crc16_arc`: la implementación de `v1/dsmr.py:16` es correcta contra
§6.2 del estándar y se copia sin tocar. Sobreviven `serial_link.py` completo y el
esqueleto de `relay.py`. Sobrevive la interfaz del simulador, con su selección de
puerto y su escritura a todos los adaptadores a la vez.

Se descarta `discovery.py`. Se descarta `dashboard.py` entero, incluidas las
aproximadamente 200 líneas de `v1/dashboard.py:30-220` que enumeran adaptadores
Ethernet de Windows y lanzan PowerShell elevado para asignar direcciones: existían
solo porque el dashboard corría en Windows. Se descarta el enmarcado y el parseo
de `v1/dsmr.py:70-100`, por las razones de la sección siguiente.

## Componentes

Estructura plana en `v2/`, siguiendo el estilo de `v1/`:

| Archivo | Dónde corre | Función |
| --- | --- | --- |
| `dsmr.py` | compartido | CRC16, generación, enmarcado y parser genérico OBIS |
| `serial_link.py` | Pi #1 y simulador | Detección y apertura de puertos serie |
| `relay.py` | Pi #1 | Serie a TCP |
| `meter_simulator.py` | PC o Pi (Plan B) | Generador con interfaz gráfica |
| `store.py` | Pi #2 | SQLite: escritura, consulta e higiene |
| `server.py` | Pi #2 | Cliente TCP, servidor HTTP y estado en vivo |
| `static/index.html` | Pi #2 | Dashboard, una sola página |
| `install_reader.sh` | Pi #1 | Instalación, red y servicio |
| `install_display.sh` | Pi #2 | Instalación, red, AP, kiosco y servicio |

### `dsmr.py`: parser genérico

El parser del prototipo 1 tiene el identificador del fabricante hardcodeado en la
expresión de enmarcado (`v1/dsmr.py:100`, `rb"/ISK5..."`) y una regex por cada
uno de los tres campos que entiende. Un medidor real puede identificarse como
`/KFM5KAIFA-METER`, `/Ene5\XS210` o `/XMX5LG...`, y emite alrededor de 35 líneas
OBIS. Ese parser no lee ningún medidor real.

El reemplazo no conoce ningún código de antemano. Toma cualquier línea con forma
`<código OBIS>(<valor>)(<valor>)...` y devuelve todos los valores como texto:

```python
{
    "ident": "ISk5\\2MT382-1000",
    "objects": {
        "1-0:1.8.1": ["123456.789*kWh"],
        "1-0:99.97.0": ["2", "0-0:96.7.19", "101208152415W", "0000000240*s", ...],
        "0-1:24.2.1": ["101209112500W", "12785.123*m3"],
    },
}
```

Un ayudante aparte convierte `"123456.789*kWh"` en `(123456.789, "kWh")`. El mapa
de códigos OBIS a nombres legibles vive en `server.py`, no en el parser: qué
mostrar es decisión de la presentación, no de la lectura.

Ventajas concretas: un medidor con tres fases, con gas en el canal 2 en vez del 1,
o con códigos que este documento no lista, funciona sin cambios. Y es menos código
que el enfoque por regex individuales.

**Enmarcado.** Se busca desde un `/` hasta el `!` seguido de cuatro hexadecimales
y CRLF, sin exigir nada del identificador. El buffer se limita a 16 KB: un
telegrama con el mensaje de texto de 1024 caracteres y un registro largo de cortes
no llega a eso, y sin el límite el ruido de línea de un medidor real hace crecer
el buffer sin fin. Al desbordar se descarta lo acumulado hasta el `/` más reciente.

**Validación.** El CRC se calcula sobre los bytes desde el `/` hasta el `!`
inclusive, tal como llegaron. Un telegrama con CRC inválido se descarta y se
cuenta; no se parsea ni se guarda. El contador de descartes se expone en el
dashboard, porque en un medidor real es el primer síntoma de un cable con ruido.

### `store.py`: SQLite

La base vive en `/var/lib/smartmeter/readings.db`, creada por `install_display.sh`
con dueño el usuario del servicio. Una sola tabla, con la marca de tiempo del
medidor como clave primaria:

```sql
CREATE TABLE IF NOT EXISTS readings (
  ts            INTEGER PRIMARY KEY,
  power_in      REAL, power_out     REAL,
  energy_in_t1  REAL, energy_in_t2  REAL,
  energy_out_t1 REAL, energy_out_t2 REAL,
  voltage_l1    REAL, voltage_l2    REAL, voltage_l3    REAL,
  current_l1    REAL, current_l2    REAL, current_l3    REAL,
  gas           REAL
);
```

Usar `ts` como clave primaria da la deduplicación gratis con `INSERT OR REPLACE`:
si el reloj del medidor repite un segundo, la fila se sobrescribe en vez de
duplicarse.

**Conversión de la marca de tiempo.** El campo `0-0:1.0.0` llega como
`YYMMDDhhmmssX`, hora local holandesa, donde `X` vale `S` si el horario de verano
está activo y `W` si no (§6.4). Se convierte a segundos unix interpretándolo en
`Europe/Amsterdam` con `zoneinfo`. La bandera no es decorativa: en la madrugada
del cambio de otoño la hora entre las 02:00 y las 03:00 ocurre dos veces, y `S` o
`W` es lo único que distingue una de la otra. Se traduce a `fold=0` para `S` y
`fold=1` para `W`. Sin eso, esa hora produciría colisiones de clave primaria y
perdería una hora de lecturas una vez al año.

**No se guarda el telegrama crudo.** Un telegrama pesa cerca de 1 KB y llegan
86.400 por día; guardarlos sería alrededor de 86 MB diarios sobre una tarjeta SD.
El último telegrama crudo se mantiene en memoria para el panel del dashboard que
lo muestra, y nada más.

**Configuración de la base.** `PRAGMA journal_mode=WAL` y `PRAGMA
synchronous=NORMAL`. Sin eso, una escritura por segundo significa un `fsync` por
segundo contra la SD, que es desgaste innecesario para datos de prototipo.

**Retención.** Un `DELETE FROM readings WHERE ts < ?` cada hora, conservando siete
días. Sin tablas de agregados: la gráfica pide como mucho unas horas y una
consulta por rango sobre la clave primaria es instantánea a esta escala. Si
alguna vez la gráfica se pone lenta, ahí se agregan.

### `server.py`: transporte y HTTP

Dos responsabilidades, en hilos separados.

**Cliente TCP.** Se conecta a `192.168.7.1:4000` en bucle, con reintento y
espera creciente. Alimenta el enmarcador, valida, parsea, actualiza el estado en
memoria y escribe en SQLite. Si el enlace se cae, marca el estado como
desconectado y sigue: el dashboard continúa sirviendo la historia guardada.

**Servidor HTTP.** `http.server.ThreadingHTTPServer` de la biblioteca estándar,
en el puerto 8080. Sin Flask ni FastAPI: son tres rutas.

| Ruta | Respuesta |
| --- | --- |
| `GET /` | `static/index.html` |
| `GET /api/latest` | Última lectura parseada, telegrama crudo, estado del enlace, telegramas recibidos y descartados |
| `GET /api/history?minutes=N` | Arreglo de lecturas de los últimos N minutos |

**Se descarta Server-Sent Events a propósito.** Un `fetch` cada segundo desde el
navegador es menos código en las dos puntas, no deja conexiones largas abiertas
que haya que limpiar, y se recupera solo cuando un teléfono se va de la WiFi y
vuelve. Un pedido por segundo por espectador no justifica el costo de SSE.

### `static/index.html`: dashboard

Una página, sin dependencias externas. La Pi puede no tener internet, así que no
hay CDNs ni librerías de gráficas: la curva de potencia se dibuja a mano sobre un
`<canvas>`.

Muestra potencia instantánea, energía acumulada por tarifa en entrega y en
devolución, voltaje y corriente por fase, la última lectura de gas si el medidor
la envía, la gráfica de potencia, el telegrama crudo completo más reciente, y el
estado del enlace con los contadores de telegramas válidos y descartados.

El diseño es legible desde una pantalla pequeña de Raspberry Pi y desde un
teléfono. Los campos de los que el medidor no informa nada simplemente no se
dibujan, porque el conjunto de códigos OBIS varía entre medidores.

### `meter_simulator.py`: Plan B

Se hereda de `v1/meter_simulator.py` la interfaz gráfica, la selección de puerto
y la escritura simultánea a todos los adaptadores USB detectados, que ya resuelve
el problema de no saber cuál de los puertos COM es el cable.

Lo que cambia es el telegrama: pasa de cinco líneas propias a un telegrama DSMR
5.0.2 completo, con la estructura del ejemplo de §6.13. Identificación, versión
`1-3:0.2.8(50)`, marca de tiempo, identificador de equipo, los cuatro registros de
energía por tarifa, indicador de tarifa, potencia entregada y recibida,
contadores de cortes y de sags y swells, mensaje de texto, voltaje, corriente y
potencia por cada una de las tres fases, y un bloque de gas por M-Bus.

Los valores evolucionan de forma creíble: la potencia como una caminata aleatoria
acotada, la energía monótonamente creciente, y el gas incrementándose cada cinco
minutos con su marca de tiempo de captura, que es como se comporta un medidor de
gas real sobre M-Bus.

## Configuración de red

### Enlace directo entre las Pis

Direcciones fijas, sin DHCP ni descubrimiento: Pi #1 en `192.168.7.1/24` y Pi #2
en `192.168.7.2/24`, como perfil persistente de NetworkManager creado por cada
instalador.

**El rango no es arbitrario.** El hotspot de NetworkManager usa `10.42.0.0/24`
por omisión. Si el enlace Ethernet usara ese mismo prefijo, la Pi #2 tendría dos
rutas hacia la misma red y el enrutamiento fallaría de forma intermitente y
difícil de diagnosticar. `192.168.7.0/24` no colisiona con el hotspot ni con los
rangos domésticos habituales.

### AP WiFi en la Pi #2

NetworkManager levanta el punto de acceso por sí solo, sin `hostapd` ni
configuración de `dnsmasq`: en modo compartido ya provee DHCP a los clientes.

```bash
nmcli device wifi hotspot ifname wlan0 ssid smartmeter password "$SMARTMETER_AP_PASSWORD"
nmcli connection modify Hotspot connection.autoconnect yes
```

La clave la toma el instalador de la variable `SMARTMETER_AP_PASSWORD`. Si no
está definida, genera una aleatoria y la imprime al terminar, en vez de usar una
por omisión: una clave fija en un repositorio no es una clave.

Los espectadores se conectan a la red `smartmeter` y abren la dirección de la Pi
en el puerto 8080.

### Kiosco en la pantalla

El instalador escribe `/etc/xdg/autostart/smartmeter-kiosk.desktop`, que es el
mecanismo que respetan tanto las imágenes de Raspberry Pi OS con LXDE como las de
Bookworm con labwc, y lanza Chromium contra `http://localhost:8080` con
`--kiosk --noerrdialogs --disable-infobars`.

Chromium se niega a arrancar si el servidor todavía no responde, así que la
entrada envuelve el lanzamiento en una espera corta a que el puerto 8080 acepte
conexiones. Es la diferencia entre una pantalla que muestra el dashboard al
encender y una que muestra una página de error hasta que alguien la refresque a
mano.

### Servicios

`relay.service` en la Pi #1 y `display.service` en la Pi #2, ambos habilitados
para arrancar solos y reiniciarse ante fallos. El usuario del servicio de la Pi #1
se agrega al grupo `dialout`.

## Manejo de errores

El principio es que ningún fallo de una capa apague las de abajo, y que todo
fallo sea visible en la pantalla en lugar de convertirse en silencio.

**Sin puerto serie en la Pi #1.** `wait_and_open` reintenta indefinidamente y
registra en el journal qué puertos probó y cuáles no traían telegramas. Es el
comportamiento heredado de v1 y ya funciona con hardware real.

**Puerto desconectado en caliente.** El bucle de lectura captura el error, cierra
y vuelve a la búsqueda. Sigue valiendo lo aprendido en v1: Windows no falla el
`write()` de un adaptador desenchufado, así que del lado del simulador la
detección se hace preguntando por la lista de puertos, no por el resultado de la
escritura.

**Enlace Ethernet caído.** La Pi #2 marca el estado como desconectado, lo muestra
en el dashboard y sigue sirviendo la historia de SQLite. Reintenta con espera
creciente hasta un tope.

**CRC inválido.** El telegrama se descarta, se incrementa un contador y ese
contador se muestra. Con un medidor real es la primera señal de un cable con
ruido o demasiado largo.

**Campo OBIS ausente o con formato inesperado.** Se guarda `NULL` en esa columna
y el dashboard omite el bloque. Un medidor monofásico no informa L2 ni L3, y eso
es normal, no un error.

**Base de datos bloqueada o disco lleno.** El fallo de escritura se registra y se
descarta esa lectura; el estado en vivo y el dashboard siguen funcionando. Los
datos en tiempo real nunca dependen de que SQLite esté sano.

## Pruebas

Todo se prueba sin hardware físico. Los puertos serie se simulan con un pty y el
enlace TCP con sockets en `localhost`.

**`test_dsmr.py`.** Ida y vuelta entre generación y parseo. Parseo del telegrama
de ejemplo de §6.13 del estándar, comprobando que se extraen las tres fases, el
registro de cortes y el bloque de gas. Rechazo de CRC inválido. Enmarcado
correcto cuando un telegrama llega partido en varios trozos y cuando llegan dos
pegados. Descarte al desbordar el límite de 16 KB. Identificadores de fabricante
distintos, para asegurar que no quedó ningún `/ISK5` implícito.

Nota sobre el telegrama de ejemplo: en el PDF la línea `0-0:96.13.0` viene
partida por el salto de página, así que el CRC publicado `EF2F` no puede darse por
válido sobre el texto reconstruido. El fixture usa la estructura del estándar con
el CRC recalculado, y una prueba aparte verifica la función de CRC contra un
vector conocido.

**`test_store.py`.** Inserción, sobrescritura ante marca de tiempo repetida,
consulta por rango, y borrado por retención.

**`test_server.py`.** Las tres rutas contra un servidor levantado en un puerto
efímero, incluido el comportamiento con el enlace caído.

**`test_relay.py`.** Heredado de v1, casi sin cambios.

**`test_integration_e2e.py`.** La cadena entera sin hardware: simulador escribe en
un pty, el relay lo lee y lo sirve por TCP, el servidor lo consume, lo parsea, lo
guarda y lo devuelve por HTTP.

## Alcance y limitaciones

Esto es un prototipo técnico, no un producto certificado para medición ni para
facturación.

El dashboard no tiene autenticación. Vive en una red aislada creada por la propia
Pi y cualquiera que se conecte al AP puede verlo. Para un uso más allá de la demo
haría falta al menos una clave y HTTPS.

El tráfico TCP entre las dos Pis no está cifrado ni autenticado. Es un cable
directo entre dos equipos, pero sigue siendo texto plano.

Plan A no se puede verificar de extremo a extremo hasta que haya acceso a un
medidor con P1 activo. Lo que sí se verifica desde ahora es que el software no
asume nada del identificador del medidor ni del conjunto de códigos OBIS, que es
donde falló el prototipo 1.

El comportamiento con medidores DSMR anteriores a la versión 4 queda fuera de
alcance: usan 9600 baudios y 7E1 en vez de 115200 y 8N1. Agregarlos sería sondear
las dos configuraciones al abrir el puerto, pero no hay ningún medidor de esos a
mano para probarlo y no se especifica lo que no se puede verificar.

Los valores del simulador son sintéticos y no representan un perfil de consumo
real de ninguna vivienda.
