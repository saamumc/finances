import tempfile
import unittest
from pathlib import Path

from lumina.core import database as db
from lumina.core import financial_plan


class FinancialPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "finances.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.original
        self.tmp.cleanup()

    def test_inversiones_y_movimientos_cuadran(self):
        iid = financial_plan.crear_inversion(
            nombre="Prueba", clase="cdt", titular="persona1",
            riesgo="bajo", liquidez="baja",
        )
        financial_plan.registrar_movimiento_inversion(iid, "APORTE", 1_000_000)
        financial_plan.registrar_movimiento_inversion(iid, "VALORACION", 50_000)
        financial_plan.registrar_movimiento_inversion(iid, "RETIRO", 100_000)
        r = financial_plan.resumen_inversiones()
        self.assertEqual(r["aportes"], 1_000_000)
        self.assertEqual(r["retiros"], 100_000)
        self.assertEqual(r["valoraciones"], 50_000)
        self.assertEqual(r["total"], 950_000)

    def test_proyeccion_no_mueve_la_bd(self):
        antes = db.DB_PATH.read_bytes()
        r = financial_plan.proyeccion_inversion(100_000, 12, 1200)
        despues = db.DB_PATH.read_bytes()
        self.assertEqual(antes, despues)
        self.assertGreater(r["valor_proyectado"], r["aportado"])

    def test_deuda_no_tarjeta_entra_en_total(self):
        did = financial_plan.registrar_deuda(
            acreedor="Banco", tipo="prestamo", titular="persona1",
            saldo=2_000_000, tasa_ea_pb=1800, pago_minimo=100_000,
        )
        self.assertGreater(did, 0)
        r = financial_plan.resumen_deudas()
        self.assertEqual(r["otras_total"], 2_000_000)
        self.assertEqual(r["total"], 2_000_000)

    def test_plan_financiero_exhibe_modulos(self):
        r = financial_plan.financial_os("2026-09")
        for key in ("flujo", "liquidez", "patrimonio", "tarjetas", "ahorros",
                    "deudas", "inversiones", "emergencia", "plan_deuda",
                    "preparacion_inversion", "proyeccion", "prioridades"):
            self.assertIn(key, r)


if __name__ == "__main__":
    unittest.main()
