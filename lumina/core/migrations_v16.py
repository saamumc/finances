# Migración v16: valoraciones de inversión con variación firmada.
# Additiva: conserva movimientos existentes y permite ganancias/pérdidas futuras.

from __future__ import annotations
import sqlite3

VERSION = 16


def aplicar_v16(conn: sqlite3.Connection) -> None:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current >= VERSION:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(movimientos_inversion)").fetchall()}
        if "variacion_valor" not in cols:
            conn.execute("ALTER TABLE movimientos_inversion ADD COLUMN variacion_valor INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE movimientos_inversion SET variacion_valor = CASE WHEN tipo='VALORACION' THEN monto ELSE 0 END")
        conn.execute("UPDATE config SET value=? WHERE key='schema_version'", (str(VERSION),))
        conn.execute(f"PRAGMA user_version = {VERSION}")
    except Exception:
        conn.rollback()
        raise
