# Enlace serie USB real: Generador → Raspberry Pi

**Fecha:** 2026-08-14
**Estado:** Aprobado para implementación
**Depende de:** `docs/superpowers/specs/2026-08-13-smart-meter-prototype-design.md` (spec original del prototipo)

## Contexto y objetivo

Ya está la Raspberry Pi física y la segunda computadora. El tramo generador →
Pi ya no se simula por red (TCP + Zeroconf): se conecta con un cable/adaptador
USB real (producto "USB-306 E01") que en cada extremo se presenta como un
puerto serie estándar — un puerto COM en Windows, un dispositivo tipo
`/dev/ttyUSB0` en Linux. El objetivo de este documento es adaptar ese tramo
para usar el puerto serie real, dejando el tramo Pi → dashboard exactamente
como está (sigue siendo Ethernet/TCP/Zeroconf — un cable de red no cambia el
protocolo, solo el medio físico).

No es posible verificar de antemano el nombre exacto que el sistema operativo
le va a asignar a ese puerto serie en la máquina final (no es esta máquina de
desarrollo). El diseño no puede depender de conocerlo: la detección tiene que
ser automática en tiempo de ejecución, en ambos extremos.

Se agrega además un indicador visual simple en ambas ventanas (generador y
dashboard) para confirmar a simple vista que los datos están llegando
extremo a extremo — pensado para el momento de conectar el hardware real por
primera vez.

## Arquitectura

```
PC Generador (Windows)          Raspberry Pi (Linux)            PC Dashboard (Windows)
meter_simulator.exe             relay.py (systemd)               dashboard.exe
     |                               |        |                       |
     | puerto serie (USB-306 E01)    |        | TCP puerto 4000       |
     +-------------------------------+        | Zeroconf              |
                                      +----------------------------------+
                                        Ethernet (cable + adaptador USB-Ethernet)
```

- **Generador ↔ Pi:** puerto serie real. Sin red, sin Zeroconf, sin
  descubrimiento — la conexión física ES la topología.
- **Pi ↔ Dashboard:** sin cambios respecto al spec original. Sigue siendo
  `RelayServer` (TCP puerto 4000) + `advertise_service` de
  `_smartmeter._tcp.local.` + `ServiceWaiter` del lado del dashboard.

## Componentes afectados vs. sin cambios

| Componente | Cambia? | Detalle |
|---|---|---|
| `meter_simulator.py` | Sí | Servidor TCP → escritor de puerto serie |
| `relay.py` (lado generador) | Sí | Cliente TCP/Zeroconf → lector de puerto serie |
| `relay.py` (lado dashboard) | No | `RelayServer`, advertise, broadcast — igual que hoy |
| `dashboard.py` | No | Sigue conectando por TCP/Zeroconf a `relay.py`, sin cambios |
| `dsmr.py` | No | `generate_telegram`, `parse_telegram`, `TelegramReader`, CRC16 — agnósticos al transporte, se reusan tal cual |
| `discovery.py` | No | Sigue usándose para el tramo Pi↔dashboard |
| `requirements.txt` | Sí | Se agrega `pyserial` |

## Detección automática de puerto serie

Se usa `pyserial` (`serial.tools.list_ports.comports()`) para enumerar
puertos serie disponibles en el sistema, en ambos extremos (Windows y Linux).

**Regla de selección:**
- Si aparece exactamente un puerto serie disponible → se usa ese.
- Si no aparece ninguno → se reintenta la búsqueda cada pocos segundos,
  mostrando un estado de "buscando puerto" (mismo patrón que hoy usa
  `wait_for_meter` para Zeroconf), indefinidamente.
- Si aparece más de uno → se usa el primero que devuelve `comports()` (no
  hay UI de selección — YAGNI, no hay caso de uso real hoy con varios
  puertos serie simultáneos en estas máquinas) y se deja constancia clara de
  cuál se eligió (en la ventana del generador y en el log del relay), para
  poder diagnosticar a simple vista si detectó el equivocado.

No hay configuración manual de puerto (ni variable de entorno, ni argumento
de línea de comandos) — se descartó explícitamente para mantener "doble clic
y listo" sin superficies de configuración adicionales.

## `meter_simulator.py` — comportamiento nuevo

- Se elimina `MeterServer` (servidor TCP) y el `advertise_service` de
  `_metersim._tcp.local.`.
- Nuevo escritor serie: detecta el puerto (regla de arriba), lo abre, y cada
  ~1s ± jitter de 100ms escribe un telegrama nuevo (mismo generador de
  valores del spec original, sin cambios).
- Ventana: el estado "Simulando... N cliente(s) conectados" se reemplaza por
  "Buscando puerto serie..." mientras no hay puerto, y "Conectado en `<nombre
  de puerto>`" una vez abierto.
- Se agrega una lista en pantalla (auto-scroll, últimas ~20 líneas visibles)
  que registra cada telegrama enviado: `Enviado #1: 2.345 kW`, `Enviado #2:
  2.301 kW`, etc. Puramente visual, no afecta la generación de datos.
- Si el puerto se desconecta en medio de una corrida (error de escritura):
  vuelve al estado "buscando puerto", reintenta solo, sin crashear.

## `relay.py` — comportamiento nuevo (lado generador)

- Se elimina `wait_for_meter` y `connect_upstream` (Zeroconf + TCP hacia el
  generador).
- Nueva función equivalente que detecta y abre el puerto serie (misma regla
  de selección), y lee del puerto en un loop, pasando cada telegrama
  completo (usando `TelegramReader` tal cual, sin cambios — ya sabe separar
  telegramas de un flujo de bytes) a `relay.broadcast()`.
- Logging: al igual que hoy imprime transiciones de estado (puerto
  encontrado y cuál, puerto perdido, reintentando), para que
  `journalctl -u relay -f` siga siendo útil para diagnosticar en la Pi.
- Si el puerto serie se desconecta: vuelve a la búsqueda, reintenta solo. El
  tramo hacia el dashboard (`RelayServer`) no se ve afectado por esto — sigue
  sirviendo a los clientes ya conectados hasta que llegue un telegrama nuevo
  para reenviar.

## `dashboard.py` — comportamiento nuevo (solo el indicador visual)

- Sin cambios en la lógica de conexión/parseo (sigue igual que el spec
  original).
- Se agrega la misma lista visual del lado del generador, pero de
  recepción: `Recibido #1: 2.345 kW`, `Recibido #2: 2.301 kW`, etc., una
  línea por telegrama válido parseado con éxito (los telegramas corruptos
  descartados no se cuentan en esta lista — ya se loguean a consola aparte,
  sin cambios respecto al spec original).

## Manejo de errores (delta sobre la tabla del spec original)

| Situación | Comportamiento |
|---|---|
| No se encuentra ningún puerto serie (generador o Pi) | Reintenta cada pocos segundos indefinidamente, no crashea |
| El puerto serie se desconecta en medio de una corrida | Se detecta por error de lectura/escritura, vuelve a buscar/abrir solo |
| Hay más de un puerto serie disponible en la máquina | Se usa el primero devuelto por `pyserial`, se muestra claramente cuál en pantalla/log |

El resto de la tabla de manejo de errores del spec original (PC apagada,
conexión Pi↔dashboard cae, checksum inválido, sin datos por >5s, firewall)
sigue vigente sin cambios — son todos del tramo Pi↔dashboard, que no se toca.

## Testing sin hardware real disponible

No hay acceso al cable/Pi física desde el entorno de desarrollo. `pyserial`
incluye un modo de loopback en memoria (`serial.serial_for_url("loop://")`)
que permite escribir tests que ejercitan la lectura/escritura real de la
librería sin hardware — se usa este mecanismo en vez de mockear `pyserial`,
siguiendo el mismo criterio que el resto del proyecto (probar comportamiento
real, no mocks). Los tests de `test_meter_simulator.py` y `test_relay.py`
que hoy usan sockets TCP reales se reescriben sobre este loopback serie.

## Fuera de alcance (explícito)

- Selección manual de puerto (variable de entorno, argumento CLI, UI de
  selección) — se descartó a favor de autodetección pura.
- Desambiguación inteligente cuando hay más de un puerto serie disponible
  (por VID/PID, descripción del chip, etc.) — no hay caso de uso real hoy.
- Cambios al tramo Pi↔dashboard — permanece exactamente como en el spec
  original.
- Validación de las capas físicas de red del tramo Ethernet (cableado,
  adaptadores USB-Ethernet, asignación de IP) — es configuración de
  infraestructura, no código.
