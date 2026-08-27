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
