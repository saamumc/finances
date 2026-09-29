"""Fallo A: ``FinanceService.estado_actual`` delega en ``engine.financial_state``.

Contrato: devuelve la foto completa del motor y conserva
``['liquidity']['by_person']``, la única clave que consume ``ui/app.py``.
"""
import tempfile
import unittest
from pathlib import Path

from lumina.core import calculations as calc
from lumina.core import database as db
from lumina.core import engine
from lumina.ui.service import FinanceService

MES = "2026-09"
CLAVES_DEL_MOTOR = ("flow", "liquidity", "wealth", "debt", "savings", "couple_historical")


class EstadoActualContratoTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.tempdir.name) / "finances.db"
        db.init_db()
        self.service = FinanceService()

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.tempdir.cleanup()

    def _con_datos(self):
        self.service.crear_ingreso(MES, "Samuel", "Salario", "4000000")
        self.service.crear_ingreso(MES, "Sara", "Salario", "3000000")
        db.registrar_gasto(MES, "Mercado", "Hogar", 300_000, "2026-09-03", "debito", "persona1",
                           "compartido", 150_000, 150_000)

    def test_es_la_foto_del_motor(self):
        self._con_datos()
        self.assertEqual(self.service.estado_actual(MES), engine.financial_state(MES))

    def test_expone_las_claves_del_motor(self):
        self._con_datos()
        estado = self.service.estado_actual(MES)
        for clave in CLAVES_DEL_MOTOR:
            self.assertIn(clave, estado)
        self.assertIn("available", estado["liquidity"])

    def test_conserva_by_person_que_consume_la_ui(self):
        self._con_datos()
        estado = self.service.estado_actual(MES)
        self.assertEqual(estado["liquidity"]["by_person"], calc.liquidez_por_persona(MES))
        for persona in ("persona1", "persona2"):
            self.assertIn("liquidez", estado["liquidity"]["by_person"][persona])

    def test_base_vacia_no_falla(self):
        estado = self.service.estado_actual(MES)
        self.assertEqual(estado["scope"], "pareja")
        self.assertEqual(set(estado["liquidity"]["by_person"]), {"persona1", "persona2"})

    def test_es_de_solo_lectura(self):
        self._con_datos()
        antes = engine.snapshot_database()
        self.service.estado_actual(MES)
        self.assertEqual(engine.snapshot_database(), antes)


if __name__ == "__main__":
    unittest.main()
