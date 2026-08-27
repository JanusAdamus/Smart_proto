from datetime import datetime, timezone

import pytest

from dsmr import (
    InvalidTelegram,
    as_number,
    crc16_arc,
    format_timestamp,
    number,
    parse_telegram,
    parse_timestamp,
)


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
    momento = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
    assert format_timestamp(momento) == "260715123000S"
    assert parse_timestamp(format_timestamp(momento)) == int(momento.timestamp())


def test_is_summer_time_clava_los_bordes_del_cambio():
    """En 2026 el cambio cae el 29 de marzo y el 25 de octubre, a las 01:00 UTC.
    Un borde mal puesto desplaza una hora todos los telegramas de ese dia."""
    assert format_timestamp(datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)) == "260329015900W"
    assert format_timestamp(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)) == "260329030000S"
    assert format_timestamp(datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc)) == "261025025900S"
    assert format_timestamp(datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)) == "261025020000W"


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


def test_parse_rechaza_bytes_no_ascii():
    """Un byte de ruido en el cable no debe tumbar al consumidor.

    Tiene que salir por el mismo camino que un CRC que no coincide: telegrama
    descartado, contador que sube, servicio que sigue vivo.
    """
    with pytest.raises(InvalidTelegram):
        parse_telegram(b"/ISK5\r\n\r\n1-0:1.7.0(00.\xff00*kW)\r\n!0000\r\n")


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


def test_la_marca_de_captura_del_gas_no_avanza_con_el_valor_quieto():
    """Un M-Bus reporta el ultimo valor leido junto al momento en que lo leyo.

    Marca que corre con valor congelado es imposible en un medidor real, y era
    lo que pasaba durante los primeros 5 minutos de vida del estado.
    """
    estado = MeterState()
    marcas = set()
    for segundo in range(3):
        estado.tick(1.0)
        telegrama = generate_telegram(
            estado, now=datetime(2026, 8, 27, 12, 0, segundo, tzinfo=timezone.utc)
        )
        valores = parse_telegram(telegrama)["objects"]["0-1:24.2.1"]
        marcas.add(valores[0])
    assert len(marcas) == 1, f"la marca de captura cambio sin cambiar el gas: {marcas}"
