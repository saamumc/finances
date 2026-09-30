from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from lumina.core import database as db
from lumina.core import migrations_v16 as m16


class MigracionV16Tests(unittest.TestCase):
    def test_agrega_variacion_firmada_y_conserva_valoracion_existente(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "finances.db"
            conn = sqlite3.connect(ruta)
            conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO config VALUES ('schema_version','15')")
            conn.execute("CREATE TABLE movimientos_inversion ("
                         "id INTEGER PRIMARY KEY, tipo TEXT NOT NULL, monto INTEGER NOT NULL)")
            conn.execute("INSERT INTO movimientos_inversion (tipo,monto) VALUES ('VALORACION',25000)")
            conn.execute("PRAGMA user_version=15")
            conn.commit()

            m16.aplicar_v16(conn)

            cols = {row[1] for row in conn.execute("PRAGMA table_info(movimientos_inversion)")}
            self.assertIn("variacion_valor", cols)
            self.assertEqual(
                conn.execute("SELECT monto, variacion_valor FROM movimientos_inversion").fetchone(),
                (25000, 25000),
            )
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 16)
            self.assertEqual(
                conn.execute("SELECT value FROM config WHERE key='schema_version'").fetchone()[0],
                "16",
            )

            conn.close()

    def test_base_nueva_init_db_llega_a_v16(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = db.DB_PATH
            try:
                db.DB_PATH = Path(tmp) / "finances.db"
                db.init_db()
                conn = sqlite3.connect(db.DB_PATH)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 16)
                cols = {row[1] for row in conn.execute("PRAGMA table_info(movimientos_inversion)")}
                self.assertIn("variacion_valor", cols)
                conn.close()
            finally:
                db.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
