"""Tests de la migración fundacional v14 → v15.

Se congela el esquema v14 en ``fixtures/schema_v14.sql`` (extraído de una base
real) para que estos tests no dependan del código de migración que prueban.
Ejecutar:  python -m unittest lumina.tests.test_migracion_v15 -v
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lumina.core import calculations as calc
from lumina.core import database as db
from lumina.core import migrations_v15 as m15

FIXTURE = Path(__file__).parent / "fixtures" / "schema_v14.sql"
_PALABRAS_DINERO = re.compile(r"(monto|saldo|valor|aporte|pago|precio|patrimonio|total|comision)", re.I)
_MOVIMIENTOS = ("parametros_externos", "modelo_pareja", "aportes_pozo", "pagos_deudas",
                "fondo_emergencia_config", "metas_plan", "ingresos_clasificacion", "metas_ingreso",
                "valoraciones_activo", "patrimonio_snapshot", "movimientos_inversion",
                "escenarios_guardados", "compras_en_espera")


def construir_base_v14(ruta: Path) -> None:
    """Base v14 sintética con los casos que rompen migraciones descuidadas."""
    conn = sqlite3.connect(ruta)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    u = iter(f"uuid-{i:04d}" for i in range(1000))
    x = conn.execute
    x("INSERT INTO config VALUES ('schema_version','14')")
    x("INSERT INTO config VALUES ('preferencia_x','conservar')")
    # Tarjeta 1: deuda histórica PAGADA por completo (pendiente=0, inicial>0):
    # una migración legacy re-ejecutada la "resucitaría".
    x("""INSERT INTO tarjetas (nombre, propietario, cupo_total, saldo_deuda, pago_minimo, interes_mensual,
         saldo_inicial_historico, saldo_historico_pendiente, creado_en, actualizado_en)
         VALUES ('Visa', 'persona1', 5000000, 0, 0, 2.1, 1000000, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')""")
    x("""INSERT INTO tarjetas (nombre, propietario, cupo_total, saldo_deuda, pago_minimo, interes_mensual,
         saldo_inicial_historico, saldo_historico_pendiente, creado_en, actualizado_en)
         VALUES ('Master', 'persona2', 3000000, 800000, 60000, 2.4, 0, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')""")
    x("""INSERT INTO compras_tarjeta (tarjeta_id, descripcion, valor_original, fecha, mes, cuotas_totales,
         valor_pendiente, responsabilidad, monto_p1, monto_p2, transaction_uuid)
         VALUES (2, 'Nevera', 900000, '2026-08-05', '2026-08', 3, 800000, 'compartido', 450000, 450000, ?)""", (next(u),))
    x("""INSERT INTO compras_tarjeta (tarjeta_id, descripcion, valor_original, fecha, mes, valor_pendiente,
         responsabilidad, monto_p1, monto_p2, estado, motivo_reversion, fecha_reversion, transaction_uuid)
         VALUES (2, 'Error de digitación', 50000, '2026-08-06', '2026-08', 50000, 'persona1', 50000, 0,
         'REVERSADO', 'duplicada', '2026-08-07', ?)""", (next(u),))
    x("""INSERT INTO pagos_deuda (mes, fecha, tarjeta_id, monto, pagador, monto_aportado_p1, monto_aportado_p2,
         transaction_uuid) VALUES ('2026-08', '2026-08-20', 2, 100000, 'persona1', 100000, 0, ?)""", (next(u),))
    x("INSERT INTO asignaciones_pagos (pago_id, compra_id, monto_asignado, fecha) VALUES (1, 1, 100000, '2026-08-20')")
    for mes, persona, valor in (("2026-07", "persona1", 4000000), ("2026-08", "persona1", 4200000), ("2026-08", "persona2", 2500000)):
        x("INSERT INTO ingresos (mes, persona, concepto, valor, transaction_uuid) VALUES (?,?,?,?,?)",
          (mes, persona, "Salario", valor, next(u)))
    x("""INSERT INTO gastos (mes, nombre, categoria, valor, fecha, monto_p1, monto_p2, responsabilidad, transaction_uuid)
         VALUES ('2026-08', 'Mercado', 'Comida', 300000, '2026-08-03', 150000, 150000, 'compartido', ?)""", (next(u),))
    x("""INSERT INTO liquidaciones_pareja (mes, fecha, deudor, acreedor, monto, concepto, transaction_uuid)
         VALUES ('2026-08', '2026-08-25', 'persona2', 'persona1', 75000, 'Ajuste', ?)""", (next(u),))
    x("INSERT INTO ahorros (nombre, creado_en) VALUES ('Fondo de emergencia', '2026-01-01T00:00:00')")
    x("""INSERT INTO movimientos_ahorro (ahorro_id, mes, fecha, tipo, monto, concepto, transaction_uuid)
         VALUES (1, '2026-08', '2026-08-10', 'DEPOSITO', 500000, 'Aporte', ?)""", (next(u),))
    x("INSERT INTO metas (nombre, monto_objetivo, fecha_objetivo, ahorro_id, creado_en) VALUES ('Viaje', 6000000, '2027-06-30', 1, '2026-01-01T00:00:00')")
    x("INSERT INTO terceros (nombre, creado_en) VALUES ('Tío Pedro', '2026-01-01T00:00:00')")
    # Préstamo de persona2 con titularidad 'persona1': el UPDATE legacy lo reescribiría.
    x("""INSERT INTO prestamos_terceros (tercero_id, mes, fecha, tipo, propietario, titularidad, concepto,
         monto_original, saldo_pendiente, transaction_uuid)
         VALUES (1, '2026-05', '2026-05-01', 'POR_PAGAR', 'persona2', 'persona1', 'Préstamo', 500000, 300000, ?)""", (next(u),))
    x("""INSERT INTO abonos_terceros (prestamo_id, mes, fecha, monto, transaction_uuid)
         VALUES (1, '2026-06', '2026-06-01', 200000, ?)""", (next(u),))
    x("""INSERT INTO gastos_fijos (nombre, categoria, valor, propietario, creado_en, actualizado_en)
         VALUES ('Arriendo', 'Vivienda', 1500000, 'persona1', '2026-01-01T00:00:00', '2026-01-01T00:00:00')""")
    x("INSERT INTO movimientos_log (fecha, entidad, entidad_id, accion, detalle) VALUES ('2026-08-01T10:00:00','gasto',1,'CREAR','')")
    x("PRAGMA user_version = 14")
    conn.commit()
    conn.close()


def _sha(ruta: Path) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def _tablas_v14(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        if r[0] not in m15.TABLAS_V15]


def _foto(ruta: Path) -> dict:
    """Contenido de las tablas v14 y DDL de todo objeto (tabla/índice) de v14."""
    conn = sqlite3.connect(ruta)
    try:
        datos = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in _tablas_v14(conn)}
        ddl = {n: s for n, s in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            f"AND tbl_name NOT IN ({','.join('?' * len(m15.TABLAS_V15))})", m15.TABLAS_V15)}
        return {"datos": datos, "ddl": ddl}
    finally:
        conn.close()


class MigracionV15(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.ruta = self.tmp / "finances.db"
        construir_base_v14(self.ruta)
        self._db_path = db.DB_PATH
        db.DB_PATH = self.ruta
        self.addCleanup(self._limpiar)

    def _limpiar(self) -> None:
        db.DB_PATH = self._db_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _versiones(self) -> tuple[int, str | None]:
        conn = sqlite3.connect(self.ruta)
        try:
            uv = conn.execute("PRAGMA user_version").fetchone()[0]
            fila = conn.execute("SELECT value FROM config WHERE key='schema_version'").fetchone()
            return uv, fila[0] if fila else None
        finally:
            conn.close()

    def _backups(self) -> list[Path]:
        return sorted(self.tmp.glob("finances.backup.*.db"))

    # ------------------------------------------------------------------ base
    def test_fixture_es_v14_real(self) -> None:
        conn = sqlite3.connect(self.ruta)
        existentes = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertEqual(self._versiones(), (14, "14"))
        self.assertFalse(existentes & set(m15.TABLAS_V15))

    def test_migra_a_v15_y_crea_todas_las_tablas(self) -> None:
        db.init_db()
        self.assertEqual(self._versiones(), (16, "16"))
        conn = sqlite3.connect(self.ruta)
        existentes = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertTrue(set(m15.TABLAS_V15) <= existentes)

    def test_base_nueva_llega_a_v15(self) -> None:
        self.ruta.unlink()
        db.init_db()
        self.assertEqual(self._versiones(), (15, "15"))
        conn = sqlite3.connect(self.ruta)
        existentes = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertTrue({"tarjetas", "gastos", *m15.TABLAS_V15} <= existentes)

    # --------------------------------------------------------------- backup
    def test_backup_previo_es_copia_exacta_de_v14(self) -> None:
        antes = _sha(self.ruta)
        db.init_db()
        backups = self._backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(_sha(backups[0]), antes)
        c = sqlite3.connect(backups[0])
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 14)
        c.close()

    def test_restaurar_backup_devuelve_exactamente_v14(self) -> None:
        antes = _sha(self.ruta)
        db.init_db()
        shutil.copy2(self._backups()[0], self.ruta)
        self.assertEqual(_sha(self.ruta), antes)
        self.assertEqual(self._versiones(), (14, "14"))

    # ------------------------------------------------ v14 intacto (el núcleo)
    def test_datos_y_ddl_v14_identicos_tras_migrar(self) -> None:
        antes = _foto(self.ruta)
        db.init_db()
        despues = _foto(self.ruta)
        # Única diferencia permitida: config.schema_version 14 -> 15.
        cfg_antes = {k: v for k, v in antes["datos"]["config"] if k != "schema_version"}
        cfg_despues = {k: v for k, v in despues["datos"]["config"] if k != "schema_version"}
        self.assertEqual(cfg_antes, cfg_despues)
        self.assertEqual(cfg_despues["preferencia_x"], "conservar")
        antes["datos"].pop("config"), despues["datos"].pop("config")
        self.assertEqual(antes["datos"], despues["datos"])
        self.assertEqual(antes["ddl"], despues["ddl"])

    def test_saldo_historico_pagado_no_resucita(self) -> None:
        """Regresión: _migrar_legacy restablecía saldo_historico_pendiente si era 0."""
        db.init_db()
        c = sqlite3.connect(self.ruta)
        fila = c.execute("SELECT saldo_inicial_historico, saldo_historico_pendiente FROM tarjetas WHERE id=1").fetchone()
        c.close()
        self.assertEqual(fila, (1000000, 0))

    def test_titularidad_de_prestamo_no_se_reescribe(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        fila = c.execute("SELECT propietario, titularidad FROM prestamos_terceros WHERE id=1").fetchone()
        c.close()
        self.assertEqual(fila, ("persona2", "persona1"))

    def test_reversados_y_uuid_se_conservan(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM compras_tarjeta WHERE estado='REVERSADO'").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM compras_tarjeta WHERE transaction_uuid IS NULL").fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM liquidaciones_pareja").fetchone()[0], 1)
        c.close()

    def test_calculos_existentes_no_cambian(self) -> None:
        def medir() -> dict:
            return {"liquidez": calc.liquidez_total(), "deuda": calc.deuda_total_tarjetas(),
                    "pareja": calc.balance_historico_pareja(), "ahorros": calc.resumen_ahorros()}
        antes = medir()
        db.init_db()
        self.assertEqual(medir(), antes)

    # ---------------------------------------------------------- idempotencia
    def test_segunda_ejecucion_no_hace_nada(self) -> None:
        db.init_db()
        tras_primera = _sha(self.ruta)
        db.init_db()
        self.assertEqual(_sha(self.ruta), tras_primera)
        self.assertEqual(len(self._backups()), 1)

    # ----------------------------------------------------------- atomicidad
    def test_fallo_a_mitad_deja_la_base_en_v14_limpia(self) -> None:
        antes = _foto(self.ruta)
        original = m15._SENTENCIAS
        m15._SENTENCIAS = original + ("CREATE TABLE tabla_rota (id INTEGER PRIMARY KEY, x TEXT NOT NULL CHECK (",)
        try:
            with self.assertRaises(sqlite3.Error):
                db.init_db()
        finally:
            m15._SENTENCIAS = original
        self.assertEqual(self._versiones(), (14, "14"))
        c = sqlite3.connect(self.ruta)
        existentes = {n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        self.assertFalse(existentes & set(m15.TABLAS_V15), "quedaron tablas v15 a medias")
        self.assertEqual(_foto(self.ruta), antes)
        # y se puede reintentar sin problemas
        db.init_db()
        self.assertEqual(self._versiones(), (16, "16"))

    # ----------------------------------- tablas nuevas: vacías y sin activar
    def test_tablas_nuevas_vacias_y_config_sin_reglas_sembradas(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        for tabla in m15.TABLAS_V15:
            self.assertEqual(c.execute(f'SELECT COUNT(*) FROM "{tabla}"').fetchone()[0], 0, tabla)
        claves = {k for (k,) in c.execute("SELECT key FROM config")}
        c.close()
        self.assertEqual(claves, {"schema_version", "preferencia_x"})

    def test_integridad_referencial_y_verificador_existente(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        c.close()
        estado = db.verificar_bd_integridad()
        self.assertTrue(estado["ok"], estado)
        self.assertEqual(estado["schema_esperado"], "16")

    # ---------------------------------------------------- reglas del esquema
    def test_dinero_solo_integer_y_sin_real(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        for tabla in m15.TABLAS_V15:
            for _, nombre, tipo, *_ in c.execute(f'PRAGMA table_info("{tabla}")'):
                self.assertNotIn(tipo.upper(), ("REAL", "FLOAT", "DOUBLE", "NUMERIC"), f"{tabla}.{nombre}")
                genericas = tabla in ("reglas_usuario", "parametros_externos") and nombre == "valor"
                if _PALABRAS_DINERO.search(nombre) and nombre != "pagador" and not genericas:
                    self.assertEqual(tipo.upper(), "INTEGER", f"{tabla}.{nombre} debe ser INTEGER")
        c.close()

    def test_movimientos_tienen_estado_y_uuid_unico(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        for tabla in _MOVIMIENTOS:
            columnas = {r[1] for r in c.execute(f'PRAGMA table_info("{tabla}")')}
            self.assertTrue({"estado", "motivo_reversion", "fecha_reversion", "transaction_uuid"} <= columnas, tabla)
            unicos = [i[1] for i in c.execute(f'PRAGMA index_list("{tabla}")') if i[2]]
            campos = {r[2] for n in unicos for r in c.execute(f'PRAGMA index_info("{n}")')}
            self.assertIn("transaction_uuid", campos, f"{tabla}: transaction_uuid no es UNIQUE")
        c.close()

    def test_estado_solo_admite_activo_o_reversado(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("""INSERT INTO aportes_pozo (mes, fecha, persona, monto, estado, transaction_uuid)
                         VALUES ('2026-09','2026-09-01','persona1',1000,'BORRADO','x')""")
        c.close()

    def test_no_duplica_tarjetas_ni_prestamos(self) -> None:
        db.init_db()
        c = sqlite3.connect(self.ruta)
        prohibidas = {"cupo_total", "saldo_deuda", "interes_mensual", "saldo_pendiente", "monto_original", "tercero_id"}
        for tabla in m15.TABLAS_V15:
            columnas = {r[1] for r in c.execute(f'PRAGMA table_info("{tabla}")')}
            self.assertFalse(columnas & prohibidas, f"{tabla} duplica campos de tarjetas/préstamos")
        c.close()

    def test_migracion_no_contiene_alter_drop_ni_delete(self) -> None:
        fuente = Path(m15.__file__).read_text(encoding="utf-8")
        for palabra in ("ALTER ", "DROP ", "DELETE ", "UPDATE ", "TRIGGER"):
            self.assertNotIn(palabra, fuente, palabra)


if __name__ == "__main__":
    unittest.main()
