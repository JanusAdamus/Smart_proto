# Prototipo Smart Meter → Raspberry Pi → Dashboard

**Fecha:** 2026-08-13
**Estado:** Aprobado para implementación

## Contexto y objetivo

Prototipo para demostrar viabilidad de un producto que lee datos de un smart
meter (protocolo P1/DSMR, estándar NL/BE), los transporta a través de una
Raspberry Pi hasta otra computadora, y los muestra en vivo — sin persistir
datos en ningún punto de la cadena. El objetivo del prototipo es la demo en
vivo, no el almacenamiento ni la venta de datos (eso queda fuera de alcance).

Requisito no negociable: **"fool proof"**. Cualquiera de las tres piezas debe
poder operarse sin conocimiento técnico — doble clic o encender y listo, sin
IPs, sin configuración, sin comandos.

No hay medidor físico real disponible todavía, por lo que el "medidor" del
prototipo es un simulador que genera telegramas DSMR válidos. La Raspberry Pi
física llega el 2026-08-14 y las pruebas end-to-end con hardware real ocurren
ese mismo día — este documento es la referencia para esas pruebas.

## Arquitectura

```
PC1 (Windows)              Raspberry Pi (headless)         PC2 (Windows)
meter_simulator.exe   TCP  relay.py (systemd)         TCP  dashboard.exe
puerto 3000          ───▶  cliente de PC1,             ───▶ cliente de Pi,
anuncia servicio            servidor puerto 4000,            parsea en RAM,
"_metersim._tcp"             anuncia servicio                muestra vivo
                             "_smartmeter._tcp"
```

Todo el tráfico es texto plano DSMR sobre TCP, línea por línea. Ningún
componente escribe a disco ni a base de datos.

## Formato del telegrama (subset DSMR 5.0)

Para que el simulador, el relay y el parser sean interoperables y el trabajo
de mañana con hardware real tenga referencia exacta, el telegrama usa este
subset de OBIS codes real (formato idéntico al de un medidor DSMR 5.0 real,
aunque los valores sean sintéticos):

```
/ISK5\2MT382-1000
0-0:1.0.0(260813120000W)
1-0:1.7.0(002.345*kW)
1-0:1.8.1(000671.578*kWh)
1-0:32.7.0(230.4*V)
!5091
```

- Línea 1: identificación del medidor, fija (`/ISK5\2MT382-1000`).
- `0-0:1.0.0`: timestamp `YYMMDDhhmmssX` (X = `W` invierno / `S` verano).
- `1-0:1.7.0`: potencia activa instantánea importada, `NNN.NNN*kW`.
- `1-0:1.8.1`: energía activa acumulada importada, `NNNNNN.NNN*kWh`, monótona
  creciente (nunca baja, ni siquiera al reiniciar el simulador dentro de una
  misma corrida — si se reinicia el proceso, reinicia en un valor base
  aleatorio, no en 0, para simular un contador "usado").
- `1-0:32.7.0`: voltaje instantáneo, `NNN.N*V`.
- Línea final `!XXXX`: checksum CRC16/ARC (polinomio `0xA001`, init `0x0000`,
  con entrada y salida reflejadas — es la variante estándar de DSMR) en
  hexadecimal mayúsculas de 4 dígitos, calculado sobre todos los bytes desde
  el `/` inicial hasta el `!` inclusive.
- Terminador de línea: `\r\n` en cada línea, incluida la del checksum.
- Un telegrama nuevo cada 1s ± jitter de 100ms (para que se vea "vivo" y no
  metronómico).

**Generación de valores:** random walk acotado, no ruido puro independiente
por muestra — `kW(t) = clamp(kW(t-1) + N(0, 0.05), 0.3, 4.0)`,
`V(t) = clamp(V(t-1) + N(0, 0.3), 225, 235)`, `kWh` acumula
`kW(t) * (Δt_segundos / 3600)` en cada tick. Esto da una gráfica de línea
creíble en el dashboard en vez de ruido blanco.

## Componentes

### PC1 — `meter_simulator.exe`

- Python + PyInstaller `--onefile`. Sin instalación, sin dependencias
  externas visibles para el usuario.
- Genera y sirve telegramas como se describe arriba, TCP puerto 3000, acepta
  múltiples clientes (broadcast del mismo telegrama a todos).
- Anuncia servicio Zeroconf tipo `_metersim._tcp.local.`, nombre de instancia
  `meter`, con el puerto en el registro del servicio (no depende de
  resolución de hostname `.local`, ver sección Descubrimiento).
- UI mínima: ventana con estado ("Simulando · N clientes conectados") y botón
  Salir. Nada configurable — cero campos de texto, cero IPs visibles.

### Raspberry Pi — servicio `relay`

- Raspberry Pi OS Lite (64-bit), sin entorno gráfico.
- Preparación de la SD (una sola vez, la hace la persona técnica):
  1. Flashear Raspberry Pi OS Lite con Raspberry Pi Imager, preconfigurando
     WiFi (si aplica) y habilitando SSH desde el propio Imager (evita tener
     que conectar teclado/monitor).
  2. Primer boot: `sudo apt install python3-zeroconf` (o venv + pip), copiar
     `relay.py` y el unit file `relay.service` a `/etc/systemd/system/`,
     `sudo systemctl enable --now relay`.
  3. Apagar, la SD queda lista para replicar/clonar si se necesitan más
     unidades.
- En cada boot: `relay.py` arranca vía systemd (`Restart=always`,
  `WantedBy=multi-user.target`), busca el servicio `_metersim._tcp.local.`
  por Zeroconf con reintento indefinido cada 3s si no lo encuentra, conecta
  por TCP, y reenvía cada línea recibida byte-a-byte a todos los clientes
  conectados a su propio servidor TCP en el puerto 4000.
- No parsea, no valida, no transforma, no escribe a disco — pasa lo que
  recibe tal cual. Esto es lo que garantiza "sin guardarlo ni nada" en este
  punto de la cadena por construcción, no por disciplina.
- Anuncia servicio Zeroconf `_smartmeter._tcp.local.`, nombre `smartmeter`.

### PC2 — `dashboard.exe`

- Python + PyInstaller `--onefile`. UI con `tkinter` + `matplotlib`
  (ambos empaquetan bien con PyInstaller, sin dependencias del sistema).
- Busca `_smartmeter._tcp.local.` por Zeroconf, conecta por TCP al puerto
  anunciado, lee línea por línea.
- Acumula líneas hasta detectar un telegrama completo (desde `/` hasta la
  línea `!XXXX`), valida el checksum CRC16, y si es válido lo parsea
  (kW, V, kWh acumulado). Telegramas con checksum inválido o incompletos se
  descartan silenciosamente (log a consola, no a archivo) y se espera el
  siguiente — nunca se cae la conexión por un telegrama corrupto.
- Estado en memoria únicamente: buffer circular de los últimos 300 puntos
  (~5 min a 1 muestra/seg) para la gráfica. Se pierde al cerrar la app, por
  diseño.
- UI: número grande de kW actual, gráfica de línea de los últimos 5 min,
  voltaje y kWh acumulado del día como texto secundario.

## Descubrimiento (Zeroconf, no hostname `.local`)

Se usa la librería `zeroconf` (PyPI, pura Python + sockets, sin depender de
Bonjour en Windows ni de `avahi-daemon` corriendo correctamente en la Pi) con
su API de **servicios** (`ServiceInfo` + `ServiceBrowser`), no resolución de
hostname `mydevice.local`. Razón: la resolución de hostname `.local` depende
de soporte mDNS a nivel de SO (Windows 10+ lo tiene nativo pero con
comportamiento inconsistente entre versiones/firewalls corporativos; Avahi en
Raspberry Pi OS puede no estar activo si la imagen Lite lo omite). El API de
servicios de `zeroconf` resuelve IP:puerto directamente vía el propio
protocolo mDNS/DNS-SD implementado en la librería, sin tocar el resolver del
SO — es la misma dependencia (`zeroconf` en PyPI) en las tres piezas, cero
piezas móviles adicionales.

Tipos de servicio:
- PC1 anuncia `_metersim._tcp.local.`
- Pi anuncia `_smartmeter._tcp.local.`

**Requisito de red:** las tres máquinas deben estar en la misma red L2
(mismo WiFi/switch, sin aislamiento de clientes AP — "client isolation" en
routers/APs de oficina rompe mDNS porque bloquea multicast entre
dispositivos; si mañana falla el discovery, esto es lo primero a revisar).

## Manejo de errores

| Situación | Comportamiento |
|---|---|
| PC1 apagado o no encontrado | Pi reintenta cada 3s indefinidamente, no crashea el servicio |
| Pi apagada o no encontrada | PC2 muestra "Buscando Raspberry Pi..." y reintenta cada 3s |
| Conexión cae a medio stream | Reconexión automática con el mismo browser Zeroconf, sin reiniciar el proceso |
| Telegrama con checksum inválido | Se descarta, se loguea a consola, se sigue leyendo |
| Sin datos nuevos por >5s (con conexión viva) | Dashboard muestra aviso "Sin datos recientes" sin cerrar la conexión |
| Windows Firewall bloquea el puerto la primera vez | Ambos `.exe` deben pedir permiso de firewall al primer arranque (comportamiento default de Windows al abrir un socket de escucha) — anticipar el prompt en la demo, aceptar "Permitir acceso" |

## Plan de pruebas (riguroso, para mañana 2026-08-14)

**Antes de tener la Pi (hoy):**
1. Test unitario: generador de telegramas → CRC16 calculado coincide con el
   validado por el parser (round-trip generador→parser sobre 100 telegramas
   sintéticos, incluyendo valores límite: kW=0.3 y kW=4.0).
2. Test unitario: parser rechaza telegrama con un byte alterado (checksum
   debe fallar) y no lanza excepción no controlada.
3. Prueba local: `meter_simulator.exe` y `dashboard.exe` en la misma laptop
   (Zeroconf funciona en loopback/misma NIC), confirmar que el dashboard
   encuentra el simulador y grafica sin la Pi de por medio — aísla bugs de
   parsing/UI de bugs de red antes de sumar la Pi.

**Con la Pi física (mañana):**
1. Flashear y preparar la SD según los pasos de la sección Raspberry Pi.
2. Con PC1 corriendo el simulador, encender la Pi y confirmar por SSH
   (`journalctl -u relay -f`) que encuentra el servicio y empieza a
   reenviar — sin depender aún de PC2.
3. Levantar `dashboard.exe` en PC2 en la misma red y confirmar discovery +
   datos en vivo end-to-end.
4. Prueba de resiliencia: apagar PC1 con la cadena completa corriendo,
   confirmar que Pi y PC2 no crashean y se recuperan solos al reencenderlo.
5. Prueba de resiliencia: desconectar la Pi de la red 10s y reconectarla,
   confirmar que PC2 se recupera sin reiniciar el `.exe`.
6. Prueba de red real: correr las tres piezas en el WiFi real de la oficina
   (no un hotspot de laptop) — es donde "client isolation" u otras políticas
   de red pueden romper Zeroconf y es mejor descubrirlo mañana que en la
   demo final.

## Fuera de alcance (explícito)

- Persistencia de datos en cualquier punto de la cadena.
- Lectura de un medidor físico real (el simulador sustituye esa pieza en
  este prototipo).
- Autenticación, cifrado del tráfico TCP, multiusuario.
- Empaquetado de imagen SD reproducible automatizado (se prepara a mano una
  vez; automatizarlo es trabajo post-prototipo si el proyecto avanza).
