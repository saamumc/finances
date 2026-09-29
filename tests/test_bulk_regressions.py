"""Regresiones agrupadas para la estabilización del Financial OS."""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from lumina.advisor import intelligence as iq
from lumina.core import database as db
from lumina.core import engine
from lumina.ui.service import FinanceService


class BulkRegressionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "finances.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_estado_actual_es_la_foto_canónica_del_motor(self):
        service = FinanceService()
        mes = "2026-09"
        service.crear_ingreso(mes, "Samuel", "Salario", 4_000_000)
        self.assertEqual(service.estado_actual(mes), engine.financial_state(mes))
        self.assertEqual(
            service.estado_actual(mes)["liquidity"]["by_person"],
            db and __import__("lumina.core.calculations", fromlist=["liquidez_por_persona"]).liquidez_por_persona(mes),
        )

    def test_diferir_tarjeta_tiene_ruta_propia_y_no_recomienda_un_pago(self):
        contexto = iq.clasificar_intencion(
            "¿podemos diferir alguna tarjeta y pagar algunas en específico?"
        )
        self.assertEqual(contexto["intencion"], "card_deferral")
        respuesta = iq.responder(
            "2026-09",
            "¿podemos diferir alguna tarjeta y pagar algunas en específico?",
        )
        self.assertEqual(respuesta["intencion"], "card_deferral")
        self.assertIn("qué tarjeta", respuesta["respuesta"].lower())
        self.assertIn("cuotas", respuesta["respuesta"].lower())

    def test_eliminar_gasto_fijo_es_desactivación_lógica(self):
        gasto_id = db.registrar_gasto_fijo(
            "Internet", "Servicios", 100_000, "mensual",
            "persona1", "compartido", 50_000, 50_000,
        )
        db.eliminar_gasto_fijo(gasto_id)
        fila = db.get_gasto_fijo(gasto_id)
        self.assertEqual(fila["activo"], 0)
        self.assertEqual(len(db.get_gastos_fijos(solo_activos=True)), 0)

    def test_codigo_de_eliminar_gasto_fijo_no_contiene_delete(self):
        fuente = inspect.getsource(db.eliminar_gasto_fijo).upper()
        self.assertNotIn("DELETE FROM", fuente)

    def test_codigo_de_limpieza_de_reversiones_no_borra_filas(self):
        fuente = inspect.getsource(db.corregir_reversiones_orfanas).upper()
        self.assertNotIn("DELETE FROM", fuente)


if __name__ == "__main__":
    unittest.main()
