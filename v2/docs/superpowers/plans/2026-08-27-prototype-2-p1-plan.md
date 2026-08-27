# Prototipo 2 (puerto P1 real, dos Raspberry Pi) — Plan de implementación

> **Para agentes ejecutores:** SUB-SKILL REQUERIDA: usar `superpowers:subagent-driven-development` (recomendado) o `superpowers:executing-plans` para implementar este plan tarea por tarea. Los pasos usan sintaxis de casilla (`- [ ]`) para seguimiento.

**Objetivo:** Leer un puerto P1 real de un medidor holandés (o un simulador equivalente) desde una Raspberry Pi, y mostrar el consumo en vivo desde una segunda Raspberry Pi con pantalla que además sirve el mismo dashboard por WiFi.

**Arquitectura:** La Pi lectora sondea sus puertos serie, se queda con el que emite telegramas y reenvía los bytes crudos por TCP, sin conocer DSMR. La Pi con pantalla recibe ese flujo, enmarca, valida CRC, parsea con un parser OBIS genérico, guarda en SQLite y sirve una única página HTTP que abren tanto el navegador en kiosco de su propia pantalla como los teléfonos conectados a su AP WiFi.

**Stack:** Python 3.11+ (stdlib: `sqlite3`, `http.server`, `zoneinfo`, `re`, `socket`, `threading`, `tkinter`), `pyserial` como única dependencia externa en la Pi lectora y en el simulador. NetworkManager para red y AP. systemd para servicios.

**Spec:** `v2/docs/superpowers/specs/2026-08-27-prototype-2-p1-design.md`

## Restricciones globales

- **Todo el código nuevo vive en `v2/`.** `v1/` queda congelado. Nunca importar de `v1/` ni modificarlo: lo que se reutiliza se copia.
- **Estructura plana**, como `v1/`: módulos y tests como `v2/*.py`, sin paquetes ni subdirectorios salvo `v2/static/` y `v2/systemd/`.
- **Ponytail full.** Biblioteca estándar antes que dependencias. Sin abstracciones especulativas: nada de interfaces con una sola implementación, ni configuración para valores que nunca cambian. El diff más corto que funcione, una vez entendido el problema.
- **Única dependencia externa permitida:** `pyserial>=3.5`. Nada de Flask, FastAPI, matplotlib, zeroconf, ifaddr, ni librerías de gráficas. Si una tarea parece necesitar una dependencia nueva, es señal de que el enfoque está mal: parar y consultar.
- **DSMR 5.0.2:** 115200 baudios, 8N1, un telegrama por segundo. CRC16 con polinomio reflejado `0xA001`, sin XOR de entrada ni de salida, calculado sobre los bytes desde `/` hasta `!` **inclusive**, representado como cuatro hexadecimales.
- **Red:** enlace Ethernet directo `192.168.7.1/24` (Pi lectora) y `192.168.7.2/24` (Pi pantalla), `ipv4.method manual` en ambos lados. Relay en TCP `4000`. HTTP en `8080`. Nunca usar `10.42.0.0/24`: es el rango del hotspot de NetworkManager y colisionaría.
- **Zona horaria del medidor:** `Europe/Amsterdam`.
- **Idioma:** comentarios, mensajes de log y de commit en español neutro, sin voseo. Los textos visibles del dashboard, en inglés (igual que en v1).
- **Los comentarios explican por qué, no qué.** Solo donde la razón no sea evidente leyendo el código.
- **Cada commit termina con:**
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```
- **Ejecutar los tests desde `v2/`:** `cd v2 && python -m pytest -q`
- **Los conteos de tests que aparecen en los pasos son orientativos.** Lo que importa es que no haya fallos ni errores. Un conteo distinto al indicado no es un problema; un test rojo sí.

## Estructura de archivos

| Archivo | Responsabilidad |
| --- | --- |
| `v2/dsmr.py` | CRC16, marca de tiempo, parser OBIS genérico, enmarcado, generación de telegramas |
| `v2/serial_link.py` | Detección y apertura de puertos serie (copiado de v1) |
| `v2/relay.py` | Pi #1: serie a TCP |
| `v2/store.py` | Pi #2: SQLite |
| `v2/server.py` | Pi #2: cliente TCP, estado en vivo y servidor HTTP |
| `v2/static/index.html` | Pi #2: dashboard, una sola página |
| `v2/meter_simulator.py` | Plan B: generador con interfaz gráfica |
| `v2/systemd/relay.service` | Servicio de la Pi #1 |
| `v2/systemd/display.service` | Servicio de la Pi #2 |
| `v2/systemd/smartmeter-kiosk.desktop` | Autostart del navegador en kiosco |
| `v2/install_reader.sh` | Instalación de la Pi #1 |
| `v2/install_display.sh` | Instalación de la Pi #2 |
| `v2/requirements.txt` | `pyserial`, `pytest` |
| `v2/README.md` | Documentación del prototipo |

Tests: `v2/test_dsmr.py`, `v2/test_store.py`, `v2/test_server.py`, `v2/test_relay.py`, `v2/test_integration_e2e.py`.

---

### Tarea 1: `dsmr.py` — CRC16 y marca de tiempo

Los dos cimientos del parser. El CRC decide qué telegrama se acepta; la marca de tiempo decide bajo qué clave se guarda.

**Archivos:**
- Crear: `v2/dsmr.py`
- Crear: `v2/test_dsmr.py`
- Crear: `v2/requirements.txt`

**Interfaces:**
- Consume: nada.
- Produce: `crc16_arc(data: bytes) -> int`, `parse_timestamp(value: str) -> int | None`, `format_timestamp(dt: datetime) -> str`, `METER_TZ: ZoneInfo`, `InvalidTelegram(Exception)`.

- [ ] **Paso 1: Escribir los tests que fallan**

Crear `v2/test_dsmr.py`:

```python
from datetime import datetime

import pytest

from dsmr import METER_TZ, crc16_arc, format_timestamp, parse_timestamp


def test_crc16_arc_vector_conocido():
    # Vector estandar de CRC-16/ARC: la cadena "123456789" da 0xBB3D.
    # Sirve de ancla independiente del formato DSMR: si esto pasa, el
    # polinomio, la reflexion y la ausencia de XOR son correctos.
    assert crc16_arc(b"123456789") == 0xBB3D


def test_crc16_arc_vacio_es_cero():
    assert crc16_arc(b"") == 0x0000


def utc(anio, mes, dia, hora, minuto):
    """Valor esperado construido en UTC explicito.

    Escribir el segundo unix a mano invita a equivocarse justo en el offset
    que la prueba quiere verificar, y un numero magico mal calculado deja
    pasar el bug que buscaba atrapar.
    """
    from datetime import timezone
    return int(datetime(anio, mes, dia, hora, minuto, tzinfo=timezone.utc).timestamp())


def test_parse_timestamp_horario_de_invierno():
    # 12:30 CET (UTC+1) son las 11:30 UTC
    assert parse_timestamp("260115123000W") == utc(2026, 1, 15, 11, 30)


def test_parse_timestamp_horario_de_verano():
    # 12:30 CEST (UTC+2) son las 10:30 UTC
    assert parse_timestamp("260715123000S") == utc(2026, 7, 15, 10, 30)


def test_parse_timestamp_desambigua_la_hora_repetida():
    """La bandera S/W no es decorativa.

    El 25 de octubre de 2026 el horario de verano termina en Holanda y la
    hora entre las 02:00 y las 03:00 ocurre dos veces. Sin usar la bandera,
    ambas lecturas darian el mismo segundo unix, colisionarian como clave
    primaria en SQLite y se perderia una hora de datos una vez al ano.
    """
    verano = parse_timestamp("261025023000S")
    invierno = parse_timestamp("261025023000W")
    assert invierno - verano == 3600


def test_parse_timestamp_rechaza_formato_invalido():
    assert parse_timestamp("no-es-una-fecha") is None
    assert parse_timestamp("261025023000X") is None
    assert parse_timestamp("26102502300W") is None


def test_format_timestamp_ida_y_vuelta():
    momento = datetime(2026, 7, 15, 12, 30, 0, tzinfo=METER_TZ)
    assert format_timestamp(momento) == "260715123000S"
    assert parse_timestamp(format_timestamp(momento)) == int(momento.timestamp())
```

Crear `v2/requirements.txt`:

```text
pyserial>=3.5
pytest>=8.0
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'dsmr'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Crear `v2/dsmr.py`:

```python
"""Generacion, validacion y parseo de telegramas DSMR 5.0.2.

Referencia: DSMR 5.0.2 P1 Companion Standard, Netbeheer Nederland, 2016-02-26.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

METER_TZ = ZoneInfo("Europe/Amsterdam")


class InvalidTelegram(Exception):
    pass


def crc16_arc(data: bytes) -> int:
    """CRC16 segun 6.2 del estandar.

    Polinomio x^16+x^15+x^2+1 en su forma reflejada (0xA001), sin XOR de
    entrada ni de salida, bit menos significativo primero.
    """
    crc = 0x0000
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


_TST = re.compile(r"^(\d{12})([WS])$")


def parse_timestamp(value: str):
    """'YYMMDDhhmmssX' en hora holandesa -> segundos unix, o None.

    La bandera final distingue verano (S) de invierno (W). Importa una vez
    al ano: en el cambio de otono la misma hora local ocurre dos veces, y
    esa letra es lo unico que separa una ocurrencia de la otra. Se traduce
    a `fold`, que es como datetime representa la segunda ocurrencia.
    """
    match = _TST.match(value)
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group(1), "%y%m%d%H%M%S")
    except ValueError:
        return None
    fold = 0 if match.group(2) == "S" else 1
    return int(naive.replace(tzinfo=METER_TZ, fold=fold).timestamp())


def format_timestamp(moment: datetime) -> str:
    """Inversa de parse_timestamp, para el simulador."""
    flag = "S" if moment.dst() else "W"
    return moment.strftime("%y%m%d%H%M%S") + flag
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: 7 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/dsmr.py v2/test_dsmr.py v2/requirements.txt
git commit -m "feat(v2): CRC16 y marca de tiempo DSMR con desambiguacion de horario"
```

---

### Tarea 2: `dsmr.py` — parser OBIS genérico

El punto donde falló el prototipo 1. `v1/dsmr.py:100` tiene `/ISK5` hardcodeado en el enmarcado y una regex por cada uno de los tres campos que entiende; un medidor real se identifica de otra forma y emite unas 35 líneas. Este parser no conoce ningún código de antemano.

**Archivos:**
- Modificar: `v2/dsmr.py`
- Modificar: `v2/test_dsmr.py`

**Interfaces:**
- Consume: `crc16_arc`, `InvalidTelegram` de la Tarea 1.
- Produce: `parse_telegram(raw: bytes) -> dict` con forma `{"ident": str, "objects": dict[str, list[str]]}`; `as_number(value: str) -> float | None`; `number(objects: dict, code: str, index: int = 0) -> float | None`.

- [ ] **Paso 1: Escribir los tests que fallan**

Añadir a `v2/test_dsmr.py`:

```python
from dsmr import InvalidTelegram, as_number, number, parse_telegram


def construir(cuerpo: str) -> bytes:
    """Arma un telegrama valido calculando su CRC real.

    El ejemplo de 6.13 del PDF viene partido por el salto de pagina, asi que
    su CRC publicado (EF2F) no puede darse por valido sobre el texto
    reconstruido. Se usa la estructura del estandar con el CRC recalculado;
    la funcion de CRC ya quedo anclada aparte con un vector conocido.
    """
    crc = crc16_arc((cuerpo + "!").encode("ascii"))
    return (cuerpo + f"!{crc:04X}\r\n").encode("ascii")


# Estructura del ejemplo de 6.13 del estandar, con los tres bloques que mas
# importa no perder: las tres fases, el registro de cortes y el gas M-Bus.
CUERPO_EJEMPLO = (
    "/ISk5\\2MT382-1000\r\n"
    "\r\n"
    "1-3:0.2.8(50)\r\n"
    "0-0:1.0.0(101209113020W)\r\n"
    "0-0:96.1.1(4B384547303034303436333935353037)\r\n"
    "1-0:1.8.1(123456.789*kWh)\r\n"
    "1-0:1.8.2(123456.789*kWh)\r\n"
    "1-0:2.8.1(123456.789*kWh)\r\n"
    "1-0:2.8.2(123456.789*kWh)\r\n"
    "0-0:96.14.0(0002)\r\n"
    "1-0:1.7.0(01.193*kW)\r\n"
    "1-0:2.7.0(00.000*kW)\r\n"
    "0-0:96.7.21(00004)\r\n"
    "0-0:96.7.9(00002)\r\n"
    "1-0:99.97.0(2)(0-0:96.7.19)(101208152415W)(0000000240*s)"
    "(101208151004W)(0000000301*s)\r\n"
    "1-0:32.32.0(00002)\r\n"
    "0-0:96.13.0()\r\n"
    "1-0:32.7.0(220.1*V)\r\n"
    "1-0:52.7.0(220.2*V)\r\n"
    "1-0:72.7.0(220.3*V)\r\n"
    "1-0:31.7.0(001*A)\r\n"
    "1-0:51.7.0(002*A)\r\n"
    "1-0:71.7.0(003*A)\r\n"
    "0-1:24.1.0(003)\r\n"
    "0-1:96.1.0(3232323241424344313233343536373839)\r\n"
    "0-1:24.2.1(101209112500W)(12785.123*m3)\r\n"
)


def test_parse_extrae_identificacion():
    resultado = parse_telegram(construir(CUERPO_EJEMPLO))
    assert resultado["ident"] == "ISk5\\2MT382-1000"


def test_parse_extrae_las_tres_fases():
    objetos = parse_telegram(construir(CUERPO_EJEMPLO))["objects"]
    assert number(objetos, "1-0:32.7.0") == 220.1
    assert number(objetos, "1-0:52.7.0") == 220.2
    assert number(objetos, "1-0:72.7.0") == 220.3
    assert number(objetos, "1-0:31.7.0") == 1.0


def test_parse_conserva_el_registro_de_cortes_completo():
    objetos = parse_telegram(construir(CUERPO_EJEMPLO))["objects"]
    assert objetos["1-0:99.97.0"] == [
        "2", "0-0:96.7.19",
        "101208152415W", "0000000240*s",
        "101208151004W", "0000000301*s",
    ]


def test_parse_extrae_el_gas_con_su_marca_de_captura():
    objetos = parse_telegram(construir(CUERPO_EJEMPLO))["objects"]
    assert objetos["0-1:24.2.1"] == ["101209112500W", "12785.123*m3"]
    assert number(objetos, "0-1:24.2.1", index=1) == 12785.123


def test_parse_acepta_valor_vacio():
    objetos = parse_telegram(construir(CUERPO_EJEMPLO))["objects"]
    assert objetos["0-0:96.13.0"] == [""]


@pytest.mark.parametrize(
    "identificacion",
    ["/KFM5KAIFA-METER", "/Ene5\\XS210 ESMR 5.0", "/XMX5LGBBFG1009325446"],
)
def test_parse_no_asume_ningun_fabricante(identificacion):
    """El prototipo 1 tenia /ISK5 hardcodeado y no leia ningun medidor real.

    5.13 del estandar dice explicitamente que ni el conjunto ni el orden de
    los codigos OBIS son fijos, y la identificacion varia por fabricante.
    """
    cuerpo = f"{identificacion}\r\n\r\n1-0:1.7.0(00.500*kW)\r\n"
    resultado = parse_telegram(construir(cuerpo))
    assert resultado["ident"] == identificacion[1:]
    assert number(resultado["objects"], "1-0:1.7.0") == 0.5


def test_parse_rechaza_crc_incorrecto():
    valido = construir(CUERPO_EJEMPLO)
    corrupto = valido[:-6] + b"0000\r\n"
    with pytest.raises(InvalidTelegram, match="checksum"):
        parse_telegram(corrupto)


def test_parse_rechaza_telegrama_sin_marcador():
    with pytest.raises(InvalidTelegram):
        parse_telegram(b"/ISK5\r\n\r\n1-0:1.7.0(00.500*kW)\r\n")


def test_parse_rechaza_lo_que_no_empieza_con_barra():
    cuerpo = "basura previa\r\n\r\n1-0:1.7.0(00.500*kW)\r\n"
    with pytest.raises(InvalidTelegram):
        parse_telegram(construir(cuerpo))


def test_as_number_reconoce_valor_con_unidad_y_sin_ella():
    assert as_number("123456.789*kWh") == 123456.789
    assert as_number("001*A") == 1.0
    assert as_number("00002") == 2.0
    assert as_number("101209112500W") is None
    assert as_number("") is None


def test_number_devuelve_none_para_campo_ausente():
    """Un medidor monofasico no informa L2 ni L3, y eso es normal."""
    objetos = parse_telegram(construir(CUERPO_EJEMPLO))["objects"]
    assert number(objetos, "1-0:52.32.0") is None
    assert number(objetos, "1-0:1.7.0", index=5) is None
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: FAIL con `ImportError: cannot import name 'parse_telegram' from 'dsmr'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Añadir a `v2/dsmr.py`:

```python
# Una linea OBIS es un codigo seguido de cero o mas valores entre parentesis.
# No se enumeran los codigos posibles a proposito: 6.13 del estandar advierte
# que ni el conjunto ni el orden son fijos, y varian entre fabricantes.
_OBIS_LINE = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+)((?:\([^)]*\))*)\s*$")
_VALUE = re.compile(r"\(([^)]*)\)")
_UNIT_VALUE = re.compile(r"^(-?\d+(?:\.\d+)?)\*[A-Za-z0-9]+$")


def parse_telegram(raw: bytes) -> dict:
    """Valida el CRC y devuelve la identificacion y todos los objetos OBIS.

    Los valores se devuelven como texto tal cual llegaron. Convertirlos aqui
    obligaria a saber que unidad tiene cada codigo, que es justo lo que no se
    puede saber de antemano.
    """
    text = raw.decode("ascii", errors="replace")
    body, marker, rest = text.rpartition("!")
    if not marker:
        raise InvalidTelegram("falta el marcador de checksum")
    checksum_hex = rest.strip()[:4]
    try:
        expected = int(checksum_hex, 16)
    except ValueError:
        raise InvalidTelegram(f"checksum no hexadecimal: {checksum_hex!r}")
    # El CRC cubre desde '/' hasta '!' inclusive (6.2 del estandar).
    actual = crc16_arc((body + "!").encode("ascii"))
    if actual != expected:
        raise InvalidTelegram(f"checksum no coincide: {actual:04X} != {expected:04X}")

    lines = body.split("\r\n")
    if not lines[0].startswith("/"):
        raise InvalidTelegram("el telegrama no empieza con '/'")
    objects = {}
    for line in lines[1:]:
        match = _OBIS_LINE.match(line)
        if match:
            objects[match.group(1)] = _VALUE.findall(match.group(2))
    return {"ident": lines[0][1:], "objects": objects}


def as_number(value: str):
    """'123.456*kWh' -> 123.456; '00002' -> 2.0; una marca de tiempo -> None."""
    match = _UNIT_VALUE.match(value)
    if match:
        return float(match.group(1))
    try:
        return float(value)
    except ValueError:
        return None


def number(objects: dict, code: str, index: int = 0):
    """Valor numerico de un objeto OBIS, o None si no esta.

    Devolver None en vez de fallar es deliberado: un medidor monofasico no
    informa L2 ni L3, y un medidor sin gas no informa 24.2.1. La ausencia de
    un campo es normal, no un error.
    """
    values = objects.get(code)
    if not values or index >= len(values):
        return None
    return as_number(values[index])
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: 21 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/dsmr.py v2/test_dsmr.py
git commit -m "feat(v2): parser OBIS generico sin fabricante ni campos hardcodeados"
```

---

### Tarea 3: `dsmr.py` — enmarcado con límite de buffer

El flujo TCP llega en trozos arbitrarios: un telegrama puede venir partido en cinco lecturas o dos telegramas pegados en una. El enmarcador los separa. `v1/dsmr.py:103` hace esto pero sin límite de buffer, lo que con un medidor real y un cable con ruido hace crecer la memoria sin fin.

**Archivos:**
- Modificar: `v2/dsmr.py`
- Modificar: `v2/test_dsmr.py`

**Interfaces:**
- Consume: nada de tareas previas (el enmarcado es puramente sintáctico).
- Produce: `TelegramReader` con `feed(chunk: bytes) -> list[bytes]` y el atributo `dropped_bytes: int`; constante `MAX_BUFFER = 16384`.

- [ ] **Paso 1: Escribir los tests que fallan**

Añadir a `v2/test_dsmr.py`:

```python
from dsmr import MAX_BUFFER, TelegramReader


def test_reader_entrega_un_telegrama_completo():
    telegrama = construir(CUERPO_EJEMPLO)
    lector = TelegramReader()
    assert lector.feed(telegrama) == [telegrama]


def test_reader_reensambla_un_telegrama_partido_en_trozos():
    telegrama = construir(CUERPO_EJEMPLO)
    lector = TelegramReader()
    salida = []
    for inicio in range(0, len(telegrama), 7):
        salida.extend(lector.feed(telegrama[inicio:inicio + 7]))
    assert salida == [telegrama]


def test_reader_separa_dos_telegramas_pegados():
    telegrama = construir(CUERPO_EJEMPLO)
    lector = TelegramReader()
    assert lector.feed(telegrama + telegrama) == [telegrama, telegrama]


def test_reader_descarta_la_basura_previa():
    telegrama = construir(CUERPO_EJEMPLO)
    lector = TelegramReader()
    assert lector.feed(b"\x00ruido de linea\xff" + telegrama) == [telegrama]


def test_reader_no_asume_ningun_fabricante():
    telegrama = construir("/KFM5KAIFA-METER\r\n\r\n1-0:1.7.0(00.500*kW)\r\n")
    lector = TelegramReader()
    assert lector.feed(telegrama) == [telegrama]


def test_reader_acota_el_buffer_ante_basura_sin_fin():
    """Sin tope, un cable con ruido hace crecer la memoria indefinidamente."""
    lector = TelegramReader()
    assert lector.feed(b"x" * (MAX_BUFFER * 2)) == []
    assert lector.dropped_bytes == MAX_BUFFER * 2


def test_reader_acota_un_telegrama_que_nunca_termina():
    """Caso limite: la basura empieza con '/', asi que recortar hasta la
    ultima barra no libera nada y el buffer creceria igual."""
    lector = TelegramReader()
    assert lector.feed(b"/" + b"x" * (MAX_BUFFER * 2)) == []
    assert lector.dropped_bytes > 0


def test_reader_sigue_funcionando_despues_de_desbordar():
    telegrama = construir(CUERPO_EJEMPLO)
    lector = TelegramReader()
    lector.feed(b"x" * (MAX_BUFFER * 2))
    assert lector.feed(telegrama) == [telegrama]
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: FAIL con `ImportError: cannot import name 'MAX_BUFFER' from 'dsmr'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Añadir a `v2/dsmr.py`:

```python
# Un telegrama con el mensaje de texto de 1024 caracteres y un registro largo
# de cortes no pasa de unos pocos KB. 16 KB deja margen de sobra y sigue
# acotando la memoria si el cable mete ruido.
MAX_BUFFER = 16384

# Desde una '/' hasta el '!' con sus cuatro hexadecimales. El identificador
# del fabricante queda como comodin: es lo que rompia en v1.
_TELEGRAM = re.compile(rb"/[^\r\n]*\r\n.*?\r\n![0-9A-Fa-f]{4}\r\n", re.DOTALL)


class TelegramReader:
    """Separa telegramas de un flujo de bytes que llega en trozos arbitrarios."""

    def __init__(self, max_buffer=MAX_BUFFER):
        self._buffer = b""
        self._max = max_buffer
        self.dropped_bytes = 0

    def feed(self, chunk: bytes) -> list:
        self._buffer += chunk
        telegrams = []
        while True:
            match = _TELEGRAM.search(self._buffer)
            if not match:
                break
            telegrams.append(match.group(0))
            self._buffer = self._buffer[match.end():]
        if len(self._buffer) > self._max:
            start = self._buffer.rfind(b"/")
            # start == 0 significa que lo acumulado ya empieza con '/' y no es
            # un telegrama valido: recortar hasta ahi no liberaria nada.
            if start <= 0:
                self.dropped_bytes += len(self._buffer)
                self._buffer = b""
            else:
                self.dropped_bytes += start
                self._buffer = self._buffer[start:]
        return telegrams
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: 29 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/dsmr.py v2/test_dsmr.py
git commit -m "feat(v2): enmarcado de telegramas con buffer acotado"
```

---

### Tarea 4: `dsmr.py` — generación de telegramas DSMR 5.0.2 completos

El simulador de v1 emite cinco líneas propias. Para que Plan B ejercite el mismo camino que Plan A, el simulador tiene que emitir un telegrama indistinguible del de un medidor real.

**Archivos:**
- Modificar: `v2/dsmr.py`
- Modificar: `v2/test_dsmr.py`

**Interfaces:**
- Consume: `crc16_arc`, `format_timestamp`, `METER_TZ`, `parse_telegram`, `number`.
- Produce: `MeterState` con `tick(dt_seconds: float = 1.0)` y los atributos `energy_in_t1`, `energy_in_t2`, `energy_out_t1`, `energy_out_t2`, `power_in`, `power_out`, `tariff`, `voltage` (lista de 3), `current` (lista de 3), `phase_power` (lista de 3), `gas`, `gas_captured_at`; `generate_telegram(state: MeterState, now: datetime | None = None) -> bytes`.

- [ ] **Paso 1: Escribir los tests que fallan**

Añadir a `v2/test_dsmr.py`:

```python
from dsmr import MeterState, generate_telegram


def test_telegrama_generado_pasa_su_propio_parser():
    """La prueba que mas vale: si el generador y el parser coinciden, el
    formato que sale es el mismo que sabemos leer de un medidor real."""
    estado = MeterState()
    estado.tick(1.0)
    telegrama = generate_telegram(estado)
    resultado = parse_telegram(telegrama)
    assert resultado["ident"]
    assert number(resultado["objects"], "1-0:1.7.0") is not None


def test_telegrama_generado_pasa_el_enmarcador():
    estado = MeterState()
    telegrama = generate_telegram(estado)
    assert TelegramReader().feed(telegrama) == [telegrama]


def test_telegrama_declara_version_dsmr_5():
    objetos = parse_telegram(generate_telegram(MeterState()))["objects"]
    assert objetos["1-3:0.2.8"] == ["50"]


def test_telegrama_trae_las_tres_fases():
    objetos = parse_telegram(generate_telegram(MeterState()))["objects"]
    for codigo in ("1-0:32.7.0", "1-0:52.7.0", "1-0:72.7.0",
                   "1-0:31.7.0", "1-0:51.7.0", "1-0:71.7.0",
                   "1-0:21.7.0", "1-0:41.7.0", "1-0:61.7.0"):
        assert number(objetos, codigo) is not None, codigo


def test_telegrama_trae_los_cuatro_registros_de_energia():
    objetos = parse_telegram(generate_telegram(MeterState()))["objects"]
    for codigo in ("1-0:1.8.1", "1-0:1.8.2", "1-0:2.8.1", "1-0:2.8.2"):
        assert number(objetos, codigo) is not None, codigo


def test_telegrama_trae_gas_con_marca_de_captura():
    objetos = parse_telegram(generate_telegram(MeterState()))["objects"]
    assert len(objetos["0-1:24.2.1"]) == 2
    assert parse_timestamp(objetos["0-1:24.2.1"][0]) is not None
    assert number(objetos, "0-1:24.2.1", index=1) is not None


def test_la_energia_nunca_retrocede():
    """Un contador de energia que baja es fisicamente imposible y romperia
    cualquier calculo de consumo por diferencia."""
    estado = MeterState()
    anterior = estado.energy_in_t2
    for _ in range(200):
        estado.tick(1.0)
        assert estado.energy_in_t2 >= anterior
        anterior = estado.energy_in_t2


def test_la_potencia_se_mantiene_en_un_rango_domestico():
    estado = MeterState()
    for _ in range(500):
        estado.tick(1.0)
        assert 0.0 <= estado.power_in <= 4.0


def test_el_gas_solo_avanza_cada_cinco_minutos():
    """Un medidor de gas por M-Bus reporta un valor nuevo cada 5 minutos, no
    cada segundo. Simularlo continuo daria una demo que no se parece a la
    realidad que vamos a leer."""
    estado = MeterState()
    inicial = estado.gas
    for _ in range(299):
        estado.tick(1.0)
    assert estado.gas == inicial
    estado.tick(1.0)
    assert estado.gas > inicial
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: FAIL con `ImportError: cannot import name 'MeterState' from 'dsmr'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Añadir a `v2/dsmr.py` (arriba del archivo, junto a los demás imports: `import random`):

```python
IDENT = "ISk5\\2MT382-1000"
EQUIPMENT_ID = "4B384547303034303436333935353037"
GAS_EQUIPMENT_ID = "3232323241424344313233343536373839"
GAS_INTERVAL_SECONDS = 300


class MeterState:
    """Estado de un medidor domestico trifasico, con evolucion creible.

    Los valores son sinteticos, pero su forma importa: la energia solo
    crece, la potencia se mueve como una caminata aleatoria acotada y el gas
    avanza cada cinco minutos, que es como reporta un M-Bus real.
    """

    def __init__(self):
        self.energy_in_t1 = 3477.050
        self.energy_in_t2 = 4218.319
        self.energy_out_t1 = 0.0
        self.energy_out_t2 = 0.0
        self.power_in = 1.5
        self.power_out = 0.0
        self.tariff = 2
        self.voltage = [230.0, 229.4, 230.6]
        self.current = [2, 1, 3]
        self.phase_power = [0.5, 0.5, 0.5]
        self.gas = 12785.123
        self.gas_captured_at = None
        self._seconds_since_gas = 0.0

    def tick(self, dt_seconds=1.0, now=None):
        now = now or datetime.now(METER_TZ)
        self.power_in = min(3.5, max(0.2, self.power_in + random.gauss(0, 0.05)))
        share = self.power_in / 3.0
        self.phase_power = [
            round(max(0.0, share + random.gauss(0, 0.03)), 3) for _ in range(3)
        ]
        self.voltage = [
            round(min(235.0, max(225.0, v + random.gauss(0, 0.3))), 1)
            for v in self.voltage
        ]
        # 230 V por fase; la corriente se deduce de la potencia de esa fase.
        self.current = [
            int(round(p * 1000.0 / v)) for p, v in zip(self.phase_power, self.voltage)
        ]
        # Solo acumula la tarifa activa, como un medidor real con doble tarifa.
        delta = self.power_in * (dt_seconds / 3600.0)
        if self.tariff == 1:
            self.energy_in_t1 += delta
        else:
            self.energy_in_t2 += delta

        self._seconds_since_gas += dt_seconds
        if self._seconds_since_gas >= GAS_INTERVAL_SECONDS:
            self._seconds_since_gas = 0.0
            self.gas = round(self.gas + random.uniform(0.001, 0.02), 3)
            self.gas_captured_at = now


def generate_telegram(state: MeterState, now=None) -> bytes:
    """Telegrama DSMR 5.0.2 completo, con la estructura de 6.13 del estandar."""
    now = now or datetime.now(METER_TZ)
    gas_moment = state.gas_captured_at or now
    lines = [
        f"/{IDENT}",
        "",
        "1-3:0.2.8(50)",
        f"0-0:1.0.0({format_timestamp(now)})",
        f"0-0:96.1.1({EQUIPMENT_ID})",
        f"1-0:1.8.1({state.energy_in_t1:010.3f}*kWh)",
        f"1-0:1.8.2({state.energy_in_t2:010.3f}*kWh)",
        f"1-0:2.8.1({state.energy_out_t1:010.3f}*kWh)",
        f"1-0:2.8.2({state.energy_out_t2:010.3f}*kWh)",
        f"0-0:96.14.0({state.tariff:04d})",
        f"1-0:1.7.0({state.power_in:06.3f}*kW)",
        f"1-0:2.7.0({state.power_out:06.3f}*kW)",
        "0-0:96.7.21(00004)",
        "0-0:96.7.9(00002)",
        "1-0:99.97.0(0)(0-0:96.7.19)",
        "1-0:32.32.0(00002)",
        "1-0:52.32.0(00001)",
        "1-0:72.32.0(00000)",
        "1-0:32.36.0(00000)",
        "1-0:52.36.0(00003)",
        "1-0:72.36.0(00000)",
        "0-0:96.13.0()",
        f"1-0:32.7.0({state.voltage[0]:05.1f}*V)",
        f"1-0:52.7.0({state.voltage[1]:05.1f}*V)",
        f"1-0:72.7.0({state.voltage[2]:05.1f}*V)",
        f"1-0:31.7.0({state.current[0]:03d}*A)",
        f"1-0:51.7.0({state.current[1]:03d}*A)",
        f"1-0:71.7.0({state.current[2]:03d}*A)",
        f"1-0:21.7.0({state.phase_power[0]:06.3f}*kW)",
        f"1-0:41.7.0({state.phase_power[1]:06.3f}*kW)",
        f"1-0:61.7.0({state.phase_power[2]:06.3f}*kW)",
        "1-0:22.7.0(00.000*kW)",
        "1-0:42.7.0(00.000*kW)",
        "1-0:62.7.0(00.000*kW)",
        "0-1:24.1.0(003)",
        f"0-1:96.1.0({GAS_EQUIPMENT_ID})",
        f"0-1:24.2.1({format_timestamp(gas_moment)})({state.gas:09.3f}*m3)",
    ]
    body = "\r\n".join(lines) + "\r\n"
    crc = crc16_arc((body + "!").encode("ascii"))
    return (body + f"!{crc:04X}\r\n").encode("ascii")
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest test_dsmr.py -q
```

Esperado: 38 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/dsmr.py v2/test_dsmr.py
git commit -m "feat(v2): generacion de telegramas DSMR 5.0.2 completos"
```

---

### Tarea 5: `serial_link.py` y `relay.py` — la Pi lectora

La Pi #1 no sabe qué es DSMR: sondea puertos, se queda con el que emite `/` y reenvía bytes crudos. Esto ya existe y funciona con hardware real en v1; se copia quitando la dependencia de Zeroconf, que con direcciones fijas no aporta nada.

**Archivos:**
- Crear: `v2/serial_link.py` (copia de `v1/serial_link.py`, sin cambios de lógica)
- Crear: `v2/relay.py`
- Crear: `v2/test_relay.py`

**Interfaces:**
- Consume: nada de tareas previas. La sonda busca un `/` genérico, que es el primer byte tanto de un telegrama real como del generado en la Tarea 4.
- Produce: de `serial_link.py`: `BAUDRATE = 115200`, `candidate_devices(prefer_usb=False) -> list[str]`, `carries_telegrams(ser, probe_seconds=3.0) -> bool`, `open_meter_port(...)`, `wait_and_open(...)`, `port_still_present(port) -> bool`, `open_all_ports(...)`. De `relay.py`: `DOWNSTREAM_PORT = 4000`, `RelayServer` con `start()`, `broadcast(data: bytes)`, `stop()`, y el atributo `clients: list`; `serial_reader_loop(relay, serial_factory=None, retry_seconds=2.0)`.

- [ ] **Paso 1: Copiar `serial_link.py` y escribir los tests que fallan**

```bash
cp v1/serial_link.py v2/serial_link.py
```

`v1/serial_link.py` se copia tal cual: sus comentarios documentan dos comportamientos aprendidos con hardware real (Windows no falla el `write()` de un adaptador desenchufado, y en la Pi el medidor puede colgar del UART interno sin `vid`) que siguen valiendo. No tocarlo.

Crear `v2/test_relay.py`:

```python
import socket
import threading
import time

from dsmr import MeterState, generate_telegram
from relay import RelayServer, serial_reader_loop


class SerialFalso:
    """Puerto serie de mentira: entrega trozos ya preparados y luego se cuelga."""

    def __init__(self, chunks):
        self.port = "fake0"
        self._chunks = list(chunks)
        self.in_waiting = 0
        self.closed = False

    def read(self, _n):
        if self._chunks:
            return self._chunks.pop(0)
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_relay_reenvia_los_bytes_a_un_cliente_conectado():
    relay = RelayServer(port=puerto_libre())
    relay.start()
    try:
        cliente = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
        # El accept ocurre en otro hilo; se espera a que lo registre.
        for _ in range(100):
            if relay.clients:
                break
            time.sleep(0.01)
        relay.broadcast(b"hola")
        cliente.settimeout(2)
        assert cliente.recv(16) == b"hola"
    finally:
        cliente.close()
        relay.stop()


def test_relay_reenvia_un_telegrama_intacto():
    """El CRC lo calcula el medidor y debe seguir siendo valido al otro lado:
    por eso la Pi lectora reenvia crudo y no reserializa nada."""
    telegrama = generate_telegram(MeterState())
    relay = RelayServer(port=puerto_libre())
    relay.start()
    try:
        cliente = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
        for _ in range(100):
            if relay.clients:
                break
            time.sleep(0.01)
        relay.broadcast(telegrama)
        cliente.settimeout(2)
        recibido = b""
        while len(recibido) < len(telegrama):
            recibido += cliente.recv(4096)
        assert recibido == telegrama
    finally:
        cliente.close()
        relay.stop()


def test_relay_descarta_un_cliente_muerto_sin_caerse():
    relay = RelayServer(port=puerto_libre())
    relay.start()
    try:
        cliente = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
        for _ in range(100):
            if relay.clients:
                break
            time.sleep(0.01)
        cliente.close()
        for _ in range(5):
            relay.broadcast(b"x" * 1024)
        assert relay.clients == []
    finally:
        relay.stop()


def test_serial_reader_loop_publica_lo_que_lee():
    relay = RelayServer(port=puerto_libre())
    relay.start()
    recibido = []
    original = relay.broadcast
    relay.broadcast = lambda data: recibido.append(data)
    hilo = threading.Thread(
        target=serial_reader_loop,
        args=(relay,),
        kwargs={"serial_factory": lambda: SerialFalso([b"abc", b"def"])},
        daemon=True,
    )
    hilo.start()
    try:
        for _ in range(200):
            if b"".join(recibido) == b"abcdef":
                break
            time.sleep(0.01)
        assert b"".join(recibido) == b"abcdef"
    finally:
        relay.broadcast = original
        relay.stop()
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_relay.py -q
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'relay'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Crear `v2/relay.py`:

```python
"""Pi lectora: reenvia por TCP lo que llegue por el puerto serie.

No parsea nada a proposito. El telegrama ya es ASCII autodescriptivo y viene
con un CRC calculado por el medidor; reenviarlo intacto mantiene esa
proteccion valida hasta el consumidor final y deja un unico parser en todo el
sistema. Ademas hace que el mismo codigo sirva para un medidor real y para el
simulador, sin ninguna rama condicional.
"""

import socket
import threading
import time

from serial_link import BAUDRATE, wait_and_open

DOWNSTREAM_PORT = 4000


class RelayServer:
    def __init__(self, port=DOWNSTREAM_PORT):
        self.port = port
        self.clients = []
        self.lock = threading.Lock()
        self.running = True
        self.sock = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.listen(5)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                break
            conn.settimeout(5.0)
            with self.lock:
                self.clients.append(conn)

    def broadcast(self, data: bytes):
        with self.lock:
            dead = []
            for conn in self.clients:
                try:
                    conn.sendall(data)
                except OSError:
                    dead.append(conn)
            for conn in dead:
                self.clients.remove(conn)
                conn.close()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        with self.lock:
            for conn in self.clients:
                conn.close()
            self.clients.clear()


def serial_reader_loop(relay: RelayServer, serial_factory=None, retry_seconds=2.0):
    serial_factory = serial_factory or (lambda: wait_and_open(BAUDRATE))
    while True:
        try:
            ser = serial_factory()
        except OSError:
            time.sleep(retry_seconds)
            continue
        print(f"puerto serie conectado: {ser.port}", flush=True)
        try:
            while True:
                chunk = ser.read(ser.in_waiting or 1)
                if chunk:
                    relay.broadcast(chunk)
        except OSError:
            print("puerto serie perdido, reintentando", flush=True)
        finally:
            ser.close()
        time.sleep(retry_seconds)


def main():
    print("relay iniciando", flush=True)
    relay = RelayServer()
    relay.start()
    serial_reader_loop(relay)


if __name__ == "__main__":
    main()
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest -q
```

Esperado: 42 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/serial_link.py v2/relay.py v2/test_relay.py
git commit -m "feat(v2): Pi lectora que reenvia bytes crudos por TCP"
```

---

### Tarea 6: `store.py` — persistencia en SQLite

**Archivos:**
- Crear: `v2/store.py`
- Crear: `v2/test_store.py`

**Interfaces:**
- Consume: nada. Recibe valores ya numéricos; no sabe de OBIS.
- Produce: `COLUMNS: tuple[str, ...]` (las 13 columnas de medida, en orden), `Store(path)` con `save(ts: int, values: dict) -> None`, `history(minutes: int, now: int | None = None) -> list[dict]`, `prune(days: int = 7, now: int | None = None) -> int`, `close() -> None`. `history` devuelve diccionarios con la clave `ts` más las de `COLUMNS`, ordenados por `ts` ascendente.

- [ ] **Paso 1: Escribir los tests que fallan**

Crear `v2/test_store.py`:

```python
import pytest

from store import COLUMNS, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "readings.db")
    yield s
    s.close()


def test_guarda_y_devuelve_una_lectura(store):
    store.save(1000, {"power_in": 1.25, "voltage_l1": 230.1})
    filas = store.history(minutes=60, now=1000)
    assert len(filas) == 1
    assert filas[0]["ts"] == 1000
    assert filas[0]["power_in"] == 1.25
    assert filas[0]["voltage_l1"] == 230.1


def test_los_campos_ausentes_quedan_en_null(store):
    """Un medidor monofasico no informa L2 ni L3. Guardar NULL es la respuesta
    honesta; inventar un 0.0 mentiria en la grafica."""
    store.save(1000, {"power_in": 1.25})
    assert store.history(minutes=60, now=1000)[0]["voltage_l3"] is None


def test_ignora_claves_que_no_son_columnas(store):
    """Blindaje contra un codigo OBIS nuevo que llegue a la capa de datos."""
    store.save(1000, {"power_in": 1.0, "campo_inventado": 42})
    assert store.history(minutes=60, now=1000)[0]["power_in"] == 1.0


def test_una_marca_de_tiempo_repetida_sobrescribe(store):
    """El reloj del medidor puede repetir un segundo. Duplicar la fila
    ensuciaria la grafica; la clave primaria lo resuelve sin codigo extra."""
    store.save(1000, {"power_in": 1.0})
    store.save(1000, {"power_in": 2.0})
    filas = store.history(minutes=60, now=1000)
    assert len(filas) == 1
    assert filas[0]["power_in"] == 2.0


def test_history_respeta_la_ventana(store):
    store.save(1000, {"power_in": 1.0})
    store.save(4000, {"power_in": 2.0})
    filas = store.history(minutes=10, now=4000)
    assert [f["ts"] for f in filas] == [4000]


def test_history_devuelve_en_orden_ascendente(store):
    for ts in (3000, 1000, 2000):
        store.save(ts, {"power_in": 1.0})
    filas = store.history(minutes=60, now=3000)
    assert [f["ts"] for f in filas] == [1000, 2000, 3000]


def test_prune_borra_lo_mas_viejo_que_la_retencion(store):
    ahora = 10_000_000
    store.save(ahora - 8 * 86400, {"power_in": 1.0})
    store.save(ahora - 1 * 86400, {"power_in": 2.0})
    assert store.prune(days=7, now=ahora) == 1
    filas = store.history(minutes=60 * 24 * 30, now=ahora)
    assert [f["ts"] for f in filas] == [ahora - 86400]


def test_usa_wal(store):
    """Sin WAL, una escritura por segundo significa un fsync por segundo
    contra la tarjeta SD."""
    modo = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert modo.lower() == "wal"


def test_las_columnas_declaradas_existen_en_la_tabla(store):
    reales = {fila[1] for fila in store.conn.execute("PRAGMA table_info(readings)")}
    assert set(COLUMNS) | {"ts"} == reales
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_store.py -q
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'store'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Crear `v2/store.py`:

```python
"""Persistencia de lecturas en SQLite.

No se guarda el telegrama crudo: pesa cerca de 1 KB y llegan 86.400 por dia,
que serian unos 86 MB diarios sobre la tarjeta SD. El ultimo telegrama vive en
memoria, que es donde el dashboard lo necesita.
"""

import sqlite3
import threading
import time

COLUMNS = (
    "power_in", "power_out",
    "energy_in_t1", "energy_in_t2", "energy_out_t1", "energy_out_t2",
    "voltage_l1", "voltage_l2", "voltage_l3",
    "current_l1", "current_l2", "current_l3",
    "gas",
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS readings (
  ts INTEGER PRIMARY KEY,
  {', '.join(f'{name} REAL' for name in COLUMNS)}
);
"""


class Store:
    def __init__(self, path):
        # check_same_thread=False porque el hilo del enlace TCP escribe y los
        # hilos del servidor HTTP leen. El lock de abajo es lo que serializa.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL evita un fsync por segundo contra la SD; NORMAL acepta perder
        # los ultimos segundos ante un corte de luz, que para datos de
        # prototipo es un intercambio obvio.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._lock = threading.Lock()

    def save(self, ts: int, values: dict) -> None:
        present = [name for name in COLUMNS if name in values]
        columns = ["ts"] + present
        row = [ts] + [values[name] for name in present]
        placeholders = ", ".join("?" * len(columns))
        # INSERT OR REPLACE sobre la clave primaria: si el reloj del medidor
        # repite un segundo, la fila se sobrescribe en vez de duplicarse.
        sql = (
            f"INSERT OR REPLACE INTO readings ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )
        with self._lock:
            self.conn.execute(sql, row)
            self.conn.commit()

    def history(self, minutes: int, now: int = None) -> list:
        now = int(time.time()) if now is None else now
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM readings WHERE ts >= ? ORDER BY ts",
                (now - minutes * 60,),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune(self, days: int = 7, now: int = None) -> int:
        now = int(time.time()) if now is None else now
        with self._lock:
            cursor = self.conn.execute(
                "DELETE FROM readings WHERE ts < ?", (now - days * 86400,)
            )
            self.conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self.conn.close()
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest test_store.py -q
```

Esperado: 9 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/store.py v2/test_store.py
git commit -m "feat(v2): persistencia SQLite con deduplicacion por marca de tiempo"
```

---

### Tarea 7: `server.py` — enlace con la Pi lectora y estado en vivo

La mitad de `server.py` que consume el flujo TCP. La otra mitad (HTTP) es la Tarea 8.

**Archivos:**
- Crear: `v2/server.py`
- Crear: `v2/test_server.py`

**Interfaces:**
- Consume: `TelegramReader`, `parse_telegram`, `parse_timestamp`, `number`, `InvalidTelegram` de `dsmr`; `Store` de `store`.
- Produce: `FIELDS: dict[str, tuple[str, int]]` (nombre de columna → código OBIS e índice), `GAS_CHANNELS`, `extract(objects: dict) -> dict`, `MeterLink(store, host, port)` con `start()`, `stop()`, `consume(chunk: bytes) -> None` y `snapshot() -> dict`. `snapshot()` devuelve `{"connected": bool, "ts": int|None, "values": dict, "telegram": str, "received": int, "rejected": int}`.

- [ ] **Paso 1: Escribir los tests que fallan**

Crear `v2/test_server.py`:

```python
import pytest

from dsmr import MeterState, generate_telegram
from server import MeterLink, extract
from store import Store


@pytest.fixture
def link(tmp_path):
    enlace = MeterLink(Store(tmp_path / "readings.db"), host="127.0.0.1", port=1)
    yield enlace
    enlace.store.close()


def test_extract_mapea_los_codigos_obis_a_columnas():
    from dsmr import parse_telegram
    objetos = parse_telegram(generate_telegram(MeterState()))["objects"]
    valores = extract(objetos)
    assert valores["power_in"] is not None
    assert valores["voltage_l1"] is not None
    assert valores["current_l3"] is not None
    assert valores["energy_in_t2"] is not None


def test_extract_encuentra_el_gas_en_cualquier_canal_mbus():
    """El canal del gas depende del orden de instalacion de los dispositivos
    M-Bus (7.3 del estandar): puede ser 1, 2, 3 o 4. Fijarlo en 1 haria que
    un medidor perfectamente normal apareciera sin gas."""
    assert extract({"0-3:24.2.1": ["101209112500W", "999.500*m3"]})["gas"] == 999.5


def test_extract_omite_lo_que_el_medidor_no_informa():
    valores = extract({"1-0:1.7.0": ["01.000*kW"]})
    assert valores == {"power_in": 1.0}


def test_consume_guarda_y_actualiza_el_estado(link):
    telegrama = generate_telegram(MeterState())
    link.consume(telegrama)
    estado = link.snapshot()
    assert estado["received"] == 1
    assert estado["rejected"] == 0
    assert estado["values"]["power_in"] is not None
    assert estado["telegram"].startswith("/")
    assert link.store.history(minutes=60, now=estado["ts"])


def test_consume_reensambla_telegramas_partidos(link):
    telegrama = generate_telegram(MeterState())
    for inicio in range(0, len(telegrama), 13):
        link.consume(telegrama[inicio:inicio + 13])
    assert link.snapshot()["received"] == 1


def test_consume_cuenta_los_crc_invalidos_sin_guardarlos(link):
    """Con un medidor real, este contador subiendo es la primera senal de un
    cable con ruido o demasiado largo."""
    telegrama = generate_telegram(MeterState())
    link.consume(telegrama[:-6] + b"0000\r\n")
    estado = link.snapshot()
    assert estado["rejected"] == 1
    assert estado["received"] == 0
    assert link.store.history(minutes=60) == []


def test_consume_ignora_un_telegrama_sin_marca_de_tiempo(link):
    """Sin marca de tiempo no hay clave primaria. Se refleja en vivo pero no
    se persiste, en vez de inventar una hora."""
    from dsmr import crc16_arc
    cuerpo = "/ISK5\r\n\r\n1-0:1.7.0(01.000*kW)\r\n"
    crc = crc16_arc((cuerpo + "!").encode("ascii"))
    link.consume((cuerpo + f"!{crc:04X}\r\n").encode("ascii"))
    estado = link.snapshot()
    assert estado["received"] == 1
    assert estado["values"]["power_in"] == 1.0
    assert link.store.history(minutes=60) == []


def test_snapshot_arranca_desconectado(link):
    estado = link.snapshot()
    assert estado["connected"] is False
    assert estado["values"] == {}
    assert estado["telegram"] == ""
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_server.py -q
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'server'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Crear `v2/server.py`:

```python
"""Pi con pantalla: consume el flujo de la lectora y sirve el dashboard."""

import os
import socket
import threading
import time

from dsmr import InvalidTelegram, TelegramReader, number, parse_telegram, parse_timestamp
from store import Store

READER_HOST = os.environ.get("SMARTMETER_READER_HOST", "192.168.7.1")
READER_PORT = int(os.environ.get("SMARTMETER_READER_PORT", "4000"))
DB_PATH = os.environ.get("SMARTMETER_DB", "/var/lib/smartmeter/readings.db")
TIMESTAMP_CODE = "0-0:1.0.0"

# Que codigo OBIS alimenta cada columna. Este mapa es la unica parte del
# sistema que conoce codigos concretos, y vive aqui a proposito: que mostrar
# es decision de la presentacion, no de la lectura.
FIELDS = {
    "power_in": ("1-0:1.7.0", 0),
    "power_out": ("1-0:2.7.0", 0),
    "energy_in_t1": ("1-0:1.8.1", 0),
    "energy_in_t2": ("1-0:1.8.2", 0),
    "energy_out_t1": ("1-0:2.8.1", 0),
    "energy_out_t2": ("1-0:2.8.2", 0),
    "voltage_l1": ("1-0:32.7.0", 0),
    "voltage_l2": ("1-0:52.7.0", 0),
    "voltage_l3": ("1-0:72.7.0", 0),
    "current_l1": ("1-0:31.7.0", 0),
    "current_l2": ("1-0:51.7.0", 0),
    "current_l3": ("1-0:71.7.0", 0),
}

# El canal M-Bus del gas depende del orden de instalacion (7.3 del estandar).
GAS_CHANNELS = (1, 2, 3, 4)


def extract(objects: dict) -> dict:
    """Codigos OBIS a columnas. Solo incluye lo que el medidor informa."""
    values = {}
    for name, (code, index) in FIELDS.items():
        value = number(objects, code, index)
        if value is not None:
            values[name] = value
    for channel in GAS_CHANNELS:
        gas = number(objects, f"0-{channel}:24.2.1", index=1)
        if gas is not None:
            values["gas"] = gas
            break
    return values


class MeterLink:
    """Cliente TCP hacia la Pi lectora, con el estado en vivo del medidor."""

    def __init__(self, store: Store, host=READER_HOST, port=READER_PORT):
        self.store = store
        self.host = host
        self.port = port
        self.running = True
        self._reader = TelegramReader()
        self._lock = threading.Lock()
        self._connected = False
        self._ts = None
        self._values = {}
        self._telegram = ""
        self.received = 0
        self.rejected = 0

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._prune_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def consume(self, chunk: bytes) -> None:
        for raw in self._reader.feed(chunk):
            try:
                parsed = parse_telegram(raw)
            except InvalidTelegram:
                with self._lock:
                    self.rejected += 1
                continue
            objects = parsed["objects"]
            values = extract(objects)
            stamps = objects.get(TIMESTAMP_CODE) or []
            ts = parse_timestamp(stamps[0]) if stamps else None
            with self._lock:
                self.received += 1
                self._ts = ts
                self._values = values
                self._telegram = raw.decode("ascii", errors="replace")
            # Sin marca de tiempo no hay clave primaria. Se muestra en vivo
            # igual, pero no se persiste con una hora inventada.
            if ts is not None and values:
                try:
                    self.store.save(ts, values)
                except Exception as error:
                    # Disco lleno o base bloqueada no puede apagar el vivo.
                    print(f"no se pudo guardar la lectura: {error}", flush=True)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self._connected,
                "ts": self._ts,
                "values": dict(self._values),
                "telegram": self._telegram,
                "received": self.received,
                "rejected": self.rejected,
            }

    def _run(self):
        delay = 1.0
        while self.running:
            try:
                conn = socket.create_connection((self.host, self.port), timeout=10)
            except OSError as error:
                print(f"sin enlace con la lectora ({error}), reintentando", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            delay = 1.0
            with self._lock:
                self._connected = True
            print(f"enlace establecido con {self.host}:{self.port}", flush=True)
            try:
                with conn:
                    conn.settimeout(30)
                    while self.running:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        self.consume(chunk)
            except OSError as error:
                print(f"enlace perdido: {error}", flush=True)
            finally:
                with self._lock:
                    self._connected = False

    def _prune_loop(self):
        while self.running:
            time.sleep(3600)
            try:
                self.store.prune(days=7)
            except Exception as error:
                print(f"no se pudo limpiar la base: {error}", flush=True)
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest test_server.py -q
```

Esperado: 8 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/server.py v2/test_server.py
git commit -m "feat(v2): enlace TCP con la lectora, estado en vivo y persistencia"
```

---

### Tarea 8: `server.py` — servidor HTTP

Tres rutas con `http.server` de la biblioteca estándar. Sin Flask ni FastAPI: no hay nada que justifique una dependencia para tres rutas. Sin Server-Sent Events: un `fetch` por segundo es menos código en las dos puntas y se recupera solo cuando un teléfono se va de la WiFi y vuelve.

**Archivos:**
- Modificar: `v2/server.py`
- Modificar: `v2/test_server.py`

**Interfaces:**
- Consume: `MeterLink` de la Tarea 7, `Store` de la Tarea 6.
- Produce: `HTTP_PORT = 8080`, `STATIC_DIR`, `make_server(link, port=HTTP_PORT) -> ThreadingHTTPServer`, `main()`.

- [ ] **Paso 1: Escribir los tests que fallan**

Añadir a `v2/test_server.py`:

```python
import json
import threading
import urllib.request

from server import make_server


@pytest.fixture
def servidor(link):
    httpd = make_server(link, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def pedir(base, ruta):
    with urllib.request.urlopen(base + ruta, timeout=5) as respuesta:
        return respuesta.status, respuesta.read()


def test_latest_devuelve_json_con_el_estado(servidor, link):
    link.consume(generate_telegram(MeterState()))
    estado, cuerpo = pedir(servidor, "/api/latest")
    assert estado == 200
    datos = json.loads(cuerpo)
    assert datos["received"] == 1
    assert datos["values"]["power_in"] is not None
    assert datos["telegram"].startswith("/")


def test_latest_funciona_con_el_enlace_caido(servidor):
    """El dashboard tiene que poder decir 'sin enlace' en vez de no cargar."""
    estado, cuerpo = pedir(servidor, "/api/latest")
    assert estado == 200
    datos = json.loads(cuerpo)
    assert datos["connected"] is False
    assert datos["values"] == {}


def test_history_devuelve_las_lecturas_guardadas(servidor, link):
    link.consume(generate_telegram(MeterState()))
    estado, cuerpo = pedir(servidor, "/api/history?minutes=60")
    assert estado == 200
    filas = json.loads(cuerpo)
    assert len(filas) == 1
    assert filas[0]["power_in"] is not None


def test_history_acepta_minutes_invalido_sin_reventar(servidor):
    estado, cuerpo = pedir(servidor, "/api/history?minutes=no-es-un-numero")
    assert estado == 200
    assert json.loads(cuerpo) == []


def test_raiz_sirve_el_dashboard(servidor):
    estado, cuerpo = pedir(servidor, "/")
    assert estado == 200
    assert b"<html" in cuerpo.lower()


def test_ruta_desconocida_da_404(servidor):
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as error:
        pedir(servidor, "/no-existe")
    assert error.value.code == 404
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_server.py -q
```

Esperado: FAIL con `ImportError: cannot import name 'make_server' from 'server'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Crear el marcador de posición `v2/static/index.html` para que la ruta raíz tenga qué servir (la página real es la Tarea 9):

```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Smart Meter</title></head>
<body><p>Dashboard pendiente.</p></body></html>
```

Añadir a `v2/server.py` (más `import json` y `from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer` y `from urllib.parse import parse_qs, urlparse` arriba):

```python
HTTP_PORT = int(os.environ.get("SMARTMETER_HTTP_PORT", "8080"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class DashboardHandler(BaseHTTPRequestHandler):
    link = None  # lo inyecta make_server

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/":
            return self._send_file("index.html", "text/html; charset=utf-8")
        if route.path == "/api/latest":
            return self._send_json(self.link.snapshot())
        if route.path == "/api/history":
            return self._send_json(self._history(route.query))
        self.send_error(404)

    def _history(self, query):
        raw = parse_qs(query).get("minutes", ["60"])[0]
        try:
            minutes = int(raw)
        except ValueError:
            # Un parametro roto no debe tumbar el dashboard entero.
            return []
        return self.link.store.history(minutes=max(1, min(minutes, 60 * 24 * 7)))

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # El dashboard consulta cada segundo; una respuesta cacheada seria
        # un dashboard congelado.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name, content_type):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as handle:
                body = handle.read()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Silencio: una linea por peticion son 86.400 lineas de journal por
        dia por espectador, y ninguna dice nada."""


def make_server(link, port=HTTP_PORT):
    handler = type("BoundHandler", (DashboardHandler,), {"link": link})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    httpd.daemon_threads = True
    return httpd


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    link = MeterLink(Store(DB_PATH))
    link.start()
    print(f"dashboard en http://0.0.0.0:{HTTP_PORT}", flush=True)
    make_server(link).serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest -q
```

Esperado: 56 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/server.py v2/static/index.html v2/test_server.py
git commit -m "feat(v2): servidor HTTP con las rutas del dashboard"
```

---

### Tarea 9: `static/index.html` — el dashboard

Una sola página, sin dependencias externas. La Pi puede no tener internet: nada de CDNs ni librerías de gráficas. La curva se dibuja sobre un `<canvas>`.

Textos visibles en inglés, como en v1. Se lee bien en la pantalla pequeña de la Pi y en un teléfono.

**Archivos:**
- Modificar: `v2/static/index.html` (reemplaza el marcador de la Tarea 8)

**Interfaces:**
- Consume: `GET /api/latest` y `GET /api/history?minutes=N` de la Tarea 8.
- Produce: nada que consuma otra tarea.

- [ ] **Paso 1: Escribir la página**

Reemplazar el contenido completo de `v2/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart Meter P1</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 16px; background: #12151a; color: #e6e9ef;
         font: 15px/1.4 system-ui, -apple-system, Segoe UI, sans-serif; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  #status { font-size: 13px; margin-bottom: 16px; color: #9aa4b2; }
  #status.live { color: #4ade80; }
  #status.down { color: #f87171; }
  .grid { display: grid; gap: 10px;
          grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
  .card { background: #1b1f27; border-radius: 8px; padding: 12px; }
  .card h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
             color: #8b95a5; margin: 0 0 6px; font-weight: 600; }
  .value { font: 600 24px/1 ui-monospace, Consolas, monospace; }
  .unit { font-size: 13px; color: #8b95a5; margin-left: 3px; }
  section { margin-top: 18px; }
  canvas { width: 100%; height: 200px; background: #1b1f27; border-radius: 8px; }
  pre { background: #1b1f27; border-radius: 8px; padding: 12px; overflow-x: auto;
        font: 12px/1.45 ui-monospace, Consolas, monospace; color: #b9c2d0;
        max-height: 260px; }
</style>
</head>
<body>
<h1>Smart Meter P1</h1>
<div id="status">Connecting…</div>

<div class="grid" id="cards"></div>

<section>
  <canvas id="chart" width="900" height="200"></canvas>
</section>

<section>
  <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#8b95a5">
    Latest raw telegram</h2>
  <pre id="telegram">—</pre>
</section>

<script>
// Que tarjeta se dibuja para cada campo. Los campos que el medidor no informa
// simplemente no aparecen: un medidor monofasico no manda L2 ni L3, y eso es
// normal, no un error que haya que mostrar como un hueco.
const CARDS = [
  ["power_in",      "Power drawn",     "kW",  3],
  ["power_out",     "Power returned",  "kW",  3],
  ["energy_in_t1",  "Energy T1 in",    "kWh", 3],
  ["energy_in_t2",  "Energy T2 in",    "kWh", 3],
  ["energy_out_t1", "Energy T1 out",   "kWh", 3],
  ["energy_out_t2", "Energy T2 out",   "kWh", 3],
  ["voltage_l1",    "Voltage L1",      "V",   1],
  ["voltage_l2",    "Voltage L2",      "V",   1],
  ["voltage_l3",    "Voltage L3",      "V",   1],
  ["current_l1",    "Current L1",      "A",   0],
  ["current_l2",    "Current L2",      "A",   0],
  ["current_l3",    "Current L3",      "A",   0],
  ["gas",           "Gas",             "m³",  3],
];

function renderCards(values) {
  document.getElementById("cards").innerHTML = CARDS
    .filter(([key]) => values[key] !== undefined && values[key] !== null)
    .map(([key, label, unit, digits]) => `
      <div class="card">
        <h2>${label}</h2>
        <div class="value">${values[key].toFixed(digits)}<span class="unit">${unit}</span></div>
      </div>`)
    .join("");
}

function renderStatus(data) {
  const el = document.getElementById("status");
  const when = data.ts ? new Date(data.ts * 1000).toLocaleTimeString() : "—";
  if (data.connected) {
    el.className = "live";
    el.textContent = `Live · ${when} · ${data.received} telegrams · ${data.rejected} rejected`;
  } else {
    el.className = "down";
    el.textContent = `No link to the reader Pi · showing stored history · ` +
                     `${data.received} telegrams · ${data.rejected} rejected`;
  }
}

function drawChart(rows) {
  const canvas = document.getElementById("chart");
  // El canvas se estira por CSS; sin esto la curva sale borrosa en la pantalla
  // de la Pi, que tiene una relacion de pixeles distinta a la del navegador.
  const ratio = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * ratio;
  canvas.height = canvas.clientHeight * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const w = canvas.clientWidth, h = canvas.clientHeight, pad = 24;
  ctx.clearRect(0, 0, w, h);

  const points = rows.filter(r => r.power_in !== null);
  if (points.length < 2) return;

  const values = points.map(r => r.power_in);
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = (hi - lo) || 1;
  const x = i => pad + (i / (points.length - 1)) * (w - pad * 2);
  const y = v => h - pad - ((v - lo) / span) * (h - pad * 2);

  ctx.strokeStyle = "#2a3040";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const gy = pad + (i / 4) * (h - pad * 2);
    ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(w - pad, gy); ctx.stroke();
  }

  ctx.strokeStyle = "#4ade80";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((r, i) => i ? ctx.lineTo(x(i), y(r.power_in))
                             : ctx.moveTo(x(i), y(r.power_in)));
  ctx.stroke();

  ctx.fillStyle = "#8b95a5";
  ctx.font = "11px ui-monospace, Consolas, monospace";
  ctx.fillText(hi.toFixed(3) + " kW", pad, pad - 8);
  ctx.fillText(lo.toFixed(3) + " kW", pad, h - 8);
}

async function tick() {
  try {
    const data = await (await fetch("/api/latest", { cache: "no-store" })).json();
    renderStatus(data);
    renderCards(data.values);
    document.getElementById("telegram").textContent = data.telegram || "—";
  } catch (error) {
    document.getElementById("status").className = "down";
    document.getElementById("status").textContent = "Dashboard unreachable";
  }
}

async function refreshChart() {
  try {
    drawChart(await (await fetch("/api/history?minutes=15", { cache: "no-store" })).json());
  } catch (error) { /* la grafica puede esperar al proximo ciclo */ }
}

// Polling en vez de SSE: menos codigo en las dos puntas, sin conexiones largas
// que limpiar, y se recupera solo cuando un telefono se va de la WiFi y vuelve.
tick(); refreshChart();
setInterval(tick, 1000);
setInterval(refreshChart, 5000);
</script>
</body>
</html>
```

- [ ] **Paso 2: Verificar que los tests siguen pasando**

```bash
cd v2 && python -m pytest -q
```

Esperado: 56 passed. `test_raiz_sirve_el_dashboard` sigue valiendo porque la página real también contiene `<html`.

- [ ] **Paso 3: Comprobarlo a ojo con datos reales**

En una terminal, generar datos y levantar el servidor contra una base temporal:

```bash
cd v2 && SMARTMETER_DB=/tmp/smartmeter-demo.db python -c "
import threading, time
from dsmr import MeterState, generate_telegram
from server import MeterLink, make_server
from store import Store

link = MeterLink(Store('/tmp/smartmeter-demo.db'), host='127.0.0.1', port=1)
state = MeterState()

def feed():
    while True:
        state.tick(1.0)
        link.consume(generate_telegram(state))
        time.sleep(0.2)

threading.Thread(target=feed, daemon=True).start()
print('http://127.0.0.1:8080')
make_server(link, port=8080).serve_forever()
"
```

Abrir `http://127.0.0.1:8080`. Confirmar: las tarjetas se llenan, el contador de telegramas sube, la curva se dibuja después de unos segundos, y el telegrama crudo se ve completo. Cortar con Ctrl+C y borrar `/tmp/smartmeter-demo.db`.

- [ ] **Paso 4: Commit**

```bash
git add v2/static/index.html
git commit -m "feat(v2): dashboard web sin dependencias externas"
```

---

### Tarea 10: `meter_simulator.py` — el generador de Plan B

Se hereda de `v1/meter_simulator.py` la interfaz gráfica, la selección de puerto y la escritura simultánea a todos los adaptadores USB, que ya resuelve el problema de no saber cuál de los puertos COM es el cable. Lo que cambia son los valores mostrados, ahora que el telegrama trae mucho más que tres campos.

**Archivos:**
- Crear: `v2/meter_simulator.py`
- Crear: `v2/test_meter_simulator.py`

**Interfaces:**
- Consume: `MeterState`, `generate_telegram` de `dsmr`; `open_all_ports`, `port_still_present`, `BAUDRATE` de `serial_link`.
- Produce: `MeterSerialWriter(serial_factory=None, baudrate=BAUDRATE)` con `start()`, `stop()`, `status() -> tuple[list[str], int, list[str]]`, `reading_snapshot() -> dict`, `latest_telegram_snapshot() -> str`; `SmartMeterGeneratorUI(root, writer)`; `main()`.

- [ ] **Paso 1: Escribir los tests que fallan**

Crear `v2/test_meter_simulator.py`:

```python
import time

import pytest

from dsmr import TelegramReader, parse_telegram
from meter_simulator import MeterSerialWriter


class PuertoFalso:
    def __init__(self, nombre="fake0"):
        self.port = nombre
        self.escrito = b""
        self.in_waiting = 0
        self.closed = False

    def write(self, data):
        self.escrito += data

    def close(self):
        self.closed = True


def esperar(condicion, plazo=5.0):
    limite = time.time() + plazo
    while time.time() < limite:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def test_escribe_telegramas_validos_en_el_puerto():
    puerto = PuertoFalso()
    writer = MeterSerialWriter(serial_factory=lambda: [puerto])
    writer.start()
    try:
        assert esperar(lambda: TelegramReader().feed(puerto.escrito))
        telegrama = TelegramReader().feed(puerto.escrito)[0]
        # Si el parser lo acepta, el simulador emite el mismo formato que
        # esperamos leer de un medidor real.
        assert parse_telegram(telegrama)["objects"]["1-3:0.2.8"] == ["50"]
    finally:
        writer.stop()


def test_escribe_en_todos_los_puertos_a_la_vez():
    """El generador no puede sondear: escribe y nadie le contesta, asi que no
    sabe cual adaptador es el cable. Elegir uno seria adivinar."""
    puertos = [PuertoFalso("a"), PuertoFalso("b"), PuertoFalso("c")]
    writer = MeterSerialWriter(serial_factory=lambda: puertos)
    writer.start()
    try:
        assert esperar(lambda: all(p.escrito for p in puertos))
    finally:
        writer.stop()


def test_reading_snapshot_expone_los_campos_del_dashboard():
    writer = MeterSerialWriter(serial_factory=lambda: [PuertoFalso()])
    lectura = writer.reading_snapshot()
    for clave in ("power_in", "energy_in_t2", "voltage_l1", "gas"):
        assert clave in lectura


def test_stop_cierra_los_puertos():
    puerto = PuertoFalso()
    writer = MeterSerialWriter(serial_factory=lambda: [puerto])
    writer.start()
    assert esperar(lambda: puerto.escrito)
    writer.stop()
    assert esperar(lambda: puerto.closed)
```

- [ ] **Paso 2: Ejecutar los tests para verificar que fallan**

```bash
cd v2 && python -m pytest test_meter_simulator.py -q
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'meter_simulator'`.

- [ ] **Paso 3: Escribir la implementación mínima**

Crear `v2/meter_simulator.py`. Partir de `v1/meter_simulator.py` y aplicar exactamente estos cambios; el resto (la clase `SmartMeterGeneratorUI`, `_port_alive`, `_drop`, `_note` y `main`) se copia sin tocar, porque documenta comportamiento aprendido con hardware real:

1. Cambiar el import a `from dsmr import MeterState, generate_telegram` (mismo nombre, nueva implementación) y `APP_VERSION = "5.0 P1"`.
2. Reemplazar `reading_snapshot` por:

```python
    def reading_snapshot(self) -> dict:
        with self.lock:
            return {
                "power_in": self.state.power_in,
                "energy_in_t2": self.state.energy_in_t2,
                "voltage_l1": self.state.voltage[0],
                "gas": self.state.gas,
            }
```

3. En `_tick`, reemplazar el bloque que lee el estado por:

```python
        with self.lock:
            self.state.tick(1.0)
            telegram = generate_telegram(self.state)
            energy_kwh = self.state.energy_in_t2
```

4. En `SmartMeterGeneratorUI._build`, cambiar las tres tarjetas por cuatro: `Power`, `Energy T2`, `Voltage L1`, `Gas`. En `_refresh`, leer del diccionario:

```python
        lectura = self.writer.reading_snapshot()
        self.power_label.configure(text=f"{lectura['power_in']:.3f} kW")
        self.energy_label.configure(text=f"{lectura['energy_in_t2']:,.3f} kWh")
        self.voltage_label.configure(text=f"{lectura['voltage_l1']:.1f} V")
        self.gas_label.configure(text=f"{lectura['gas']:,.3f} m³")
```

5. Cambiar el pie de la ventana a `"DSMR 5.0.2  |  Serial output  |  1 telegram per second"` y agrandar el `Text` del telegrama a `height=14`, porque ahora son 37 líneas en vez de 5.

- [ ] **Paso 4: Ejecutar los tests para verificar que pasan**

```bash
cd v2 && python -m pytest -q
```

Esperado: 60 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/meter_simulator.py v2/test_meter_simulator.py
git commit -m "feat(v2): simulador que emite telegramas DSMR 5.0.2 completos"
```

---

### Tarea 11: prueba de extremo a extremo sin hardware

La cadena entera —simulador, puerto serie, relay, TCP, parser, SQLite, HTTP— con un pty haciendo de cable. Es la prueba que confirma que las piezas encajan, y la que va a fallar primero si alguien rompe un contrato entre módulos.

**Archivos:**
- Crear: `v2/test_integration_e2e.py`

**Interfaces:**
- Consume: todo lo anterior.
- Produce: nada.

- [ ] **Paso 1: Escribir el test que falla**

Crear `v2/test_integration_e2e.py`:

```python
import json
import os
import socket
import threading
import time
import urllib.request

import pytest

import serial

from dsmr import MeterState, generate_telegram
from relay import RelayServer, serial_reader_loop
from server import MeterLink, make_server
from store import Store

pytestmark = pytest.mark.skipif(
    not hasattr(os, "openpty"), reason="requiere pty (POSIX)"
)


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar(condicion, plazo=15.0):
    limite = time.time() + plazo
    while time.time() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


def test_del_simulador_al_dashboard_sin_hardware(tmp_path):
    """La cadena completa con un pty haciendo de cable serie.

    Simulador -> pty -> relay -> TCP -> parser -> SQLite -> HTTP.
    """
    maestro, esclavo = os.openpty()
    lectura_relay = serial.Serial(os.ttyname(esclavo), timeout=1)

    relay = RelayServer(port=puerto_libre())
    relay.start()
    threading.Thread(
        target=serial_reader_loop,
        args=(relay,),
        kwargs={"serial_factory": lambda: lectura_relay},
        daemon=True,
    ).start()

    link = MeterLink(
        Store(tmp_path / "readings.db"), host="127.0.0.1", port=relay.port
    )
    link.start()
    httpd = make_server(link, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    detener = threading.Event()

    def simulador():
        estado = MeterState()
        while not detener.is_set():
            estado.tick(1.0)
            os.write(maestro, generate_telegram(estado))
            time.sleep(0.2)

    threading.Thread(target=simulador, daemon=True).start()

    try:
        assert esperar(lambda: link.snapshot()["received"] >= 3), "no llegaron telegramas"

        with urllib.request.urlopen(base + "/api/latest", timeout=5) as r:
            ultimo = json.loads(r.read())
        assert ultimo["connected"] is True
        assert ultimo["rejected"] == 0, "hubo telegramas con CRC invalido"
        assert ultimo["values"]["power_in"] is not None
        assert ultimo["values"]["voltage_l3"] is not None
        assert ultimo["values"]["gas"] is not None
        assert ultimo["telegram"].startswith("/")

        with urllib.request.urlopen(base + "/api/history?minutes=60", timeout=5) as r:
            historia = json.loads(r.read())
        assert historia, "nada llego a SQLite"
        assert historia[-1]["power_in"] is not None
    finally:
        detener.set()
        httpd.shutdown()
        httpd.server_close()
        link.stop()
        link.store.close()
        relay.stop()
        lectura_relay.close()
        os.close(maestro)
        os.close(esclavo)
```

- [ ] **Paso 2: Ejecutar el test para verificar que falla o se salta**

```bash
cd v2 && python -m pytest test_integration_e2e.py -q
```

Esperado en Linux o macOS: FAIL hasta que todo encaje. Esperado en Windows: `1 skipped` (no hay `os.openpty`). En ese caso, ejecutarlo en la Raspberry Pi antes de dar la tarea por terminada; una prueba de integración que solo se salta no prueba nada.

- [ ] **Paso 3: Corregir lo que la prueba destape**

No se anticipa código nuevo: si esta prueba falla, es porque una interfaz entre módulos no coincide con lo que declara su tarea. Arreglar el módulo, no la prueba.

- [ ] **Paso 4: Ejecutar la suite completa**

```bash
cd v2 && python -m pytest -q
```

Esperado en la Pi: 61 passed.

- [ ] **Paso 5: Commit**

```bash
git add v2/test_integration_e2e.py
git commit -m "test(v2): prueba de extremo a extremo con pty, sin hardware"
```

---

### Tarea 12: instaladores, servicios y kiosco

Sin esto no hay prototipo: hay scripts que alguien tiene que arrancar a mano cada vez.

**Archivos:**
- Crear: `v2/systemd/relay.service`
- Crear: `v2/systemd/display.service`
- Crear: `v2/systemd/smartmeter-kiosk.desktop`
- Crear: `v2/install_reader.sh`
- Crear: `v2/install_display.sh`

**Interfaces:**
- Consume: `relay.py` y `serial_link.py` (Tarea 5); `server.py`, `store.py`, `dsmr.py`, `static/` (Tareas 6-9).
- Produce: nada que consuma otra tarea.

- [ ] **Paso 1: Escribir las unidades de systemd**

Crear `v2/systemd/relay.service`:

```ini
[Unit]
Description=Smart meter P1 relay (serie a TCP)
After=network.target

[Service]
Type=simple
User=relay
WorkingDirectory=/opt/smartmeter
ExecStart=/usr/bin/python3 /opt/smartmeter/relay.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Crear `v2/systemd/display.service`:

```ini
[Unit]
Description=Smart meter dashboard (enlace TCP, SQLite y HTTP)
After=network.target

[Service]
Type=simple
User=smartmeter
WorkingDirectory=/opt/smartmeter
ExecStart=/usr/bin/python3 /opt/smartmeter/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Crear `v2/systemd/smartmeter-kiosk.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Smart Meter Kiosk
Exec=/opt/smartmeter/kiosk.sh
X-GNOME-Autostart-enabled=true
```

- [ ] **Paso 2: Escribir `install_reader.sh`**

Crear `v2/install_reader.sh`:

```bash
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
```

- [ ] **Paso 3: Escribir `install_display.sh`**

Crear `v2/install_display.sh`:

```bash
#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHERNET_INTERFACE="${SMARTMETER_ETHERNET_INTERFACE:-eth0}"
ETHERNET_ADDRESS="${SMARTMETER_ETHERNET_ADDRESS:-192.168.7.2/24}"
WIFI_INTERFACE="${SMARTMETER_WIFI_INTERFACE:-wlan0}"
AP_SSID="${SMARTMETER_AP_SSID:-smartmeter}"
CONNECTION_NAME="smartmeter-direct"

# Una clave fija en un repositorio no es una clave. Si no viene por entorno se
# genera una y se imprime al final, en vez de usar un valor por omision.
AP_PASSWORD="${SMARTMETER_AP_PASSWORD:-}"
if [ -z "$AP_PASSWORD" ]; then
    AP_PASSWORD="$(tr -dc 'a-z0-9' </dev/urandom | head -c 12)"
    GENERADA=1
fi

sudo apt-get update
sudo apt-get install -y python3 network-manager chromium-browser
sudo systemctl enable --now NetworkManager

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

# Chromium falla si el servidor todavia no responde, y la pantalla se queda en
# una pagina de error hasta que alguien la refresque a mano. Esperar el puerto
# es la diferencia entre encender la Pi y ver el dashboard, o no verlo.
sudo tee /opt/smartmeter/kiosk.sh >/dev/null <<'KIOSK'
#!/bin/bash
for _ in $(seq 1 60); do
    if (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; then break; fi
    sleep 1
done
exec chromium-browser --kiosk --noerrdialogs --disable-infobars \
    --check-for-update-interval=31536000 http://localhost:8080
KIOSK
sudo chmod +x /opt/smartmeter/kiosk.sh
sudo cp "$SCRIPT_DIR/systemd/smartmeter-kiosk.desktop" \
    /etc/xdg/autostart/smartmeter-kiosk.desktop

sudo cp "$SCRIPT_DIR/systemd/display.service" /etc/systemd/system/display.service
sudo systemctl daemon-reload
sudo systemctl enable display

if ! sudo nmcli --wait 15 connection up "$CONNECTION_NAME"; then
    echo "Ethernet sin enlace por ahora; se activara al conectar el cable."
fi

sudo systemctl restart display
echo "Pi de pantalla instalada en $ETHERNET_ADDRESS."
echo "Dashboard: http://localhost:8080 (local) y por la red $AP_SSID."
if [ -n "$GENERADA" ]; then
    echo "Clave del AP generada: $AP_PASSWORD"
    echo "Guardala: no se vuelve a mostrar. Para fijar otra, reejecuta con"
    echo "SMARTMETER_AP_PASSWORD=... bash install_display.sh"
fi
echo "Estado: systemctl status display"
echo "Datos:  journalctl -u display -f"
```

- [ ] **Paso 4: Verificar la sintaxis de los scripts**

```bash
bash -n v2/install_reader.sh && bash -n v2/install_display.sh && echo "sintaxis correcta"
```

Esperado: `sintaxis correcta`.

- [ ] **Paso 5: Commit**

```bash
chmod +x v2/install_reader.sh v2/install_display.sh
git add v2/systemd v2/install_reader.sh v2/install_display.sh
git commit -m "build(v2): instaladores, servicios systemd y kiosco"
```

---

### Tarea 13: `README.md` y verificación final

**Archivos:**
- Crear: `v2/README.md`

**Interfaces:**
- Consume: todo lo anterior.
- Produce: nada.

- [ ] **Paso 1: Escribir el README**

Crear `v2/README.md` con estas secciones, escritas a partir de lo que quedó implementado (no copiar la spec: el README dice cómo usarlo, la spec dice por qué es así):

1. **Qué es.** Prototipo que lee un puerto P1 de un medidor holandés desde una Raspberry Pi y lo muestra en una segunda Pi con pantalla, que además sirve el dashboard por WiFi.
2. **Los dos planes**, con el diagrama de la spec. Plan A con cable P1 comercial; Plan B con el simulador por USB-serie. Dejar claro que el software es el mismo y que solo cambia el cable.
3. **Advertencia de hardware.** El puerto P1 entrega una señal invertida de 5 V open collector y no transmite hasta que Data Request está en alto (§5.7 del estándar). Conectar el RJ12 directo al GPIO de la Pi no funciona. Este prototipo asume un cable P1 comercial que trae el inversor y la alimentación adentro. Ese cable es de recepción y no sirve para simular un medidor.
4. **Requisitos.** Python 3.11+ y `pyserial` en la Pi lectora y en el simulador. Raspberry Pi OS con NetworkManager en ambas Pis. Chromium en la Pi de pantalla.
5. **Instalación**, en tres pasos: `bash install_reader.sh` en la Pi #1, `bash install_display.sh` en la Pi #2, y `python meter_simulator.py` en la computadora si se usa Plan B.
6. **Conexión.** Cable Ethernet entre las dos Pis. Cable P1 (Plan A) o USB-serie (Plan B) en la Pi lectora. El dashboard aparece solo en la pantalla; los teléfonos se conectan a la red `smartmeter` y abren `http://192.168.7.2:8080`.
7. **Variables de entorno**, en tabla: `SMARTMETER_PORT`, `SMARTMETER_ETHERNET_INTERFACE`, `SMARTMETER_ETHERNET_ADDRESS`, `SMARTMETER_WIFI_INTERFACE`, `SMARTMETER_AP_SSID`, `SMARTMETER_AP_PASSWORD`, `SMARTMETER_READER_HOST`, `SMARTMETER_READER_PORT`, `SMARTMETER_HTTP_PORT`, `SMARTMETER_DB`.
8. **Pruebas.** `cd v2 && python -m pytest -q`. Aclarar que la prueba de extremo a extremo se salta en Windows porque necesita `os.openpty`, y que hay que ejecutarla en la Pi.
9. **Diagnóstico.** Tabla de síntoma y causa: el dashboard dice "No link to the reader Pi" (revisar cable y `systemctl status relay`); el contador de rechazados sube (cable con ruido o demasiado largo); el relay no encuentra puerto (`journalctl -u relay -f` lista los puertos probados); la pantalla muestra error en vez del dashboard (`systemctl status display`).
10. **Alcance y limitaciones**, resumido de la spec: no es un producto certificado, el dashboard no tiene autenticación, el TCP entre las Pis va en texto plano, Plan A no está verificado con hardware real, y DSMR anterior a la versión 4 (9600 baudios, 7E1) queda fuera de alcance.

- [ ] **Paso 2: Verificar que la suite completa pasa**

```bash
cd v2 && python -m pytest -q
```

Esperado en la Pi: 61 passed. En Windows: 60 passed, 1 skipped.

- [ ] **Paso 3: Verificar que no quedó ninguna dependencia de v1**

```bash
cd v2 && grep -rn "from v1\|import v1\|\.\./v1" --include="*.py" --include="*.sh" . ; echo "salida vacia = correcto"
```

Esperado: sin coincidencias.

- [ ] **Paso 4: Verificar que no se coló ninguna dependencia externa nueva**

```bash
cd v2 && grep -rhn "^import \|^from " --include="*.py" . | grep -v "^.*:from dsmr\|from store\|from server\|from relay\|from serial_link" | sort -u
```

Revisar a ojo: solo debe haber módulos de la biblioteca estándar y `serial`. Cualquier otra cosa es una dependencia que se coló y hay que quitarla.

- [ ] **Paso 5: Commit**

```bash
git add v2/README.md
git commit -m "docs(v2): README del prototipo 2"
```

---

## Auto-repaso del plan

**Cobertura de la spec.** Fundamentos del estándar → Tareas 1-4. Arquitectura y Pi lectora → Tarea 5. Parser genérico → Tareas 2-3. SQLite con conversión de marca de tiempo → Tareas 1 y 6. Servidor y rutas → Tareas 7-8. Dashboard → Tarea 9. Simulador → Tarea 10. Configuración de red, AP, kiosco y servicios → Tarea 12. Manejo de errores → repartido: puerto ausente y desconexión en caliente en la Tarea 5, enlace caído en la 7, CRC inválido en la 7, campo OBIS ausente en las 2 y 6, base de datos con fallo en la 7. Pruebas → cada tarea trae las suyas, más la Tarea 11. Alcance y limitaciones → Tarea 13.

**Sin huecos.** No queda ningún requisito de la spec sin tarea.

**Consistencia de tipos.** `number(objects, code, index)` se define en la Tarea 2 y se usa con esa firma en las Tareas 4, 7 y en los tests. `Store.save(ts, values)`, `Store.history(minutes, now)` y `Store.prune(days, now)` se definen en la Tarea 6 y se usan igual en las Tareas 7, 8 y 11. `MeterLink.snapshot()` devuelve las mismas seis claves en la Tarea 7, en el JSON de la Tarea 8 y en el JavaScript de la Tarea 9. `reading_snapshot()` cambia de tupla a diccionario en la Tarea 10 y el test lo fija. Las 13 columnas de `COLUMNS` en la Tarea 6 coinciden una a una con las claves que produce `extract()` en la Tarea 7 y con las tarjetas de `CARDS` en la Tarea 9.

**Sobre los valores esperados de la Tarea 1.** Las pruebas de marca de tiempo construyen el segundo unix esperado con un `datetime` en UTC explícito en vez de escribirlo a mano. Es deliberado: un número mágico mal calculado en el offset es exactamente el bug que esas pruebas existen para atrapar, y escribirlo a mano lo deja pasar.
