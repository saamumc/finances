"""PR0-b: caracterización de ``intelligence.responder`` (congela el comportamiento actual).

Objetivo: poder limpiar ``intelligence.py`` (código muerto, ``_RUTAS`` duplicada) con la
garantía de que ninguna respuesta cambia. NO valida que las respuestas sean *correctas*;
valida que son *idénticas* a las capturadas en ``fixtures/responder_caracterizacion.json``.

* Dataset fijo en una base temporal (nunca ``finances.db``).
* Reloj congelado: ``date.today()`` y ``datetime.now()`` devuelven valores fijos.
* Regenerar el golden (solo intencionalmente, tras revisar el diff):
      LUMINA_REGENERAR_GOLDEN=1 python -m unittest tests.test_caracterizacion_responder
"""
from __future__ import annotations

import contextlib
import hashlib
import datetime as dt
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from lumina.advisor import intelligence as iq
from lumina.advisor import service as advisor
from lumina.core import database as db

MES = "2026-09"
GOLDEN = Path(__file__).parent / "fixtures" / "responder_caracterizacion.json"
FUENTE = Path(iq.__file__)


class _FechaFija(dt.date):
    @classmethod
    def today(cls) -> "_FechaFija":
        return cls(2026, 9, 28)


class _FechaHoraFija(dt.datetime):
    @classmethod
    def now(cls, tz: Any = None) -> "_FechaHoraFija":
        return cls(2026, 9, 28, 12, 0, 0)


# (id, pregunta, kwargs de responder). Cubre todas las intenciones de la tabla de rutas.
PREGUNTAS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("current_state", "¿Cómo estamos?", {}),
    ("current_state_2", "¿Cuál es nuestra situación financiera?", {}),
    ("affordability", "¿Nos alcanza para una salida de 200 mil?", {}),
    ("affordability_2", "¿Cuánto podemos gastar este fin de semana?", {}),
    ("purchase_evaluation", "¿Podemos comprar una moto de 8 millones?", {}),
    ("purchase_evaluation_2", "¿Vale la pena comprar un computador de 3.500.000?", {}),
    ("card_payment", "¿Cuánto le pago a la tarjeta este mes?", {}),
    ("card_payment_2", "¿Cómo pagamos esto de 500 mil, con débito o crédito?", {}),
    ("debt_strategy", "¿Qué deuda atacar primero?", {}),
    ("debt_strategy_2", "¿Avalancha o bola de nieve?", {}),
    ("savings", "¿Cuánto deberíamos ahorrar?", {}),
    ("savings_2", "¿Cómo va nuestro fondo de emergencia?", {}),
    ("fixed_expense", "¿Cuáles son nuestros gastos fijos?", {}),
    ("recurring_expense", "¿Qué gastos recurrentes no están registrados?", {}),
    ("spending_analysis", "¿Dónde se nos está yendo la plata?", {}),
    ("spending_analysis_2", "¿Qué gastos podríamos reducir?", {}),
    ("couple_analysis", "¿Quién está gastando más?", {}),
    ("couple_analysis_2", "¿Cuánto le debo a Sara?", {}),
    ("monthly_comparison", "¿Estamos gastando más que el mes pasado?", {}),
    ("anomaly", "¿Hay algo raro en los cobros?", {}),
    ("scenario", "¿Qué pasa si gastamos 500 mil más este mes?", {}),
    ("scenario_2", "Simula que dejamos de usar la tarjeta", {}),
    ("travel_comparison", "¿Podemos viajar a México o España?", {}),
    ("financial_explanation", "Explícame nuestras finanzas como si tuviera diez años", {}),
    ("data_integrity", "¿Por qué no cuadra el libro? Revisa la integridad de los datos", {}),
    ("what_should_we_do", "¿Qué deberíamos hacer este mes?", {}),
    ("recommendation", "¿Qué nos recomiendas?", {}),
    ("why", "¿De dónde sale ese número?", {}),
    ("financial_risk", "¿Cuál es nuestro riesgo financiero?", {}),
    ("financial_opportunity", "¿En qué podemos mejorar?", {}),
    ("card_status", "¿Cómo están las tarjetas?", {}),
    ("card_comparison", "¿Qué tarjeta conviene usar?", {}),
    ("debt_projection", "¿Cuándo terminamos de pagar las tarjetas?", {}),
    ("goal_status", "¿Cómo van las metas?", {}),
    ("budget", "¿Cuánto deberíamos presupuestar para el mes?", {}),
    ("personal_summary", "¿Cómo voy yo?", {}),
    ("personal_summary_sara", "¿Cuánto gastó Sara?", {}),
    ("monthly_summary", "Dame el resumen del mes", {}),
    ("income_change", "Bajó el ingreso este mes", {}),
    ("savings_capacity", "Capacidad de ahorro mensual", {}),
    ("category_analysis", "¿Cuánto gastamos en mercado?", {}),
    ("spending_trend", "¿Estamos gastando más en restaurantes?", {}),
    ("unusual_spending", "Hay un gasto inusual este mes", {}),
    ("monto_explicito", "¿Podemos comprar esto?", {"monto": 1_200_000}),
    ("mes_en_pregunta", "¿Cómo estamos en agosto de 2026?", {}),
    ("unknown", "hola, ¿qué tal el clima en Bogotá?", {}),
    ("unknown_ruido", "asdf qwerty", {}),
    ("diferir_tarjeta_baseline_B",
     "¿podemos diferir alguna tarjeta y pagar algunas en específico?", {}),
)

# Conversación con memoria: seguimientos, monto heredado y «¿por qué?».
SESION: tuple[str, ...] = (
    "¿Podemos viajar a México o España?",
    "¿Y a Cancún?",
    "¿Por qué?",
    "¿Podemos comprar una moto de 8 millones?",
    "¿Y si son 6 millones?",
    "¿Cómo lo pagamos?",
    "¿Por qué?",
)

# Preguntas por la capa de servicio (incluye la ruta de respaldo «unknown_legacy»).
PREGUNTAS_SERVICIO: tuple[str, ...] = (
    "¿Cómo estamos?",
    "hola, ¿qué tal el clima en Bogotá?",
    "¿podemos diferir alguna tarjeta y pagar algunas en específico?",
    "¿Cuánto puedo ahorrar?",
    "¿Cuánto me debe Samuel?",
)


def _dataset() -> None:
    """Base determinista: 4 meses de historia, dos tarjetas, ahorro, meta y gastos fijos."""
    for m in ("2026-06", "2026-07", "2026-08", "2026-09"):
        db.registrar_ingreso(m, "persona1", "Salario", 4_000_000)
        db.registrar_ingreso(m, "persona2", "Salario", 3_000_000)
    db.registrar_ingreso("2026-09", "persona2", "Freelance", 500_000)
    principal = db.registrar_tarjeta("Principal", "persona1", 3_000_000, pago_minimo=200_000, interes_mensual=3)
    secundaria = db.registrar_tarjeta("Secundaria", "persona2", 2_000_000, pago_minimo=100_000, interes_mensual=2)
    restaurantes = {"2026-06": 150_000, "2026-07": 180_000, "2026-08": 220_000, "2026-09": 420_000}
    for m in ("2026-06", "2026-07", "2026-08", "2026-09"):
        db.registrar_gasto(m, "Arriendo", "Hogar", 1_800_000, f"{m}-02", "debito", "persona1",
                           "compartido", 900_000, 900_000)
        db.registrar_gasto(m, "Servicios", "Hogar", 350_000, f"{m}-04", "debito", "persona2",
                           "compartido", 175_000, 175_000)
        db.registrar_gasto(m, "Mercado", "Alimentación", 600_000, f"{m}-10", "efectivo", "persona1",
                           "compartido", 300_000, 300_000)
        db.registrar_gasto(m, "Netflix", "Suscripciones", 40_000, f"{m}-05", "tarjeta", "persona2",
                           "persona2", 0, 40_000, secundaria, prioridad="discrecional")
        db.registrar_gasto(m, "Restaurantes", "Salidas", restaurantes[m], f"{m}-15", "debito", "persona1",
                           "compartido", restaurantes[m] // 2, restaurantes[m] - restaurantes[m] // 2,
                           prioridad="discrecional")
        db.registrar_gasto(m, "Transporte", "Transporte", 200_000, f"{m}-08", "efectivo", "persona1",
                           "persona1", 200_000, 0)
    db.registrar_gasto("2026-08", "Electrodoméstico", "Hogar", 600_000, "2026-08-15", "tarjeta",
                       "persona1", "compartido", 300_000, 300_000, principal)
    db.registrar_gasto("2026-09", "Celular", "Tecnología", 900_000, "2026-09-03", "tarjeta",
                       "persona1", "persona1", 900_000, 0, principal, cuotas=3, prioridad="discrecional")
    db.registrar_pago_tarjeta("2026-09", "2026-09-05", principal, 300_000, "persona1", 300_000, 0)
    fondo = db.crear_ahorro("Emergencia", "persona1", "Fondo de emergencia", 3_000_000)
    viaje = db.crear_ahorro("Viaje", "persona2", "Ahorro para viajar", 5_000_000)
    db.registrar_movimiento_ahorro("2026-08", "2026-08-20", fondo, "DEPOSITO", 600_000, "Aporte")
    db.registrar_movimiento_ahorro("2026-09", "2026-09-20", fondo, "DEPOSITO", 200_000, "Aporte")
    db.registrar_movimiento_ahorro("2026-09", "2026-09-21", viaje, "DEPOSITO", 300_000, "Aporte")
    db.crear_meta("Moto", 8_000_000, fecha_objetivo="2027-06-30", ahorro_id=viaje)
    db.registrar_gasto_fijo("Arriendo", "Hogar", 1_800_000, "mensual", "persona1", "compartido",
                            900_000, 900_000, dia_pago=2)
    db.registrar_gasto_fijo("Gimnasio", "Salud", 90_000, "mensual", "persona2", "persona2", 0, 90_000, dia_pago=10)
    db.registrar_liquidacion("2026-09", "2026-09-15", "persona2", "persona1", 150_000, concepto="Ajuste")


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _plano(valor: Any) -> Any:
    """Ida y vuelta por JSON con los transaction_uuid (aleatorios por diseño) enmascarados."""
    texto = json.dumps(valor, default=str, sort_keys=True, ensure_ascii=False)
    return json.loads(_UUID.sub("<uuid>", texto))


def _compactar(resultado: Any) -> Any:
    """Deja legible todo salvo ``datos`` (enorme), que se congela por hash SHA-256 + sus claves."""
    if isinstance(resultado, dict) and "datos" in resultado:
        datos = resultado["datos"]
        huella = hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        resultado = {**resultado, "datos": {"sha256": huella,
                                            "claves": sorted(datos) if isinstance(datos, dict) else None}}
    return resultado


def _capturar(funcion: Any) -> Any:
    try:
        return _compactar(_plano(funcion()))
    except Exception as error:  # noqa: BLE001 - el tipo de excepción también es comportamiento
        return {"__excepcion__": type(error).__name__, "mensaje": str(error)}


def _ejecutar() -> dict[str, Any]:
    """Corre todas las preguntas sobre el dataset y devuelve el resultado normalizado."""
    resultado: dict[str, Any] = {"responder": {}, "sesion": [], "servicio": {}}
    for clave, pregunta, kwargs in PREGUNTAS:
        resultado["responder"][clave] = _capturar(lambda p=pregunta, k=kwargs: iq.responder(MES, p, **k))
    resultado["responder"]["pregunta_vacia"] = _capturar(lambda: iq.responder(MES, "   "))
    iq.invalidar_contexto()
    sesion = iq.nueva_sesion(MES)
    for pregunta in SESION:
        resultado["sesion"].append({"pregunta": pregunta,
                                    "resultado": _capturar(lambda p=pregunta: iq.responder(MES, p, sesion=sesion))})
    resultado["sesion_historial"] = _plano(sesion.historial())
    for pregunta in PREGUNTAS_SERVICIO:
        resultado["servicio"][pregunta] = _capturar(lambda p=pregunta: advisor.responder_pregunta(MES, p))
    # Cada manejador de la tabla, invocado directamente: cubre también los alias que el
    # clasificador no produce y solo son alcanzables por herencia de sesión o llamada directa.
    perfil = iq.contexto_de(MES).perfil
    resultado["manejadores"] = {}
    for intencion, manejador in sorted(iq._RUTAS.items()):
        ctx_directo = {"intencion": intencion, "texto": "", "monto": None, "sesion": None}
        resultado["manejadores"][intencion] = _capturar(lambda m=manejador, c=ctx_directo: m(MES, c, perfil))
    resultado["rutas"] = {intencion: manejador.__name__ for intencion, manejador in sorted(iq._RUTAS.items())}
    resultado["intenciones"] = list(iq.INTENCIONES)
    return resultado


class _Base(unittest.TestCase):
    """Base temporal + reloj congelado; nunca toca ``finances.db``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls._ruta_original = db.DB_PATH
        db.DB_PATH = Path(cls._tmp.name) / "caracterizacion.db"
        cls._reloj = [mock.patch.object(dt, "date", _FechaFija), mock.patch.object(dt, "datetime", _FechaHoraFija)]
        for parche in cls._reloj:
            parche.start()
        with contextlib.redirect_stdout(io.StringIO()):
            db.init_db()
            _dataset()
        iq.invalidar_contexto()
        with contextlib.redirect_stdout(io.StringIO()):
            cls.obtenido = _ejecutar()

    @classmethod
    def tearDownClass(cls) -> None:
        for parche in cls._reloj:
            parche.stop()
        db.DB_PATH = cls._ruta_original
        cls._tmp.cleanup()


class CaracterizacionResponderTests(_Base):
    def test_todos_los_manejadores_de_la_tabla_estan_caracterizados(self) -> None:
        self.assertEqual(sorted(self.obtenido["manejadores"]), sorted(iq.INTENCIONES))

    def test_intenciones_que_el_clasificador_produce_estan_cubiertas_por_preguntas(self) -> None:
        producibles = {regla[0] for regla in iq._REGLAS_INTENCION} | {"unknown"}
        # ``contexto.intencion`` es la que clasificó; la ``intencion`` del resultado la fija cada manejador
        # (varios alias comparten manejador y responden con otro nombre).
        cubiertas = {r["contexto"]["intencion"] for r in self.obtenido["responder"].values() if "contexto" in r}
        cubiertas |= {t["resultado"]["contexto"]["intencion"] for t in self.obtenido["sesion"]
                      if "contexto" in t["resultado"]}
        faltan = sorted(producibles - cubiertas)
        self.assertEqual(faltan, [], f"Intenciones producibles sin pregunta de caracterización: {faltan}")

    def test_salidas_identicas_al_golden(self) -> None:
        if os.environ.get("LUMINA_REGENERAR_GOLDEN") == "1":
            GOLDEN.write_text(json.dumps(self.obtenido, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                              encoding="utf-8")
            self.skipTest("Golden regenerado; revisa el diff antes de continuar.")
        esperado = json.loads(GOLDEN.read_text(encoding="utf-8"))
        for seccion in ("responder", "servicio", "manejadores"):
            for clave, valor in esperado[seccion].items():
                with self.subTest(seccion=seccion, clave=clave):
                    self.assertEqual(self.obtenido[seccion][clave], valor)
            self.assertEqual(sorted(self.obtenido[seccion]), sorted(esperado[seccion]))
        self.assertEqual(self.obtenido["sesion"], esperado["sesion"])
        self.assertEqual(self.obtenido["sesion_historial"], esperado["sesion_historial"])

    def test_tabla_de_rutas_e_intenciones_no_cambian(self) -> None:
        esperado = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(self.obtenido["rutas"], esperado["rutas"])
        self.assertEqual(self.obtenido["intenciones"], esperado["intenciones"])
        self.assertEqual(list(iq.INTENCIONES), sorted(iq._RUTAS))

    def test_la_salida_es_determinista_entre_ejecuciones(self) -> None:
        iq.invalidar_contexto()
        with contextlib.redirect_stdout(io.StringIO()):
            segunda = _ejecutar()
        self.assertEqual(segunda, self.obtenido)

    def test_la_captura_no_contiene_uuid_ni_rutas_temporales(self) -> None:
        texto = json.dumps(self.obtenido, ensure_ascii=False)
        self.assertIsNone(_UUID.search(texto))
        self.assertNotIn(self._tmp.name, texto)

    def test_baseline_B_sigue_documentado_como_es_hoy(self) -> None:
        """Congela el fallo B: la intención nueva captura «diferir tarjeta» (no es la respuesta ideal)."""
        crudo = self.obtenido["responder"]["diferir_tarjeta_baseline_B"]
        self.assertEqual(crudo["intencion"], "card_deferral")
        self.assertIn("qué tarjeta(s) quieres diferir", crudo["respuesta"])


class EstructuraIntelligenceTests(unittest.TestCase):
    """Guardas estáticas: una sola tabla de rutas y un solo ``responder``."""

    def setUp(self) -> None:
        self.fuente = FUENTE.read_text(encoding="utf-8")

    def test_un_solo_responder(self) -> None:
        self.assertEqual(len(re.findall(r"^def responder\(", self.fuente, re.M)), 1)

    def test_una_sola_definicion_de_rutas_e_intenciones(self) -> None:
        self.assertEqual(len(re.findall(r"^_RUTAS\b(?:\s*:[^=]+)?\s*=", self.fuente, re.M)), 1)
        self.assertEqual(len(re.findall(r"^INTENCIONES\b(?:\s*:[^=]+)?\s*=", self.fuente, re.M)), 1)
        self.assertNotIn("**_RUTAS", self.fuente)
        self.assertNotIn('_RUTAS["', self.fuente)


if __name__ == "__main__":
    unittest.main()
