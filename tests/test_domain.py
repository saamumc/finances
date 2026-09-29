"""Pruebas de regresión del dominio; se ejecutan con: python -m unittest."""

import tempfile
import unittest
from pathlib import Path

from lumina.core import database as db
from lumina.core import calculations as calc
from lumina.advisor import service as financial_advisor
from lumina.core import engine as financial_engine
from lumina.ui.service import FinanceService
from lumina.constants import InsufficientFundsError, ValidationError


class FinanceDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.tempdir.name) / "finances.db"
        db.init_db()

    def tearDown(self) -> None:
        db.DB_PATH = self.original_path
        self.tempdir.cleanup()

    def test_cajita_con_propietario_y_retiro_protegido(self) -> None:
        caja = db.crear_ahorro("Universidad", "persona2", "Matrícula de Sara")
        db.registrar_movimiento_ahorro("2026-09", "2026-09-02", caja, "DEPOSITO", 200_000, "Aporte")
        db.registrar_movimiento_ahorro("2026-09", "2026-09-03", caja, "RETIRO", 50_000, "Pago parcial")
        fondo = db.get_ahorros()[0]
        self.assertEqual((fondo["propietario"], fondo["saldo"]), ("persona2", 150_000))
        with self.assertRaises(InsufficientFundsError):
            db.registrar_movimiento_ahorro("2026-09", "2026-09-04", caja, "RETIRO", 150_001, "Exceso")

    def test_ingreso_cero_y_fechas_invalidas_se_rechazan(self) -> None:
        with self.assertRaises(ValidationError):
            db.registrar_ingreso("2026-09", "persona1", "Salario", 0)
        with self.assertRaises(ValidationError):
            db.registrar_gasto("2026-13", "Mercado", "Hogar", 10, "2026-13-01", "efectivo", "persona1", "persona1", 10, 0)

    def test_tercero_se_registra_independiente_con_tipo(self) -> None:
        tercero = db.crear_tercero("Banco Ejemplo", "BANCO", "Línea 123")
        registrado = db.get_terceros()[0]
        self.assertEqual((tercero, registrado["nombre"], registrado["tipo"], registrado["contacto"]),
                         (1, "Banco Ejemplo", "BANCO", "Línea 123"))
        self.assertEqual(db.get_gastos(), [])

    def test_migracion_agrega_tipo_a_terceros_de_base_existente(self) -> None:
        with db.get_conn() as conn:
            conn.execute("DROP TABLE terceros")
            conn.execute("CREATE TABLE terceros (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE, contacto TEXT, creado_en TEXT NOT NULL)")
            conn.execute("INSERT INTO terceros (nombre, contacto, creado_en) VALUES ('Ana', NULL, '2026-09-01')")
            conn.execute("PRAGMA user_version = 9")
        db.init_db()
        tercero = db.get_terceros()[0]
        self.assertEqual((tercero["nombre"], tercero["tipo"]), ("Ana", "PERSONA"))

    def test_reversa_de_ingreso_conserva_motivo(self) -> None:
        ingreso = db.registrar_ingreso("2026-09", "persona1", "Salario", 1_000)
        db.reversar_ingreso(ingreso, "Registro duplicado")
        with db.get_conn() as conn:
            fila = conn.execute("SELECT estado, motivo_reversion, fecha_reversion FROM ingresos WHERE id=?", (ingreso,)).fetchone()
        self.assertEqual(fila["estado"], "REVERSADO")
        self.assertEqual(fila["motivo_reversion"], "Registro duplicado")
        self.assertTrue(fila["fecha_reversion"])

    def test_presupuesto_acumula_solo_gastos_activos(self) -> None:
        categoria = db.crear_categoria("Mercado")
        db.guardar_presupuesto("2026-09", categoria, 300_000)
        db.registrar_gasto("2026-09", "Compra", "Mercado", 120_000, "2026-09-02", "efectivo", "persona1", "persona1", 120_000, 0)
        presupuesto = db.get_presupuestos("2026-09")[0]
        self.assertEqual((presupuesto["monto"], presupuesto["gastado"]), (300_000, 120_000))

    def test_cajita_aportes_meta_y_reversa(self) -> None:
        caja = db.crear_ahorro("Ginebra", "persona1", "Comida", 600_000, titular="compartido", icono="🐶")
        primero = db.registrar_movimiento_ahorro("2026-09", "2026-09-02", caja, "DEPOSITO", 150_000, "Samuel", aportante="persona1")
        db.registrar_movimiento_ahorro("2026-09", "2026-09-03", caja, "DEPOSITO", 100_000, "Sara", aportante="persona2")
        db.registrar_movimiento_ahorro("2026-09", "2026-09-04", caja, "RETIRO", 50_000, "Comida", aportante="persona1")
        fondo = db.get_ahorros()[0]
        self.assertEqual((fondo["saldo"], fondo["aporte_p1"], fondo["aporte_p2"], fondo["total_retirado"]), (200_000, 150_000, 100_000, 50_000))
        meta = db.crear_meta("Universidad", 600_000, ahorro_id=caja, fecha_objetivo="2026-12-31")
        progreso = next(m for m in calc.progreso_metas() if m["id"] == meta)
        self.assertAlmostEqual(progreso["progreso"], 1 / 3)
        db.reversar_movimiento_ahorro(primero, "Corrección")
        self.assertEqual(db.get_ahorros()[0]["saldo"], 50_000)

    def test_asesor_no_inventa_datos(self) -> None:
        resultado = financial_advisor.analizar_finanzas("2026-09")
        self.assertTrue(any(r["tipo"] == "datos" for r in resultado["recomendaciones"]))

    def test_decision_de_gasto_es_simulada_y_conservadora(self) -> None:
        db.registrar_ingreso("2026-09", "persona1", "Ingreso", 1_000_000)
        db.registrar_gasto("2026-09", "Arriendo", "Hogar", 300_000, "2026-09-01", "efectivo", "persona1", "persona1", 300_000, 0)
        antes = len(db.get_gastos("2026-09"))
        resultado = financial_advisor.evaluar_gasto("2026-09", 100_000, metodo="debito")
        self.assertEqual(resultado["estado"], "si")
        self.assertEqual(len(db.get_gastos("2026-09")), antes)
        no = financial_advisor.evaluar_gasto("2026-09", 900_000, metodo="debito")
        self.assertEqual(no["estado"], "no")

    def test_compra_simulada_no_supera_cupo(self) -> None:
        tarjeta = db.registrar_tarjeta("Principal", "persona1", 100_000)
        resultado = financial_advisor.evaluar_gasto("2026-09", 120_000, metodo="tarjeta", tarjeta_id=tarjeta)
        self.assertEqual(resultado["estado"], "no")

    def test_tarjeta_separa_deuda_liquidez_responsabilidad_y_reversiones(self) -> None:
        """Una compra a crédito no es salida de caja; el pago sí lo es."""
        db.registrar_ingreso("2026-09", "persona1", "Salario", 1_000_000)
        tarjeta = db.registrar_tarjeta("Principal", "persona1", 1_000_000)
        gasto = db.registrar_gasto(
            "2026-09", "Mercado", "Hogar", 500_000, "2026-09-02", "tarjeta",
            "persona1", "compartido", 300_000, 200_000, tarjeta,
        )
        self.assertEqual(calc.deuda_total_tarjetas(), 500_000)
        # La compra entra a la liquidez económica desde el momento en que se
        # asume, aunque el pago real de la tarjeta ocurra después.
        self.assertEqual(calc.liquidez_total("2026-09"), 500_000)
        self.assertEqual(calc.deuda_pendiente_por_responsabilidad(tarjeta), {"persona1": 300_000, "persona2": 200_000})

        pago = db.registrar_pago_tarjeta(
            "2026-09", "2026-09-03", tarjeta, 400_000, "persona1", 250_000, 150_000,
        )
        self.assertEqual(calc.deuda_total_tarjetas(), 100_000)
        self.assertEqual(calc.liquidez_total("2026-09"), 500_000)
        self.assertEqual(calc.deuda_pendiente_por_responsabilidad(tarjeta), {"persona1": 60_000, "persona2": 40_000})

        db.reversar_pago_tarjeta(pago, "Pago registrado por error")
        self.assertEqual(calc.deuda_total_tarjetas(), 500_000)
        self.assertEqual(calc.liquidez_total("2026-09"), 500_000)
        self.assertEqual(calc.deuda_pendiente_por_responsabilidad(tarjeta), {"persona1": 300_000, "persona2": 200_000})

        interes = db.registrar_movimiento_tarjeta(
            "2026-09", "2026-09-04", tarjeta, "INTERES", 22_000, "Interés mensual",
            "compartido", 13_200, 8_800,
        )
        self.assertEqual(calc.deuda_total_tarjetas(), 522_000)
        db.reversar_movimiento_tarjeta(interes, "Cobro corregido")
        db.reversar_gasto(gasto, "Compra anulada")
        self.assertEqual(calc.deuda_total_tarjetas(), 0)

    def test_no_reasigna_deuda_historica_al_cambiar_titular(self) -> None:
        tarjeta = db.registrar_tarjeta(
            "Histórica", "persona1", 1_000_000, saldo_inicial_historico=300_000,
        )
        with self.assertRaises(ValidationError):
            db.actualizar_tarjeta(
                tarjeta, nombre="Histórica", propietario="persona2", cupo_total=1_000_000,
                pago_minimo=0, interes_mensual=0, activa=True,
            )

    def test_abono_de_deuda_historica_conserva_libro_de_pareja(self) -> None:
        tarjeta = db.registrar_tarjeta("Histórica", "persona1", 2_000, saldo_inicial_historico=1_000)
        db.registrar_pago_tarjeta("2026-09", "2026-09-03", tarjeta, 400, "persona1", 400, 0)
        balance = calc.balance_historico_pareja()
        self.assertTrue(balance["cuadra"])
        self.assertEqual((balance["balance_neto_persona1"], balance["balance_neto_persona2"]), (0, 0))

    def test_liquidacion_explicita_nombra_el_pago_y_la_compra(self) -> None:
        tarjeta = db.registrar_tarjeta("Tarjeta de Sara", "persona2", 1_000_000)
        db.registrar_gasto(
            "2026-09", "Tenis", "Ropa", 100_000, "2026-09-02", "tarjeta",
            "persona2", "persona2", 0, 100_000, tarjeta,
        )
        db.registrar_pago_tarjeta(
            "2026-09", "2026-09-03", tarjeta, 100_000, "persona1", 100_000, 0,
        )
        samuel = calc.explicar_balance("persona1")["lineas"]
        sara = calc.explicar_balance("persona2")["lineas"]
        self.assertTrue(any("Pago de «Tarjeta de Sara»: Samuel aportó $100.000." == linea["detalle"] for linea in samuel))
        self.assertTrue(any("Compra «Tenis» en «Tarjeta de Sara»: a Sara le corresponde $100.000." == linea["detalle"] for linea in sara))

    def test_liquidacion_historica_conserva_una_compra_y_pago_de_meses_distintos(self) -> None:
        tarjeta = db.registrar_tarjeta("Tarjeta de Sara", "persona2", 1_000_000)
        db.registrar_gasto(
            "2026-08", "Tenis", "Ropa", 100_000, "2026-08-28", "tarjeta",
            "persona2", "persona2", 0, 100_000, tarjeta,
        )
        db.registrar_pago_tarjeta(
            "2026-09", "2026-09-03", tarjeta, 100_000, "persona1", 100_000, 0,
        )
        balance = calc.balance_historico_pareja()
        self.assertEqual(balance["balance_neto_persona1"], 100_000)
        self.assertEqual(balance["balance_neto_persona2"], -100_000)

    def test_liquidacion_registrada_desde_servicio_equilibra_sin_ingreso_ni_gasto(self) -> None:
        tarjeta = db.registrar_tarjeta("Tarjeta de Sara", "persona2", 1_000_000)
        db.registrar_gasto("2026-09", "Tenis", "Ropa", 100_000, "2026-09-02", "tarjeta",
                           "persona2", "persona2", 0, 100_000, tarjeta)
        db.registrar_pago_tarjeta("2026-09", "2026-09-03", tarjeta, 100_000, "persona1", 100_000, 0)
        FinanceService().crear_liquidacion({"mes": "2026-09", "fecha": "2026-09-03", "deudor": "persona2",
                                            "acreedor": "persona1", "monto": "100000", "concepto": "Pago de balance"})
        balance = calc.balance_historico_pareja()
        self.assertEqual((balance["balance_neto_persona1"], balance["balance_neto_persona2"]), (0, 0))
        self.assertEqual((len(db.get_ingresos()), len(db.get_gastos()), len(db.get_liquidaciones())), (0, 1, 1))

    def test_dashboard_personal_reparte_compartidos_sin_duplicar_y_asesor_es_puro(self) -> None:
        """Escenario mínimo de Samuel/Sara: 50/50 y 70/30 siguen siendo una sola compra."""
        db.registrar_ingreso("2026-09", "persona1", "Salario Samuel", 5_000_000)
        db.registrar_ingreso("2026-09", "persona2", "Salario Sara", 4_000_000)
        tarjeta = db.registrar_tarjeta("Samuel", "persona1", 2_000_000, pago_minimo=100_000, interes_mensual=2)
        db.registrar_gasto("2026-09", "Mercado", "Hogar", 200_000, "2026-09-02", "efectivo", "persona1", "compartido", 100_000, 100_000)
        db.registrar_gasto("2026-09", "Viaje", "Ocio", 200_000, "2026-09-03", "tarjeta", "persona1", "compartido", 140_000, 60_000, tarjeta)
        caja_samuel = db.crear_ahorro("Emergencia Samuel", "persona1", "", 6_000_000, titular="persona1")
        caja_sara = db.crear_ahorro("Sara ahorro", "persona2", "", 2_000_000, titular="persona2")
        db.registrar_movimiento_ahorro("2026-09", "2026-09-03", caja_samuel, "DEPOSITO", 2_400_000, "Reserva", aportante="persona1")
        db.registrar_movimiento_ahorro("2026-09", "2026-09-03", caja_sara, "DEPOSITO", 800_000, "Reserva", aportante="persona2")
        db.crear_meta("Viaje Samuel", 3_000_000, ahorro_id=caja_samuel)
        samuel, sara = calc.dashboard_personal("persona1", "2026-09"), calc.dashboard_personal("persona2", "2026-09")
        self.assertEqual((samuel["gastos"], sara["gastos"]), (240_000, 160_000))
        self.assertEqual(samuel["gastos"] + sara["gastos"], 400_000)
        self.assertEqual((samuel["ahorrado"], sara["ahorrado"]), (2_400_000, 800_000))
        antes = db.get_tarjetas()[0]["saldo_deuda"]
        asesor = financial_advisor.analizar_finanzas("2026-09")
        escenario = financial_advisor.simular_escenario("2026-09", "compra_tarjeta", 100_000, tarjeta_id=tarjeta)
        proyeccion = financial_advisor.proyectar_deuda(tarjeta, 50_000)
        self.assertIn("score_salud", asesor); self.assertTrue(asesor["plan_financiero"])
        self.assertFalse(escenario["modifica_base"]); self.assertEqual(db.get_tarjetas()[0]["saldo_deuda"], antes)
        self.assertEqual(proyeccion["saldo"], antes)

    def test_gasto_con_tarjeta_responsabilidad_congelada_despues_de_pago(self) -> None:
        """Los metadatos cambian; importe y distribución no se reasignan tras un pago."""
        tarjeta = db.registrar_tarjeta("Samuel", "persona1", 2_000_000)
        gasto = db.registrar_gasto("2026-09", "Viaje", "Ocio", 800_000, "2026-09-02", "tarjeta",
                                   "persona1", "compartido", 200_000, 600_000, tarjeta)
        db.registrar_pago_tarjeta("2026-09", "2026-09-03", tarjeta, 300_000, "persona1", 300_000, 0)
        db.actualizar_gasto(gasto, "2026-09", "Viaje corregido", "Vacaciones", 800_000,
                            "2026-09-04", "persona2", "compartido", 200_000, 600_000)
        compra = db.get_compras_tarjeta(tarjeta)[0]
        actualizado = db.get_gasto(gasto)
        self.assertEqual((compra["valor_original"], compra["valor_pendiente"], db.get_tarjetas()[0]["saldo_deuda"]),
                         (800_000, 500_000, 500_000))
        self.assertEqual((actualizado["nombre"], actualizado["categoria"], actualizado["pagador"],
                          actualizado["monto_p1"], actualizado["monto_p2"]),
                         ("Viaje corregido", "Vacaciones", "persona2", 200_000, 600_000))
        with self.assertRaises(ValidationError):
            db.actualizar_gasto(gasto, "2026-09", "Inválido", "Vacaciones", 900_000,
                                "2026-09-04", "persona2", "compartido", 270_000, 630_000)

    def test_liquidez_usa_responsabilidad_y_separa_la_caja_real(self) -> None:
        """Caso obligatorio: Samuel paga pero solo asume el 25% de la compra."""
        db.registrar_ingreso("2026-09", "persona1", "Disponible Samuel", 2_000_000)
        tarjeta = db.registrar_tarjeta("Samuel", "persona1", 1_000_000)
        db.registrar_gasto("2026-09", "Compra compartida", "Hogar", 800_000, "2026-09-02", "tarjeta",
                           "persona1", "compartido", 200_000, 600_000, tarjeta)
        liquidez = calc.liquidez_por_persona("2026-09")
        caja = calc.caja_real_por_persona("2026-09")
        self.assertEqual((liquidez["persona1"]["liquidez"], liquidez["persona2"]["liquidez"]),
                         (1_800_000, -600_000))
        self.assertEqual(caja["persona1"]["caja"], 2_000_000)  # Aún no se ha pagado la tarjeta.

    def test_pago_en_tarjeta_de_samuel_registra_el_aporte_real_de_sara(self) -> None:
        """La tarjeta puede ser de Samuel sin atribuirle el pago de Sara."""
        tarjeta = db.registrar_tarjeta("Tarjeta Samuel", "persona1", 1_000_000)
        db.registrar_gasto("2026-09", "Compra compartida", "Hogar", 400_000, "2026-09-02", "tarjeta",
                           "persona1", "compartido", 200_000, 200_000, tarjeta)
        db.registrar_pago_tarjeta("2026-09", "2026-09-03", tarjeta, 200_000, "persona2", 0, 200_000)
        resumen = calc.resumen_tarjeta(db.get_tarjetas()[0])
        balance = calc.balance_historico_pareja()
        self.assertEqual((resumen["pagado_persona1"], resumen["pagado_persona2"]), (0, 200_000))
        self.assertEqual((resumen["deuda_persona1"], resumen["deuda_persona2"]), (100_000, 100_000))
        self.assertEqual((balance["balance_neto_persona1"], balance["balance_neto_persona2"]),
                         (-100_000, 100_000))

    def test_motor_financiero_expone_estado_diagnostico_auditoria_y_escenario_completo(self) -> None:
        db.registrar_ingreso("2026-09", "persona1", "Salario", 1_000_000)
        tarjeta = db.registrar_tarjeta("Riesgo", "persona1", 1_000_000, pago_minimo=20_000, interes_mensual=3)
        db.registrar_gasto("2026-09", "Compra", "Ocio", 950_000, "2026-09-02", "tarjeta", "persona1", "persona1", 950_000, 0, tarjeta)
        estado = financial_engine.financial_state("2026-09")
        diagnostico = financial_engine.diagnose(estado)
        auditoria = financial_engine.audit_integrity("2026-09")
        escenario = financial_advisor.simular_escenario("2026-09", "perdida_ingresos", 300_000)
        self.assertEqual(estado["debt"]["total"], 950_000)
        self.assertTrue(any(x["code"] == "card_critical" for x in diagnostico))
        self.assertIn("ok", auditoria)
        self.assertFalse(escenario["modifica_base"])
        self.assertEqual(escenario["liquidez_antes"], escenario["liquidez_despues"] + 300_000)

    def test_servicio_convierte_nombre_de_interfaz_al_registrar_ingreso(self) -> None:
        service = FinanceService()
        service.crear_ingreso("2026-09", "Samuel", "Sueldo", "3802114")
        ingreso = db.get_ingresos("2026-09")[0]
        self.assertEqual((ingreso["persona"], ingreso["valor"]), ("persona1", 3_802_114))

    def test_estado_actual_se_reconstruye_despues_de_ingreso_gasto_tarjeta_y_ahorro(self) -> None:
        """Regresión E2E: ninguna vista depende de datos previos en memoria."""
        mes = "2026-09"
        service = FinanceService()
        service.crear_ingreso(mes, "Samuel", "Salario", "4000000")
        service.crear_gasto({"mes": mes, "nombre": "Arriendo", "categoria": "Hogar", "valor": "2500000",
                              "fecha": "2026-09-02", "metodo": "Efectivo", "pagador": "Samuel",
                              "responsabilidad": "Samuel", "monto_p1": "2500000", "monto_p2": "0",
                              "tarjeta_id": "", "cuotas": "1", "prioridad": "Obligatorio"})
        inicial = service.estado_actual(mes)
        self.assertEqual((inicial["flow"]["ingresos"], inicial["liquidity"]["available"], inicial["wealth"]["disponible_gastos_recurrentes"]),
                         (4_000_000, 1_500_000, 1_500_000))
        balance_antes = inicial["couple_historical"]["balance_neto_persona1"]
        score_antes = service.asesor(mes)["score_salud"]["score"]

        service.crear_ingreso(mes, "Samuel", "Ingreso adicional", "1000000")
        tras_ingreso = service.estado_actual(mes)
        tablero = service.dashboard(mes)
        asesor = service.asesor(mes)
        self.assertEqual((tras_ingreso["flow"]["ingresos"], tras_ingreso["liquidity"]["available"],
                          tablero["flujo"]["ingresos"], asesor["flujo"]["ingresos"]),
                         (5_000_000, 2_500_000, 5_000_000, 5_000_000))
        self.assertIn("fondos", tablero["cajitas"])
        self.assertEqual(service.dashboard_personal("persona1", mes)["ingresos"], 5_000_000)
        self.assertGreater(asesor["score_salud"]["score"], score_antes)
        # Un ingreso personal cambia liquidez y asesor; no inventa una deuda
        # entre Samuel y Sara si no hubo un gasto, pago o liquidación compartida.
        self.assertEqual(tras_ingreso["couple_historical"]["balance_neto_persona1"], balance_antes)

        service.crear_gasto({"mes": mes, "nombre": "Mercado", "categoria": "Hogar", "valor": "300000",
                              "fecha": "2026-09-03", "metodo": "Débito", "pagador": "Samuel",
                              "responsabilidad": "Samuel", "monto_p1": "300000", "monto_p2": "0",
                              "tarjeta_id": "", "cuotas": "1", "prioridad": "Obligatorio"})
        tarjeta = service.crear_tarjeta("Principal", "Samuel", "1000000", "0", "0", "0", None)
        service.crear_gasto({"mes": mes, "nombre": "Compra", "categoria": "Hogar", "valor": "500000",
                              "fecha": "2026-09-03", "metodo": "Tarjeta", "pagador": "Samuel",
                              "responsabilidad": "Samuel", "monto_p1": "500000", "monto_p2": "0",
                              "tarjeta_id": str(tarjeta), "cuotas": "1", "prioridad": "Obligatorio"})
        caja = db.crear_ahorro("Emergencia", "persona1", "", 1_000_000)
        db.registrar_movimiento_ahorro(mes, "2026-09-03", caja, "DEPOSITO", 200_000, "Aporte")
        final = FinanceService().estado_actual(mes)  # Simula cerrar y abrir la aplicación.
        asesor_final = FinanceService().asesor(mes)
        self.assertEqual((final["liquidity"]["available"], final["wealth"]["disponible_gastos_recurrentes"],
                          final["debt"]["total"], final["savings"]["total"]),
                         (1_700_000, 1_500_000, 500_000, 200_000))
        self.assertEqual((asesor_final["liquidez"], asesor_final["estado_financiero"]["debt"]["total"]),
                         (1_700_000, 500_000))

    def test_criterio_aceptacion_samuel_compra_y_sara_paga(self) -> None:
        """El pago bancario de Sara no borra la responsabilidad de Samuel."""
        tarjeta = db.registrar_tarjeta("Samuel", "persona1", 1_500_000)
        db.registrar_gasto("2026-09", "Compra compartida", "Hogar", 1_000_000, "2026-09-02", "tarjeta",
                           "persona1", "compartido", 500_000, 500_000, tarjeta)
        db.registrar_pago_tarjeta("2026-09", "2026-09-03", tarjeta, 1_000_000, "persona2", 0, 1_000_000)
        balance = calc.balance_historico_pareja()
        self.assertEqual(db.get_tarjetas()[0]["saldo_deuda"], 0)
        self.assertEqual((balance["detalle"]["persona1"]["consumido"], balance["detalle"]["persona2"]["consumido"]),
                         (500_000, 500_000))
        self.assertEqual((balance["balance_neto_persona1"], balance["balance_neto_persona2"]), (-500_000, 500_000))

    def test_criterio_aceptacion_gasto_mixto_y_pago_de_sara(self) -> None:
        tarjeta = db.registrar_tarjeta("Sara", "persona2", 1_500_000)
        db.registrar_gasto("2026-09", "Compra mixta", "Hogar", 1_000_000, "2026-09-02", "tarjeta",
                           "persona2", "compartido", 400_000, 600_000, tarjeta)
        db.registrar_pago_tarjeta("2026-09", "2026-09-03", tarjeta, 1_000_000, "persona2", 0, 1_000_000)
        balance = calc.balance_historico_pareja()
        self.assertEqual((balance["detalle"]["persona1"]["consumido"], balance["detalle"]["persona2"]["consumido"]),
                         (400_000, 600_000))
        self.assertEqual((balance["balance_neto_persona1"], balance["balance_neto_persona2"]), (-400_000, 400_000))

    def test_reversa_de_compra_de_tarjeta_crea_asiento_inverso_y_revisa_impacto(self) -> None:
        tarjeta = db.registrar_tarjeta("Principal", "persona1", 1_000_000, interes_mensual=2)
        gasto = db.registrar_gasto("2026-09", "Mercado", "Hogar", 800_000, "2026-09-02", "tarjeta",
                                   "persona1", "compartido", 300_000, 500_000, tarjeta)
        vista = db.revisar_transaccion(tarjeta, gasto, "gasto")
        self.assertTrue(vista["se_puede_anular"])
        self.assertEqual((vista["situacion_actual"]["saldo_tarjeta"], vista["despues_de_anular"]["saldo_tarjeta"]),
                         (800_000, 0))
        self.assertEqual((vista["despues_de_anular"]["responsabilidad_samuel"], vista["despues_de_anular"]["responsabilidad_sara"]),
                         (0, 0))
        db.reversar_gasto(gasto, "Compra cancelada")
        reversa = db.get_reversiones_tarjeta(tarjeta)[0]
        self.assertEqual((reversa["tipo_original"], reversa["monto_inverso"], reversa["original_id"]), ("COMPRA", -800_000, 1))
        self.assertEqual((db.get_tarjetas()[0]["saldo_deuda"], calc.deuda_pendiente_por_responsabilidad(tarjeta)["persona1"]), (0, 0))
        with self.assertRaises(ValidationError):
            db.reversar_gasto(gasto, "Intento duplicado")

    def test_reversa_de_pago_crea_operacion_opuesta_y_respeta_cupo(self) -> None:
        tarjeta = db.registrar_tarjeta("Principal", "persona1", 1_000_000)
        db.registrar_gasto("2026-09", "Compra", "Hogar", 1_000_000, "2026-09-02", "tarjeta",
                           "persona1", "compartido", 500_000, 500_000, tarjeta)
        pago = db.registrar_pago_tarjeta("2026-09", "2026-09-03", tarjeta, 1_000_000, "persona2", 0, 1_000_000)
        vista = db.revisar_transaccion(tarjeta, pago, "pago")
        self.assertTrue(vista["se_puede_anular"])
        self.assertEqual(vista["despues_de_anular"]["saldo_tarjeta"], 1_000_000)
        db.reversar_pago_tarjeta(pago, "Transferencia rechazada")
        reversa = db.get_reversiones_tarjeta(tarjeta)[0]
        self.assertEqual((reversa["tipo_original"], reversa["monto_inverso"]), ("PAGO", 1_000_000))
        # Al volver a ocupar el cupo, un pago anterior no puede deshacerse si
        # eso eleva la deuda sobre el límite de la tarjeta.
        pago_nuevo = db.registrar_pago_tarjeta("2026-09", "2026-09-04", tarjeta, 1_000_000, "persona1", 1_000_000, 0)
        db.registrar_gasto("2026-09", "Compra posterior", "Hogar", 1_000_000, "2026-09-05", "tarjeta",
                           "persona1", "persona1", 1_000_000, 0, tarjeta)
        with self.assertRaises(InsufficientFundsError):
            db.reversar_pago_tarjeta(pago_nuevo, "No debe superar cupo")

    def test_reconciliacion_de_sara_separa_caja_real_de_responsabilidad(self) -> None:
        """Caso reportado: la diferencia no es un movimiento perdido ni un saldo inventado."""
        db.registrar_ingreso("2026-09", "persona2", "Sueldo", 3_847_114)
        db.registrar_gasto("2026-09", "Gasto personal", "Hogar", 607_100, "2026-09-02", "debito",
                           "persona2", "persona2", 0, 607_100)
        db.registrar_gasto("2026-09", "Compra compartida", "Hogar", 346_400, "2026-09-02", "debito",
                           "persona2", "compartido", 173_200, 173_200)
        tarjeta = db.registrar_tarjeta("Tarjeta Samuel", "persona1", 1_500_000, saldo_inicial_historico=1_138_316)
        db.registrar_pago_tarjeta("2026-09", "2026-09-02", tarjeta, 1_138_316, "persona2", 0, 1_138_316)
        conciliacion = calc.reconciliar_saldo_personal("persona2", "2026-09")
        self.assertEqual((conciliacion["saldo_mostrado_anterior"], conciliacion["saldo_reconstruido"], conciliacion["diferencia"]),
                         (3_066_814, 1_755_298, 1_311_516))
        self.assertEqual(conciliacion["estado"], "explicado")
        self.assertEqual({causa["tipo"] for causa in conciliacion["causas"]}, {"pago_tarjeta", "adelanto_compartido"})

    def test_ingresos_historicos_respeta_ventana_cronologia_y_faltantes(self) -> None:
        """El histórico no rellena meses ausentes ni ordena montos para inferir tendencia."""
        for mes, monto in (("2026-06", 4_000_000), ("2026-07", 4_200_000), ("2026-08", 4_400_000)):
            db.registrar_ingreso(mes, "persona1", "Salario", monto)
        historial = financial_engine.ingresos_historicos("persona1", meses=4, as_of_month="2026-09")
        self.assertEqual([dato["mes"] for dato in historial["datos_mes_a_mes"]], ["2026-06", "2026-07", "2026-08"])
        self.assertEqual((historial["meses_solicitados"], historial["meses_con_datos"], historial["datos_incompletos"]),
                         (4, 3, ["2026-09"]))
        self.assertEqual((historial["tendencia"], historial["dato_faltante_mes_actual"], historial["siguiente_ingreso_estimado"]),
                         ("creciente", True, 4_200_000))
        self.assertIsNone(historial["fecha_estimada_proximo_ingreso"])

    def test_preview_de_reversa_no_escribe_y_confirmacion_crea_asiento_auditable(self) -> None:
        ingreso = db.registrar_ingreso("2026-09", "persona1", "Salario", 1_000_000)
        before = (db.get_ingresos("2026-09", incluir_reversados=True), db.get_reversiones_movimientos())
        preview = db.proponer_anular_movimiento("ingreso", ingreso)
        after_preview = (db.get_ingresos("2026-09", incluir_reversados=True), db.get_reversiones_movimientos())
        self.assertEqual(before, after_preview)
        self.assertTrue(preview["se_puede_anular"])
        self.assertEqual((preview["situacion_actual"]["ingresos"], preview["despues_de_anular"]["ingresos"]),
                         (1_000_000, 0))
        db.reversar_ingreso(ingreso, "Duplicado")
        reversa = db.get_reversiones_movimientos("ingreso", ingreso)[0]
        self.assertEqual((reversa["original_id"], reversa["monto_inverso"], reversa["motivo"]),
                         (ingreso, -1_000_000, "Duplicado"))

    def test_simular_compra_efectivo_separa_pagador_responsabilidad_y_no_muta_sqlite(self) -> None:
        db.registrar_ingreso("2026-09", "persona2", "Disponible Sara", 1_000_000)
        before = financial_engine.snapshot_database()
        scenario = financial_engine.simular_compra(300_000, "2026-09", "Compra de Samuel",
                                                    quien_paga="persona2", responsabilidad="persona1")
        after = financial_engine.snapshot_database()
        self.assertEqual(before, after)
        self.assertTrue(scenario["integridad_sqlite"])
        self.assertEqual((scenario["estado_actual"]["caja_disponible"], scenario["estado_despues"]["caja_disponible"]),
                         (1_000_000, 700_000))
        pareja = scenario["impacto_pareja"]
        self.assertEqual((pareja["responsabilidad_samuel"], pareja["responsabilidad_sara"]), (300_000, 0))
        self.assertEqual((pareja["saldo_samuel_despues"], pareja["saldo_sara_despues"]), (-300_000, 300_000))

    def test_simular_compra_tarjeta_calcula_deuda_cupo_y_conserva_base(self) -> None:
        db.registrar_ingreso("2026-09", "persona1", "Disponible", 1_000_000)
        tarjeta = db.registrar_tarjeta("Principal", "persona1", 1_000_000, pago_minimo=50_000, interes_mensual=2)
        db.registrar_gasto("2026-09", "Compra previa", "Hogar", 600_000, "2026-09-02", "tarjeta",
                           "persona1", "persona1", 600_000, 0, tarjeta)
        before = financial_engine.snapshot_database()
        scenario = financial_engine.simular_compra(300_000, "2026-09", "Tenis", quien_paga="persona1",
                                                    responsabilidad="persona1", usar_tarjeta=tarjeta)
        self.assertEqual(before, financial_engine.snapshot_database())
        impact = scenario["impacto_tarjeta"]
        self.assertEqual((impact["deuda_antes"], impact["deuda_despues"], impact["cupo_despues"]), (600_000, 900_000, 100_000))
        self.assertEqual((scenario["estado_actual"]["caja_disponible"], scenario["estado_despues"]["caja_disponible"]),
                         (1_000_000, 1_000_000))
        self.assertEqual(db.get_tarjetas()[0]["saldo_deuda"], 600_000)

    def test_respuesta_compra_inteligente_es_explica_y_no_registra_movimiento(self) -> None:
        db.registrar_ingreso("2026-09", "persona1", "Disponible", 500_000)
        before = financial_engine.snapshot_database()
        answer = financial_advisor.responder_compra_inteligente(100_000, "Cena", "normal", mes="2026-09",
                                                                 persona_pregunta="persona1", responsabilidad="persona1")
        self.assertEqual(before, financial_engine.snapshot_database())
        self.assertIn("respuesta_rapida", answer)
        self.assertIn("cambio_recomendacion", answer)
        self.assertFalse(answer["scenario_completo"]["modifica_base"])

    def test_priorizacion_tarjetas_difiere_y_concentra_sin_escribir(self) -> None:
        diferida = db.registrar_tarjeta("Diferida", "persona1", 1_000_000, pago_minimo=100_000, interes_mensual=2)
        principal = db.registrar_tarjeta("Principal", "persona1", 1_000_000, pago_minimo=200_000, interes_mensual=3)
        secundaria = db.registrar_tarjeta("Secundaria", "persona2", 1_000_000, pago_minimo=50_000, interes_mensual=1)
        for tarjeta, monto in ((diferida, 500_000), (principal, 700_000), (secundaria, 300_000)):
            db.registrar_gasto("2026-09", "Compra", "Hogar", monto, "2026-09-02", "tarjeta",
                               "persona1", "persona1", monto, 0, tarjeta)
        before = financial_engine.snapshot_database()
        resultado = financial_engine.simular_priorizacion_tarjetas("2026-09", 600_000, [diferida], [principal, secundaria])
        self.assertEqual(before, financial_engine.snapshot_database())
        self.assertTrue(resultado["posible"])
        self.assertEqual((resultado["minimos_necesarios"], resultado["presupuesto_usado"], resultado["presupuesto_sobrante"]),
                         (250_000, 600_000, 0))
        self.assertEqual(resultado["tarjetas_diferidas"][0]["interes_generado_este_mes"], 10_000)
        pagos = {pago["tarjeta_id"]: pago for pago in resultado["tarjetas_pagadas"]}
        self.assertEqual((pagos[principal]["pago_total"], pagos[principal]["saldo_despues"]), (550_000, 150_000))
        self.assertEqual((pagos[secundaria]["pago_total"], pagos[secundaria]["saldo_despues"]), (50_000, 250_000))

    def test_priorizacion_tarjetas_inviable_y_pregunta_detectada(self) -> None:
        diferida = db.registrar_tarjeta("Diferida", "persona1", 1_000_000, pago_minimo=100_000, interes_mensual=2)
        principal = db.registrar_tarjeta("Principal", "persona1", 1_000_000, pago_minimo=200_000, interes_mensual=3)
        for tarjeta in (diferida, principal):
            db.registrar_gasto("2026-09", "Compra", "Hogar", 300_000, "2026-09-02", "tarjeta",
                               "persona1", "persona1", 300_000, 0, tarjeta)
        resultado = financial_engine.simular_priorizacion_tarjetas("2026-09", 150_000, [diferida], [principal])
        self.assertFalse(resultado["posible"])
        self.assertEqual(resultado["deficit_minimos"], 50_000)
        self.assertTrue(any("Faltan" in consecuencia for consecuencia in resultado["consecuencias"]))
        respuesta = financial_advisor.responder_pregunta("2026-09", "¿podemos diferir alguna tarjeta y pagar algunas en específico?")
        self.assertIn("qué tarjeta(s) quieres diferir", respuesta["respuesta"])
        self.assertEqual(respuesta["datos"]["requiere"], ["diferir", "priorizar", "presupuesto_disponible"])


if __name__ == "__main__":
    unittest.main()
