"""Verificación del asesor de LÚMINA contra el esquema y los datos reales.

No comprueba imports: ejecuta cada capacidad contra la base y valida
condiciones concretas. Cada caso imprime lo que obtuvo para poder revisarlo.

Uso:
    python test_advisor.py            # usa data/finances.db
    python test_advisor.py 2026-09    # fija el mes de análisis
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

from lumina.advisor import context as actx
from lumina.advisor import findings as af
from lumina.advisor import intelligence as iq
from lumina.core import database as db
from lumina.advisor import service as advisor
from lumina.core import engine
from lumina.ui.service import FinanceService

MES = sys.argv[1] if len(sys.argv) > 1 else "2026-09"
OK: list[str] = []
FALLOS: list[tuple[str, str]] = []
SECCION = {"actual": ""}


def seccion(nombre: str) -> None:
    SECCION["actual"] = nombre
    print(f"\n{'=' * 70}\n{nombre}\n{'=' * 70}")


def probar(nombre: str, funcion: Callable[[], Any], *,
           esperar: Callable[[Any], bool] | None = None,
           mostrar: Callable[[Any], str] | None = None) -> Any:
    """Ejecuta un caso, valida su condición y reporta el resultado."""
    etiqueta = f"[{SECCION['actual']}] {nombre}"
    try:
        resultado = funcion()
    except Exception as error:  # noqa: BLE001 - cualquier excepción es un fallo real
        FALLOS.append((etiqueta, f"{type(error).__name__}: {error}"))
        print(f"\nFALLO {nombre}\n{traceback.format_exc()}")
        return None
    if esperar is not None:
        try:
            cumple = bool(esperar(resultado))
        except Exception as error:  # noqa: BLE001
            FALLOS.append((etiqueta, f"la condición falló: {type(error).__name__}: {error}"))
            print(f"FALLO {nombre}: la condición lanzó {error}")
            return resultado
        if not cumple:
            FALLOS.append((etiqueta, "no cumplió la condición esperada"))
            print(f"FALLO {nombre}: no cumplió la condición\n   {str(resultado)[:300]}")
            return resultado
    OK.append(etiqueta)
    detalle = mostrar(resultado) if mostrar else ""
    print(f"ok  {nombre}" + (f"\n   {detalle}" if detalle else ""))
    return resultado


def _capturar(funcion: Callable[[], Any]) -> Any:
    try:
        return funcion()
    except Exception as error:  # noqa: BLE001 - se está probando el rechazo
        return error


# ---------------------------------------------------------------------------
# 1. Parsing de montos en formato colombiano
# ---------------------------------------------------------------------------

def probar_montos() -> None:
    seccion("1. Formatos de dinero colombianos")
    casos = [
        ("150.000", 150_000), ("150,000", 150_000), ("150000", 150_000),
        ("150 mil", 150_000), ("150k", 150_000), ("400 mil", 400_000),
        ("400.000", 400_000), ("1.5 millones", 1_500_000), ("1,5 millones", 1_500_000),
        ("1.500.000", 1_500_000), ("$150.000", 150_000), ("COP 150.000", 150_000),
        ("1.500", 1_500), ("2 millones", 2_000_000),
        ("¿podemos gastar 1.200.000?", 1_200_000),
        ("¿podemos gastar 80 mil?", 80_000),
        ("¿qué pasa si gastamos 300k en 2026-09?", 300_000),
        ("una pregunta sin monto", None),
        ("", None), ("¿cómo vamos?", None),
    ]
    for texto, esperado in casos:
        probar(f"«{texto}» -> {esperado}",
               lambda t=texto: iq.extraer_monto(t),
               esperar=lambda valor, e=esperado: valor == e,
               mostrar=lambda valor: f"obtenido: {valor}")
    probar("No confunde 1.500 con 1.5 millones",
           lambda: (iq.extraer_monto("1.500"), iq.extraer_monto("1.5 millones")),
           esperar=lambda r: r == (1_500, 1_500_000))
    probar("Entrada malformada no rompe",
           lambda: [iq.extraer_monto(valor) for valor in ("...", "$$$", "mil", "k", "-")],
           esperar=lambda r: all(valor is None or isinstance(valor, int) for valor in r))


# ---------------------------------------------------------------------------
# 2. Contexto, rendimiento y solo lectura
# ---------------------------------------------------------------------------

def probar_contexto() -> None:
    seccion("2. Contexto financiero y solo lectura")
    probar("El contexto se construye con secciones perezosas",
           lambda: iq.construir_contexto(MES),
           esperar=lambda c: c.calculadas() == [] and "perfil" in c.disponibles(),
           mostrar=lambda c: f"{len(c.disponibles())} secciones declaradas, 0 calculadas")
    probar("Precargar calcula y reutiliza",
           lambda: iq.construir_contexto(MES).precargar("perfil", "hallazgos"),
           esperar=lambda c: "perfil" in c.calculadas() and "hallazgos" in c.calculadas())
    probar("La caché de lecturas acierta",
           lambda: (actx.ESTADISTICAS.copy(), iq.estado_actual(MES), actx.ESTADISTICAS.copy()),
           esperar=lambda r: r[2]["aciertos"] >= r[0]["aciertos"],
           mostrar=lambda r: f"llamadas={r[2]['llamadas']} aciertos={r[2]['aciertos']}")
    probar("El análisis completo NO modifica la base",
           lambda: (engine.snapshot_database(),
                    iq.estado_actual(MES), iq.plan_de_accion(MES), iq.riesgo_financiero(MES),
                    iq.cotejar_gastos_fijos(MES), iq.explicacion_estructurada(MES),
                    iq.simular_escenario_detallado(MES, "gasto", 100_000),
                    engine.snapshot_database()),
           esperar=lambda r: r[0] == r[-1],
           mostrar=lambda r: "snapshot idéntico antes y después")
    probar("Invalidar el contexto no rompe nada",
           lambda: (iq.invalidar_contexto(), iq.estado_actual(MES)["titular"]),
           esperar=lambda r: isinstance(r[1], str) and bool(r[1]))


# ---------------------------------------------------------------------------
# 3. Estado financiero y diagnóstico
# ---------------------------------------------------------------------------

def _orden_coherente(hallazgos: list[dict[str, Any]]) -> bool:
    orden = [af.ORDEN_SEVERIDAD.get(item["severidad"], 9) for item in hallazgos]
    return orden == sorted(orden)


def probar_estado() -> None:
    seccion("3. Estado financiero y diagnóstico")
    estado = probar("Estado financiero actual", lambda: advisor.estado_financiero(MES),
                    esperar=lambda r: bool(r["titular"]) and isinstance(r["hallazgos"], list),
                    mostrar=lambda r: r["titular"])
    if estado:
        for hallazgo in estado["hallazgos"][:4]:
            print(f"   - [{hallazgo['prioridad']}] {hallazgo['titulo']}")
            print(f"     QUÉ: {hallazgo['que']}")
            print(f"     POR QUÉ: {hallazgo['por_que']}")
            print(f"     ACCIÓN: {hallazgo['accion']}")
            print(f"     SEVERIDAD: {hallazgo['razon_severidad']}")
    probar("Todo hallazgo trae estructura completa",
           lambda: advisor.hallazgos(MES),
           esperar=lambda r: all(
               {"id", "categoria", "severidad", "titulo", "que", "por_que", "accion", "impacto",
                "evidencia", "confianza", "razon_severidad", "prioridad"} <= set(item) for item in r),
           mostrar=lambda r: f"{len(r)} hallazgos con esquema completo")
    probar("Toda severidad viene justificada",
           lambda: advisor.hallazgos(MES),
           esperar=lambda r: all(item["razon_severidad"] for item in r))
    probar("Prioriza impacto grande sobre detalle pequeño",
           lambda: advisor.hallazgos(MES), esperar=_orden_coherente,
           mostrar=lambda r: " > ".join(f"{i['titulo'][:28]}({i['prioridad']})" for i in r[:3]))
    probar("Hallazgos agrupados por decisión",
           lambda: advisor.hallazgos_agrupados(MES),
           esperar=lambda r: isinstance(r, list) and all("tipo" in item for item in r),
           mostrar=lambda r: ", ".join(f"{i['tipo']}:{i.get('titulo', '')[:26]}" for i in r[:4]))
    probar("Modelo de riesgo multifactor",
           lambda: advisor.riesgo_financiero(MES),
           esperar=lambda r: len(r["factores"]) >= 6 and r["nivel"] in ("alto", "moderado", "contenido"),
           mostrar=lambda r: f"{r['nivel']} | dominantes: {r['factores_dominantes']}")
    probar("Oportunidades basadas en datos",
           lambda: advisor.oportunidades(MES),
           esperar=lambda r: all(item["evidencia"] for item in r),
           mostrar=lambda r: "; ".join(item["titulo"] for item in r[:3]) or "ninguna")
    probar("Dimensiones de salud separadas",
           lambda: advisor.dimensiones_salud(MES),
           esperar=lambda r: len(r["dimensiones"]) == 7,
           mostrar=lambda r: ", ".join(f"{d['dimension']}={d['nivel']}" for d in r["dimensiones"]))
    probar("Plan de acción con horizontes",
           lambda: advisor.plan_de_accion(MES),
           esperar=lambda r: {"inmediato", "este_mes", "proximos_tres_meses",
                              "optimizacion_opcional", "resumen"} <= set(r),
           mostrar=lambda r: r["resumen"])
    probar("Explicación estructurada en nueve bloques",
           lambda: advisor.explicacion_estructurada(MES),
           esperar=lambda r: len(r["bloques"]) == 9 and all(b["texto"] for b in r["bloques"]),
           mostrar=lambda r: " | ".join(b["titulo"] for b in r["bloques"]))


# ---------------------------------------------------------------------------
# 4. Gastos fijos y recurrencia
# ---------------------------------------------------------------------------

def probar_gastos_fijos() -> None:
    seccion("4. Detección de gastos fijos y cotejo")
    deteccion = probar("Detección de candidatos", lambda: advisor.detectar_gastos_fijos(MES),
                       esperar=lambda r: isinstance(r["candidatos"], list),
                       mostrar=lambda r: f"{r['cantidad_nuevos']} nuevos por {af.fmt_cop(r['total_mensual_estimado'])}")
    if deteccion:
        for candidato in deteccion["candidatos"][:5]:
            print(f"   - {candidato['nombre']}: {af.fmt_cop(candidato['monto_promedio'])} "
                  f"{candidato['frecuencia']}, {candidato['ocurrencias']} meses, "
                  f"cv={candidato['variacion_monto']:.2f}, clase={candidato['clase']}, "
                  f"conf={candidato['puntaje_confianza']:.2f}")
    probar("Cada candidato trae todas las métricas exigidas",
           lambda: advisor.detectar_gastos_fijos(MES, incluir_registrados=True)["todos"],
           esperar=lambda r: all(
               {"ocurrencias", "meses_detectados", "frecuencia", "monto_promedio", "monto_mediano",
                "monto_minimo", "monto_maximo", "variacion_monto", "regularidad", "ultima_aparicion",
                "meses_sin_aparecer", "metodo_pago", "tarjeta", "pagador", "responsabilidad",
                "confianza", "ya_registrado", "evidencia"} <= set(item) for item in r))
    probar("No clasifica como fijo algo con dos apariciones",
           lambda: advisor.detectar_gastos_fijos(MES, incluir_registrados=True)["todos"],
           esperar=lambda r: all(item["ocurrencias"] >= 3 for item in r))
    probar("Un comercio con varias compras al mes no es gasto fijo",
           lambda: advisor.detectar_gastos_fijos(MES, incluir_registrados=True),
           esperar=lambda r: all(item.get("apariciones_por_mes", 0) <= 1.5 for item in r["todos"]),
           mostrar=lambda r: "descartados: " + "; ".join(d["nombre"] for d in r["descartados"][:3]))
    cotejo = probar("Cotejo registrado vs realidad",
                    lambda: advisor.cotejar_gastos_fijos(MES),
                    esperar=lambda r: {"sin_registrar", "sin_cobros_recientes", "monto_desactualizado",
                                       "frecuencia_distinta", "responsabilidad_distinta",
                                       "titular_distinto"} <= set(r),
                    mostrar=lambda r: (f"{r['total_desajustes']} desajustes | sin registrar: "
                                       f"{len(r['sin_registrar'])} | monto viejo: "
                                       f"{len(r['monto_desactualizado'])} | inactivos: "
                                       f"{len(r['sin_cobros_recientes'])}"))
    if cotejo:
        for item in cotejo["monto_desactualizado"][:3]:
            print(f"   - {item['detalle']}")
    probar("La clasificación se explica con evidencia",
           lambda: advisor.podria_ser_gasto_fijo("Netflix", mes=MES),
           esperar=lambda r: bool(r["evidencia"]) and bool(r["razon"]),
           mostrar=lambda r: f"{r['respuesta_corta']} ({r['confianza']:.2f}) :: {r['razon']}")
    probar("Consulta de comercio inexistente no inventa",
           lambda: advisor.podria_ser_gasto_fijo("Comercio Que No Existe", mes=MES),
           esperar=lambda r: r["es_candidato"] is False and r["ocurrencias"] == 0,
           mostrar=lambda r: r["respuesta"][:130])
    probar("Monto recurrente variable queda en revisión",
           lambda: advisor.podria_ser_gasto_fijo("Restaurante", mes=MES),
           esperar=lambda r: r["respuesta_corta"] in (iq.RESPUESTA_REVISAR, iq.RESPUESTA_NO),
           mostrar=lambda r: f"{r['respuesta_corta']} :: {r['razon'][:110]}")


# ---------------------------------------------------------------------------
# 5. Tarjetas, deuda, ahorro y pareja
# ---------------------------------------------------------------------------

def probar_dominios() -> None:
    seccion("5. Tarjetas, deuda, ahorro y pareja")
    probar("Inteligencia de tarjetas con señales",
           lambda: advisor.inteligencia_tarjetas(MES),
           esperar=lambda r: all("señales" in item for item in r["tarjetas"]),
           mostrar=lambda r: " | ".join(
               f"{t['nombre']}: {len(t['señales'])} señales, uso {t['utilizacion']:.0%}"
               for t in r["tarjetas"]))
    probar("Titular y responsabilidad se reportan por separado",
           lambda: advisor.inteligencia_tarjetas(MES),
           esperar=lambda r: all("deuda_responsabilidad" in i and "titular" in i for i in r["tarjetas"]),
           mostrar=lambda r: "; ".join(
               f"{t['nombre']} titular={t['titular']} resp={t['deuda_responsabilidad']}"
               for t in r["tarjetas"]))
    probar("Comparación de tarjetas",
           lambda: advisor.comparar_tarjetas(MES),
           esperar=lambda r: "lectura" in r or "mensaje" in r,
           mostrar=lambda r: r.get("lectura", r.get("mensaje", ""))[:170])
    probar("Avalancha vs snowball con cifras",
           lambda: advisor.perfil_financiero(MES)["deuda"],
           esperar=lambda r: "avalancha" in r and "snowball" in r,
           mostrar=lambda r: (f"avalancha={r['avalancha'].get('months')} meses / interés "
                              f"{af.fmt_cop(r['avalancha'].get('total_interest', 0))} | "
                              f"snowball={r['snowball'].get('months')} meses / interés "
                              f"{af.fmt_cop(r['snowball'].get('total_interest', 0))}"))
    probar("Inteligencia de ahorro con estados de meta",
           lambda: advisor.inteligencia_ahorro(MES),
           esperar=lambda r: all(m["estado"] in ("cumplida", "en_ritmo", "atrasada", "inactiva", "sin_fecha")
                                 for m in r["metas"]),
           mostrar=lambda r: "; ".join(f"{m['nombre']}={m['estado']}" for m in r["metas"]))
    probar("Análisis de pareja sin repartir por tarjeta",
           lambda: advisor.analisis_pareja(MES),
           esperar=lambda r: "regla" in r and "compras_en_tarjeta_del_otro" in r,
           mostrar=lambda r: (f"saldo={af.fmt_cop(r['saldo_pendiente'])} deudor={r['deudor']} "
                              f"cruces={len(r['compras_en_tarjeta_del_otro'])} "
                              f"sin distribución={len(r['gastos_sin_distribucion'])}"))
    probar("Comparación mensual con contribución al cambio",
           lambda: advisor.comparar_meses(MES),
           esperar=lambda r: "principales_subidas" in r,
           mostrar=lambda r: (f"gastos {af.fmt_cop(r['gastos']['diferencia'])} | " + "; ".join(
               f"{i['categoria']} {af.fmt_cop(i['diferencia'])}" for i in r["principales_subidas"][:3])))
    probar("Anomalías explican por qué se marcaron",
           lambda: advisor.anomalias(MES),
           esperar=lambda r: all(item["detalle"] and item["nota"] for item in r),
           mostrar=lambda r: "; ".join(a["tipo"] for a in r[:5]))


# ---------------------------------------------------------------------------
# 6. Decisiones
# ---------------------------------------------------------------------------

def probar_decisiones() -> None:
    seccion("6. Decisiones: asequibilidad, pago y escenarios")
    probar("Asequibilidad con monto",
           lambda: advisor.evaluar_asequibilidad(MES, 150_000),
           esperar=lambda r: r["veredicto"] in ("asequible", "al_limite", "no_asequible"),
           mostrar=lambda r: f"{r['veredicto']} :: {r['mensaje']}")
    probar("Asequibilidad sin monto devuelve el techo",
           lambda: advisor.evaluar_asequibilidad(MES),
           esperar=lambda r: r["veredicto"] == "informativo" and r["maximo_discrecional"] >= 0,
           mostrar=lambda r: r["mensaje"])
    probar("Un gasto mayor al flujo no se responde con un sí seco",
           lambda: advisor.evaluar_asequibilidad(MES, 8_500_000),
           esperar=lambda r: r["veredicto"] != "asequible" or not r["excede_flujo_del_mes"],
           mostrar=lambda r: f"{r['veredicto']} :: {r['mensaje'][:150]}")
    probar("Monto inválido se rechaza",
           lambda: _capturar(lambda: advisor.evaluar_asequibilidad(MES, -100)),
           esperar=lambda r: isinstance(r, ValueError),
           mostrar=lambda r: f"rechazado: {r}")
    opciones = probar("Opciones de pago comparadas",
                      lambda: advisor.opciones_de_pago(MES, 600_000),
                      esperar=lambda r: len(r["opciones"]) >= 3 and bool(r["recomendada"]),
                      mostrar=lambda r: f"recomendada: {r['recomendada']} :: {r['razon'][:120]}")
    if opciones:
        for opcion in opciones["opciones"]:
            print(f"   - {opcion['opcion']}: viable={opcion['viable']} | {opcion['impacto'][:110]}")
    probar("No recomienda tarjeta solo porque hay cupo",
           lambda: advisor.opciones_de_pago(MES, 50_000),
           esperar=lambda r: r["recomendada"].lower().startswith(("efectivo", "esperar")),
           mostrar=lambda r: r["recomendada"])
    for tipo, monto in (("gasto", 500_000), ("pago_tarjeta", 1_000_000), ("perdida_ingresos", 500_000),
                        ("ahorro_adicional", 300_000), ("acelerar_meta", 400_000),
                        ("aplazar_meta", 200_000), ("reduccion_gasto_fijo", 80_000),
                        ("no_pagar_tarjeta", 180_000), ("nueva_deuda", 2_000_000),
                        ("retiro_cajita", 300_000), ("viaje", 6_000_000)):
        probar(f"Escenario {tipo} por {af.fmt_cop(monto)}",
               lambda t=tipo, m=monto: advisor.simular_escenario_detallado(MES, t, m),
               esperar=lambda r: ("progreso_metas" in r["antes"] and r["modifica_base"] is False
                                  and r["veredicto"] in ("sostenible", "riesgoso", "no_sostenible")),
               mostrar=lambda r: f"{r['veredicto']} | {r['explicacion'][:130]}")
    probar("Escenario inexistente se rechaza",
           lambda: _capturar(lambda: advisor.simular_escenario_detallado(MES, "teletransporte", 1000)),
           esperar=lambda r: isinstance(r, ValueError))


# ---------------------------------------------------------------------------
# 7. Viajes
# ---------------------------------------------------------------------------

def probar_viajes() -> None:
    seccion("7. Viajes y comparación de destinos")
    probar("Sin costos, no inventa precios",
           lambda: advisor.comparar_destinos(MES, []),
           esperar=lambda r: r["listo"] is False and "contexto_financiero" in r,
           mostrar=lambda r: r["mensaje"][:150])
    destinos = [
        {"nombre": "México", "transporte_ida_vuelta": 2_400_000, "alojamiento_noche": 320_000,
         "comida_dia": 120_000, "transporte_local_dia": 60_000, "actividades": 900_000,
         "seguro_visa_otros": 300_000, "noches": 7, "viajeros": 2},
        {"nombre": "España", "transporte_ida_vuelta": 4_100_000, "alojamiento_noche": 420_000,
         "comida_dia": 160_000, "transporte_local_dia": 80_000, "actividades": 1_200_000,
         "seguro_visa_otros": 500_000, "noches": 9, "viajeros": 2},
    ]
    probar("Comparación con datos entregados",
           lambda: advisor.comparar_destinos(MES, destinos, meses_para_viajar=12),
           esperar=lambda r: r["listo"] and r["comparacion"] and len(r["destinos"]) == 2,
           mostrar=lambda r: "\n   ".join(
               [f"{d['nombre']}: total {af.fmt_cop(d['total'])}, ahorro mensual "
                f"{af.fmt_cop(d['ahorro_mensual_requerido'] or 0)}, {d['asequible']}"
                for d in r["destinos"]] + [r["comparacion"]["lectura"]]))
    probar("Destino incompleto declara qué falta",
           lambda: advisor.comparar_destinos(MES, [{"nombre": "Lisboa", "noches": 5}]),
           esperar=lambda r: bool(r["destinos"][0]["datos_faltantes"]),
           mostrar=lambda r: "faltan: " + ", ".join(r["destinos"][0]["datos_faltantes"]))
    probar("La plantilla dice exactamente qué se necesita",
           lambda: advisor.plantilla_viaje(), esperar=lambda r: len(r["campos"]) >= 8)


# ---------------------------------------------------------------------------
# 8. Conversación
# ---------------------------------------------------------------------------

PREGUNTAS: list[tuple[str, str | None]] = [
    ("¿Cuál es nuestra situación financiera actual?", "current_state"),
    ("¿Qué deberíamos hacer?", "what_should_we_do"),
    ("¿Qué riesgos tenemos?", "financial_risk"),
    ("¿Qué oportunidades ves?", "financial_opportunity"),
    ("¿Podemos salir hoy?", "affordability"),
    ("¿Cuánto podemos gastar en una salida?", "affordability"),
    ("¿Podemos gastar 150.000?", "affordability"),
    ("¿Podemos comprar una moto de 8.500.000?", "purchase_evaluation"),
    ("¿Cómo pago esto de 400 mil?", "card_payment"),
    ("¿Es mejor pagar con débito o crédito?", "card_payment"),
    ("¿Cuánto deberíamos pagarle a la tarjeta?", "card_payment"),
    ("¿Cómo están las tarjetas?", "card_status"),
    ("¿Qué tarjeta conviene usar?", "card_comparison"),
    ("¿Qué deuda deberíamos atacar primero?", "debt_strategy"),
    ("¿Cuándo salimos de las tarjetas?", None),
    ("¿Por qué estamos gastando tanto?", "spending_analysis"),
    ("¿Dónde se nos está yendo la plata?", "spending_analysis"),
    ("¿Cuánto gastamos en Mercado?", "category_analysis"),
    ("¿Quién está gastando más?", "couple_analysis"),
    ("¿Cuánto le debo a Sara?", "couple_analysis"),
    ("¿Cómo va Samuel?", "personal_summary"),
    ("¿Qué gastos fijos tenemos?", "fixed_expense"),
    ("¿Qué gastos parecen fijos pero no están registrados?", "recurring_expense"),
    ("¿Tenemos capacidad para ahorrar 300.000 este mes?", "savings"),
    ("¿Cómo van las metas?", "goal_status"),
    ("¿Qué pasa si gastamos 500.000?", "scenario"),
    ("¿Qué pasa si este mes gano menos?", "scenario"),
    ("¿Qué pasa si pagamos 1.000.000 de la tarjeta?", "scenario"),
    ("¿Hay algo raro en los gastos de este mes?", "anomaly"),
    ("¿Cómo vamos comparados con el mes pasado?", "monthly_comparison"),
    ("¿El gasto viene subiendo?", "spending_trend"),
    ("¿El libro cuadra o hay descuadre?", "data_integrity"),
    ("Explícame nuestras finanzas como si no entendiera nada.", "financial_explanation"),
    ("¿Cuánto deberíamos presupuestar este mes?", "budget"),
    ("¿Bajó el ingreso este mes?", "income_change"),
    ("¿Dónde nos conviene más ir, México o España?", "travel_comparison"),
]


def probar_conversacion() -> None:
    seccion("8. Conversación en lenguaje natural")
    for pregunta, intencion_esperada in PREGUNTAS:
        probar(f"«{pregunta}»",
               lambda p=pregunta: advisor.responder_pregunta(MES, p),
               esperar=lambda r, esperada=intencion_esperada: (
                   bool(r["respuesta"]) and r["intencion"] != "unknown"
                   and (esperada is None or r["intencion"] == esperada)),
               mostrar=lambda r: f"[{r['intencion']}] {r['respuesta'][:220].replace(chr(10), ' | ')}")
    probar("Pregunta fuera de dominio se admite como desconocida",
           lambda: advisor.responder_pregunta(MES, "¿Cuál es la capital de Mongolia?"),
           esperar=lambda r: r["intencion"] in ("unknown", "unknown_legacy"),
           mostrar=lambda r: r["respuesta"][:150])
    probar("Pregunta vacía se rechaza",
           lambda: _capturar(lambda: advisor.responder_pregunta(MES, "   ")),
           esperar=lambda r: isinstance(r, ValueError))

    seccion("8b. Seguimiento conversacional")
    sesion = advisor.nueva_sesion(MES)
    probar("Turno 1: ¿podemos viajar?",
           lambda: advisor.conversar(MES, "¿Podemos viajar?", sesion=sesion),
           esperar=lambda r: r["intencion"] == "travel_comparison",
           mostrar=lambda r: r["respuesta"][:130])
    probar("Turno 2 hereda el tema: ¿y a España?",
           lambda: advisor.conversar(MES, "¿Y a España?", sesion=sesion),
           esperar=lambda r: r["intencion"] == "travel_comparison" and "España" in r["respuesta"],
           mostrar=lambda r: r["respuesta"][:130])
    probar("Turno 3: monto nuevo sobre la misma pregunta",
           lambda: (advisor.conversar(MES, "¿Podemos gastar 150.000?", sesion=sesion),
                    advisor.conversar(MES, "¿y 900 mil?", sesion=sesion))[1],
           esperar=lambda r: r["intencion"] == "affordability" and r["contexto"]["monto"] == 900_000,
           mostrar=lambda r: r["respuesta"][:130])
    probar("Turno 4: ¿por qué? traza la respuesta anterior",
           lambda: advisor.conversar(MES, "¿por qué?", sesion=sesion),
           esperar=lambda r: r["intencion"] == "why" and "900" in r["respuesta"],
           mostrar=lambda r: r["respuesta"][:200].replace("\n", " | "))
    probar("La sesión guarda historial acotado",
           lambda: sesion.historial(),
           esperar=lambda r: 0 < len(r) <= sesion.limite_turnos,
           mostrar=lambda r: f"{len(r)} turnos: " + ", ".join(t["intencion"] for t in r))


# ---------------------------------------------------------------------------
# 9. Integridad
# ---------------------------------------------------------------------------

def probar_integridad() -> None:
    seccion("9. Integridad de datos")
    probar("Informe de integridad del libro actual",
           lambda: advisor.informe_integridad(MES),
           esperar=lambda r: "mensaje" in r and "candidatos_descuadre" in r,
           mostrar=lambda r: (f"cuadra={r['cuadra']} descuadre={af.fmt_cop(r['descuadre'])} :: "
                              f"{r['mensaje'][:140]}"))
    original = db.DB_PATH
    try:
        copia = Path(tempfile.mkdtemp()) / "descuadre.db"
        copia.write_bytes(Path(original).read_bytes())
        conexion = sqlite3.connect(copia)
        fila = conexion.execute(
            "SELECT id FROM gastos WHERE responsabilidad='compartido' ORDER BY valor DESC LIMIT 1").fetchone()
        if fila:
            conexion.execute("UPDATE gastos SET monto_p1 = monto_p1 + 478467 WHERE id = ?", (fila[0],))
            conexion.commit()
        conexion.close()
        db.DB_PATH = copia
        iq.invalidar_contexto()
        probar("Un descuadre forzado se reporta, no se esconde",
               lambda: advisor.informe_integridad(MES),
               esperar=lambda r: r["cuadra"] is False and r["descuadre_absoluto"] == 478_467,
               mostrar=lambda r: r["mensaje"][:180])
        probar("Se exponen movimientos candidatos concretos",
               lambda: advisor.informe_integridad(MES)["candidatos_descuadre"],
               esperar=lambda r: any("distribución" in item["motivo"] for item in r),
               mostrar=lambda r: "; ".join(f"{i['tipo']}#{i['id']} {i['motivo'][:60]}" for i in r[:2]))
        probar("El saldo de pareja se matiza cuando el libro no cuadra",
               lambda: advisor.responder_pregunta(MES, "¿cuánto le debo a Sara?"),
               esperar=lambda r: "descuadre" in r["respuesta"].lower(),
               mostrar=lambda r: r["respuesta"][:180])
        probar("Los cálculos del mes siguen disponibles pese al descuadre",
               lambda: advisor.evaluar_asequibilidad(MES, 100_000),
               esperar=lambda r: r["veredicto"] in ("asequible", "al_limite", "no_asequible"))
    finally:
        db.DB_PATH = original
        iq.invalidar_contexto()


# ---------------------------------------------------------------------------
# 10. Casos límite
# ---------------------------------------------------------------------------

def probar_casos_limite() -> None:
    seccion("10. Casos límite")
    original = db.DB_PATH
    carpeta = Path(tempfile.mkdtemp())
    try:
        db.DB_PATH = carpeta / "vacia.db"
        db.init_db()
        iq.invalidar_contexto()
        probar("Base vacía: estado sin inventar nada",
               lambda: advisor.estado_financiero("2026-09"),
               esperar=lambda r: isinstance(r["titular"], str) and bool(r["titular"]),
               mostrar=lambda r: r["titular"][:150])
        probar("Base vacía: asequibilidad no promete dinero",
               lambda: advisor.evaluar_asequibilidad("2026-09", 100_000),
               esperar=lambda r: r["veredicto"] == "no_asequible" and r["maximo_discrecional"] == 0,
               mostrar=lambda r: r["mensaje"][:140])
        probar("Base vacía: no detecta gastos fijos",
               lambda: advisor.detectar_gastos_fijos("2026-09"),
               esperar=lambda r: r["cantidad_nuevos"] == 0)
        probar("Base vacía: conversación responde sin romperse",
               lambda: advisor.responder_pregunta("2026-09", "¿podemos salir hoy?"),
               esperar=lambda r: bool(r["respuesta"]),
               mostrar=lambda r: r["respuesta"][:140])
        probar("Base vacía: plan de acción sin acciones inventadas",
               lambda: advisor.plan_de_accion("2026-09"),
               esperar=lambda r: isinstance(r["resumen"], str))

        db.registrar_ingreso("2026-09", "persona1", "Salario", 3_000_000)
        iq.invalidar_contexto()
        probar("Un solo movimiento: no hay tendencia inventada",
               lambda: advisor.tendencias("2026-09"),
               esperar=lambda r: r["gastos"]["direccion"] in ("sin_datos", "estable"),
               mostrar=lambda r: f"dirección={r['gastos']['direccion']}")
        probar("Un solo movimiento: comparación declara falta de historial",
               lambda: advisor.comparar_meses("2026-09"),
               esperar=lambda r: r["suficiente_historial"] is False)
        db.registrar_gasto("2026-09", "Netflix", "Entretenimiento", 39_900, "2026-09-14",
                           "debito", "persona1", "compartido", 19_950, 19_950)
        iq.invalidar_contexto()
        probar("Dos apariciones no bastan para llamar fijo a algo",
               lambda: advisor.podria_ser_gasto_fijo("Netflix", mes="2026-09"),
               esperar=lambda r: r["respuesta_corta"] != iq.RESPUESTA_SI,
               mostrar=lambda r: f"{r['respuesta_corta']} :: {r['razon'][:110]}")
        probar("Mes sin datos no produce conclusiones del mes",
               lambda: advisor.estado_financiero("2027-05"),
               esperar=lambda r: ("no puedo concluir" in r["titular"].lower()
                                  or "no hay movimientos" in r["titular"].lower()),
               mostrar=lambda r: r["titular"][:140])
    finally:
        db.DB_PATH = original
        iq.invalidar_contexto()


# ---------------------------------------------------------------------------
# 11. Contratos existentes y capa de servicio
# ---------------------------------------------------------------------------

def probar_contratos() -> None:
    seccion("11. Contratos existentes y capa de servicio")
    for nombre in ("analizar_finanzas", "evaluar_gasto", "simular_escenario", "responder_pregunta",
                   "presupuesto_seguro", "proyectar_deuda", "calendario_financiero",
                   "score_salud_financiera", "comparacion_mensual", "plan_financiero_mes",
                   "responder_compra_inteligente", "simular_priorizacion", "fondo_emergencia",
                   "confianza_datos", "simular"):
        probar(f"financial_advisor.{nombre} sigue existiendo",
               lambda n=nombre: getattr(advisor, n), esperar=callable)
    probar("analizar_finanzas mantiene su contrato",
           lambda: advisor.analizar_finanzas(MES),
           esperar=lambda r: "recomendaciones" in r and "flujo" in r,
           mostrar=lambda r: f"{len(r['recomendaciones'])} recomendaciones")
    probar("evaluar_gasto heredado sigue respondiendo",
           lambda: advisor.evaluar_gasto(MES, 100_000),
           esperar=lambda r: r["estado"] in ("si", "no", "cuidado"))
    probar("simular_escenario heredado sigue respondiendo",
           lambda: advisor.simular_escenario(MES, "gasto", 100_000),
           esperar=lambda r: r["modifica_base"] is False)
    probar("presupuesto_seguro heredado sigue respondiendo",
           lambda: advisor.presupuesto_seguro(MES),
           esperar=lambda r: "puede_gastar_hasta" in r)

    servicio = FinanceService()
    metodos = ("estado_financiero", "plan_de_accion", "riesgo_financiero", "oportunidades",
               "hallazgos_agrupados", "cotejar_gastos_fijos", "inteligencia_tarjetas",
               "comparar_tarjetas", "inteligencia_ahorro", "explicacion_estructurada",
               "detectar_gastos_fijos", "evaluar_asequibilidad", "opciones_de_pago",
               "escenario_detallado", "analisis_pareja", "comparar_meses", "tendencias",
               "informe_integridad", "preguntar_asesor", "conversar", "dashboard", "asesor")
    for nombre in metodos:
        probar(f"FinanceService.{nombre}", lambda n=nombre: getattr(servicio, n), esperar=callable)
    probar("FinanceService ejecuta el asesor completo",
           lambda: servicio.estado_financiero(MES),
           esperar=lambda r: r["hallazgos"] is not None,
           mostrar=lambda r: f"{len(r['hallazgos'])} hallazgos, riesgo {r['riesgo']['nivel']}")
    probar("FinanceService conversa con sesión",
           lambda: servicio.conversar(MES, "¿podemos gastar 200.000?",
                                      sesion=servicio.nueva_sesion_asesor(MES)),
           esperar=lambda r: r["intencion"] == "affordability")
    probar("Los métodos de escritura invalidan la caché del asesor",
           lambda: getattr(FinanceService.crear_gasto, "_invalida_cache", False),
           esperar=lambda r: r is True)
    probar("Los métodos de lectura no fueron envueltos",
           lambda: getattr(FinanceService.dashboard, "_invalida_cache", False),
           esperar=lambda r: r is False)


def main() -> None:
    db.init_db()
    print(f"Base analizada: {db.DB_PATH}")
    print(f"Mes de análisis: {MES}")
    probar_montos()
    probar_contexto()
    probar_estado()
    probar_gastos_fijos()
    probar_dominios()
    probar_decisiones()
    probar_viajes()
    probar_conversacion()
    probar_integridad()
    probar_casos_limite()
    probar_contratos()

    print(f"\n\n{'=' * 70}\nRESUMEN\n{'=' * 70}")
    print(f"Casos ejecutados: {len(OK) + len(FALLOS)} | OK: {len(OK)} | FALLOS: {len(FALLOS)}")
    for nombre, error in FALLOS:
        print(f"  FALLO {nombre}: {error}")
    sys.exit(1 if FALLOS else 0)


if __name__ == "__main__":
    main()
