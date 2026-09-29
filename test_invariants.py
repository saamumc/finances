import tempfile
import unittest
from pathlib import Path

from lumina.core import calculations as calc
from lumina.core import database as db
from lumina.constants import DuplicateOperationError, IntegrityError


class FinancialInvariantTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.tempdir.name) / "finances.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.tempdir.cleanup()

    def test_shared_card_purchase_preserves_split(self):
        card = db.registrar_tarjeta("Principal", "persona1", 2_000_000)
        gasto = db.registrar_gasto("2026-09", "Compra", "Hogar", 1_000_000, "2026-09-02",
                                   "tarjeta", "persona1", "compartido", 400_000, 600_000, card)
        compra = db.get_compras_tarjeta(tarjeta_id=card)[0]
        self.assertEqual(gasto, compra["gasto_id"])
        self.assertEqual(compra["monto_p1"] + compra["monto_p2"], compra["valor_original"])
        self.assertEqual(db.get_tarjetas()[0]["saldo_deuda"], 1_000_000)

    def test_card_payment_uses_fifo(self):
        card = db.registrar_tarjeta("Principal", "persona1", 2_000_000)
        first = db.registrar_gasto("2026-09", "Primera", "Hogar", 100_000, "2026-09-01",
                                    "tarjeta", "persona1", "persona1", 100_000, 0, card)
        second = db.registrar_gasto("2026-09", "Segunda", "Hogar", 300_000, "2026-09-02",
                                     "tarjeta", "persona1", "persona1", 300_000, 0, card)
        db.registrar_pago_tarjeta("2026-09", "2026-09-03", card, 150_000, "persona1", 150_000, 0)
        purchases = {row["gasto_id"]: row for row in db.get_compras_tarjeta(tarjeta_id=card)}
        self.assertEqual(purchases[first]["valor_pendiente"], 0)
        self.assertEqual(purchases[second]["valor_pendiente"], 250_000)
        self.assertEqual(calc.deuda_pendiente_por_responsabilidad(card), {"persona1": 250_000, "persona2": 0})

    def test_card_payment_is_not_counted_as_second_expense(self):
        card = db.registrar_tarjeta("Principal", "persona1", 1_000_000)
        db.registrar_gasto("2026-09", "Compra", "Hogar", 500_000, "2026-09-02",
                           "tarjeta", "persona1", "persona1", 500_000, 0, card)
        before = calc.liquidez_total("2026-09")
        db.registrar_pago_tarjeta("2026-09", "2026-09-05", card, 500_000, "persona1", 500_000, 0)
        self.assertEqual(calc.liquidez_total("2026-09"), before)
        self.assertEqual(db.get_tarjetas()[0]["saldo_deuda"], 0)

    def test_payment_reversal_restores_debt_and_assignments(self):
        card = db.registrar_tarjeta("Principal", "persona1", 1_000_000)
        db.registrar_gasto("2026-09", "Compra", "Hogar", 600_000, "2026-09-02",
                           "tarjeta", "persona1", "compartido", 250_000, 350_000, card)
        payment = db.registrar_pago_tarjeta("2026-09", "2026-09-03", card, 400_000,
                                            "persona2", 100_000, 300_000)
        db.reversar_pago_tarjeta(payment, "Corrección")
        purchase = db.get_compras_tarjeta(tarjeta_id=card)[0]
        self.assertEqual(purchase["valor_pendiente"], 600_000)
        self.assertEqual(db.get_tarjetas()[0]["saldo_deuda"], 600_000)
        self.assertEqual(calc.deuda_pendiente_por_responsabilidad(card),
                         {"persona1": 250_000, "persona2": 350_000})

    def test_transaction_uuid_blocks_duplicate(self):
        tx = "11111111-1111-1111-1111-111111111111"
        first = db.registrar_ingreso("2026-09", "persona1", "Salario", 1_000_000, tx)
        with self.assertRaises(DuplicateOperationError):
            db.registrar_ingreso("2026-09", "persona1", "Salario", 1_000_000, tx)
        self.assertEqual(len(db.get_ingresos("2026-09")), 1)
        self.assertEqual(db.get_ingresos("2026-09")[0]["id"], first)

    def test_shared_distribution_must_sum_to_total(self):
        with self.assertRaises(IntegrityError):
            db.registrar_gasto("2026-09", "Compra", "Hogar", 100_000, "2026-09-02",
                               "debito", "persona1", "compartido", 40_000, 40_000)


if __name__ == "__main__":
    unittest.main()
