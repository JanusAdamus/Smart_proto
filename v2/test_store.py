import sqlite3

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


def test_save_revierte_la_transaccion_si_falla_commit(store):
    def denegar_commit(action, parametro, _tabla, _base, _origen):
        if action == sqlite3.SQLITE_TRANSACTION and parametro == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store.conn.set_authorizer(denegar_commit)

    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        store.save(1000, {"power_in": 1.0})

    assert not store.conn.in_transaction
    assert store.history(minutes=60, now=1000) == []


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
