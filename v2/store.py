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

    def history(self, minutes: int, now: int | None = None) -> list[dict]:
        now = int(time.time()) if now is None else now
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM readings WHERE ts >= ? ORDER BY ts",
                (now - minutes * 60,),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune(self, days: int = 7, now: int | None = None) -> int:
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
