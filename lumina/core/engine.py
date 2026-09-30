"""Motor determinístico de contexto, diagnóstico, proyección y auditoría.

No persiste datos ni sustituye los cálculos del dominio. Reúne resultados de
``calculations`` para que las reglas financieras estén centralizadas,
explicables y puedan evolucionar sin crear otra fuente de verdad.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from math import ceil
from statistics import median, pstdev
from typing import Any

from . import calculations as calc
from . import database as db
from .motor.deuda import proyectar_una_deuda, simular_cascada
from .motor.dinero import cop, porcentaje_a_decimal
from ..constants import SAMUEL, SARA, PERSONAS_VALIDAS, PRIORIDAD_DISCRECIONAL, validar_mes, validar_persona


# Único lugar para modificar los umbrales del asesor. Son referencias de
# producto, no reglas universales de finanzas personales.
from .motor.reglas import reglas

RULES = reglas()


def previous_month(month: str) -> str:
    year, number = (int(value) for value in month.split("-"))
    return f"{year - 1:04d}-12" if number == 1 else f"{year:04d}-{number - 1:02d}"


def _month_index(month: str) -> int:
    year, number = (int(value) for value in month.split("-"))
    return year * 12 + number


def _add_months(base: dt.date, months: int) -> dt.date:
    index = base.year * 12 + base.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    last_day = (dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(year, month, min(base.day, last_day))


def _normalized_expense_name(value: str) -> str:
    """Normaliza sin mezclar comercios distintos ni depender de paquetes externos."""
    return " ".join("".join(char if char.isalpha() or char.isspace() else " " for char in value.casefold()).split())


def detect_recurring_expenses(*, as_of_month: str | None = None) -> list[dict[str, Any]]:
    """Detecta gastos mensuales repetidos con evidencia, nunca los convierte en obligaciones.

    Exige tres apariciones en meses diferentes, montos cercanos y huecos
    mensuales razonables. El resultado es una observación para revisar, no una
    orden de cancelar ni una transacción futura creada artificialmente.
    """
    cutoff = _month_index(as_of_month) if as_of_month else None
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for expense in db.get_gastos():
        if cutoff is not None and _month_index(expense["mes"]) > cutoff:
            continue
        normalized = _normalized_expense_name(expense["nombre"])
        if normalized:
            groups[(normalized, expense["categoria"])].append(expense)
    result: list[dict[str, Any]] = []
    minimum = int(RULES["recurring_min_occurrences"])
    tolerance = float(RULES["recurring_amount_tolerance"])
    history = int(RULES["recurring_history_months"])
    for (normalized, category), items in groups.items():
        items = sorted(items, key=lambda item: (item["mes"], item.get("fecha") or "", item["id"]))[-history:]
        months = sorted({item["mes"] for item in items})
        if len(months) < minimum:
            continue
        values = [int(item["valor"]) for item in items]
        average = sum(values) / len(values)
        if not average or any(abs(value - average) / average > tolerance for value in values):
            continue
        indices = [_month_index(month) for month in months]
        if any(later - earlier > 2 for earlier, later in zip(indices, indices[1:])):
            continue
        p1, p2 = sum(item["monto_p1"] for item in items), sum(item["monto_p2"] for item in items)
        person = "Samuel y Sara" if p1 and p2 else "Samuel" if p1 else "Sara"
        dates = [dt.date.fromisoformat(item["fecha"]) for item in items if item.get("fecha")]
        day = round(sum(value.day for value in dates) / len(dates)) if dates else None
        confidence = "Alta" if len(months) >= 5 and len(months) == len(items) else "Media"
        result.append({"nombre": items[-1]["nombre"], "nombre_normalizado": normalized,
                       "monto_promedio": round(average), "veces_detectado": len(months),
                       "frecuencia": "mensual", "total_mensual_estimado": round(average),
                       "confianza": confidence, "persona": person, "categoria": category,
                       "meses": months, "dia_estimado": day,
                       "nota": "Patrón detectado; confirma que siga vigente antes de tratarlo como compromiso."})
    return sorted(result, key=lambda item: (-item["total_mensual_estimado"], item["nombre"].casefold()))


def forecast_60_days(month: str, *, today: dt.date | None = None) -> dict[str, Any]:
    """Proyección determinista limitada a evidencia registrada.

    Solo proyecta gastos recurrentes y fechas de tarjeta conocidas. Los
    ingresos no tienen fecha diaria en el modelo actual, por lo que se declaran
    como información faltante en lugar de inventar su llegada.
    """
    reference = today or dt.date.today()
    horizon = reference + dt.timedelta(days=int(RULES["forecast_days"]))
    events: list[dict[str, Any]] = []
    for recurring in detect_recurring_expenses(as_of_month=month):
        day = recurring.get("dia_estimado")
        if not day:
            continue
        for offset in range(0, 3):
            candidate_month = _add_months(reference.replace(day=1), offset)
            max_day = (_add_months(candidate_month.replace(day=1), 1) - dt.timedelta(days=1)).day
            candidate = candidate_month.replace(day=min(day, max_day))
            if reference < candidate <= horizon:
                events.append({"fecha": candidate.isoformat(), "tipo": "Gasto recurrente estimado",
                               "titulo": recurring["nombre"], "monto": recurring["monto_promedio"],
                               "confianza": recurring["confianza"], "origen": "patrón mensual registrado"})
    for card in calc.resumen_tarjetas():
        if not card["saldo_deuda"] or not card.get("fecha_pago"):
            continue
        due = dt.date.fromisoformat(card["fecha_pago"])
        for offset in range(0, 3):
            candidate = _add_months(due, offset)
            if reference < candidate <= horizon:
                events.append({"fecha": candidate.isoformat(), "tipo": "Pago mínimo de tarjeta",
                               "titulo": card["nombre"], "monto": min(card["pago_minimo"], card["saldo_deuda"]),
                               "confianza": "Alta", "origen": "fecha y mínimo registrados"})
    events.sort(key=lambda item: (item["fecha"], -item["monto"], item["titulo"]))
    base = calc.patrimonio_liquido(month)["disponible_gastos_recurrentes"]
    running, minimum, maximum = base, base, base
    path: list[dict[str, Any]] = []
    for event in events:
        running -= int(event["monto"])
        minimum, maximum = min(minimum, running), max(maximum, running)
        path.append({"fecha": event["fecha"], "caja_estimada": running, "evento": event["titulo"]})
    income_dates_known = False  # ingresos actuales se almacenan por mes, no por día.
    evidence = len([event for event in events if event["confianza"] == "Alta"])
    confidence = "Media" if events and income_dates_known else "Baja" if events else "Baja"
    return {"desde": reference.isoformat(), "hasta": horizon.isoformat(), "eventos": events,
            "caja_inicial": base, "caja_minima_estimada": minimum, "caja_maxima_estimada": maximum,
            "trayectoria": path, "confianza": confidence, "ingresos_con_fecha": income_dates_known,
            "nota": "No proyecto ingresos por día porque LÚMINA solo los registra por mes. Las fechas y montos estimados no son hechos ni generan movimientos."}


def savings_fatigue(month: str) -> dict[str, Any]:
    """Detecta ahorro agresivo solo cuando reduce de verdad el margen operativo."""
    months = [value for value in activity_months() if value <= month][-int(RULES["savings_fatigue_months"]):]
    rows: list[dict[str, Any]] = []
    for period in months:
        flow = calc.flujo_caja_mes(period)
        deposits = sum(item["monto"] for item in db.get_movimientos_ahorro(mes=period) if item["tipo"] == "DEPOSITO")
        operating = max(int(flow["ingresos"]) - int(flow["salidas"]), 0)
        ratio = deposits / operating if operating else 0.0
        rows.append({"mes": period, "aportes": deposits, "margen_operativo": operating, "proporcion": ratio})
    threshold = float(RULES["savings_fatigue_ratio"])
    consecutive = len(rows) == int(RULES["savings_fatigue_months"]) and all(row["proporcion"] > threshold for row in rows)
    free = calc.patrimonio_liquido(month)["disponible_gastos_recurrentes"]
    normal = sum(row["margen_operativo"] for row in rows) / len(rows) if rows else 0
    enough_margin = free >= normal * float(RULES["savings_fatigue_min_free_months"])
    funds: dict[str, int] = defaultdict(int)
    for period in months:
        for movement in db.get_movimientos_ahorro(mes=period):
            if movement["tipo"] == "DEPOSITO":
                fund = next((item for item in calc.resumen_ahorros()["fondos"] if item["id"] == movement["ahorro_id"]), None)
                if fund: funds[fund["nombre"]] += movement["monto"]
    return {"activa": consecutive and not enough_margin, "meses": rows, "umbral": threshold,
            "margen_libre": free, "margen_suficiente": enough_margin,
            "cajita_principal": max(funds, key=funds.get) if funds else None,
            "nota": "Es una señal preventiva: ahorrar no se penaliza; solo se avisa si el ritmo reduce el margen registrado."}


def unified_debt_payoff(monthly_budget: int, strategy: str = "avalancha", *, start_month: str | None = None) -> dict[str, Any]:
    """Proyecta todas las tarjetas activas usando el motor único de deuda."""
    if strategy not in ("avalancha", "snowball"):
        raise ValueError("La estrategia debe ser 'avalancha' o 'snowball'.")
    if not isinstance(monthly_budget, int) or monthly_budget < 0:
        raise ValueError("El presupuesto mensual debe ser un entero no negativo.")
    filas = []
    for card in calc.resumen_tarjetas(solo_activas=True):
        if int(card["saldo_deuda"]) <= 0:
            continue
        filas.append({
            "id": card["id"],
            "nombre": card["nombre"],
            "saldo": cop(card["saldo_deuda"]),
            "tasa_mensual": porcentaje_a_decimal(card.get("interes_mensual", 0)),
            "minimo": cop(card.get("pago_minimo", 0)),
        })
    estrategia = "avalancha" if strategy == "avalancha" else "bola_de_nieve"
    resultado = simular_cascada(filas, monthly_budget, estrategia, start_month=start_month)
    cards = []
    for item in resultado["detalle"]:
        cards.append({
            "id": item["id"], "nombre": item["nombre"], "saldo": item["saldo"],
            "tasa": float(item["tasa_mensual"]), "pago_minimo": item["minimo"],
            "interes": item["intereses"], "pagado": item["pagado"],
        })
    return {
        "strategy": strategy,
        "months": resultado["meses"],
        "debt_free_date": resultado["fecha_libre"],
        "initial_debt": resultado["deuda_inicial"],
        "total_interest": resultado["intereses_proyectados"],
        "monthly_budget": resultado["presupuesto_mensual"],
        "minimums": resultado["minimos"],
        "cards": cards,
        "viable": resultado["viable"],
        "confidence": "Media" if all(x["tasa_mensual"] > 0 for x in filas) else "Baja",
        "note": resultado.get("nota", ""),
    }

def activity_months() -> list[str]:
    """Meses observables; solo usa registros activos que ya filtra la base."""
    return sorted({x["mes"] for x in db.get_ingresos()} | {x["mes"] for x in db.get_gastos()} |
                  {x["mes"] for x in db.get_pagos_deuda()} | {x["mes"] for x in db.get_movimientos_ahorro()})


def _months_in_window(last_month: str, months: int) -> list[str]:
    """Devuelve una ventana calendario ordenada, inclusiva y sin huecos ocultos."""
    if not isinstance(months, int) or months <= 0:
        raise ValueError("La cantidad de meses debe ser un entero positivo.")
    year, number = (int(value) for value in validar_mes(last_month).split("-"))
    periods: list[str] = []
    for offset in range(months - 1, -1, -1):
        index = year * 12 + number - 1 - offset
        period_year, period_number = divmod(index, 12)
        periods.append(f"{period_year:04d}-{period_number + 1:02d}")
    return periods


def ingresos_historicos(persona: str, meses: int = 12, *, as_of_month: str | None = None) -> dict[str, Any]:
    """Resume ingresos reales de una persona sin convertir faltantes en ingresos.

    Los meses sin filas se conservan explícitamente como faltantes. El modelo
    actual guarda ingresos por mes, no por día, así que la fecha de llegada se
    declara desconocida en vez de estimarla como si fuera un dato confirmado.
    """
    persona = validar_persona(persona)
    reference = validar_mes(as_of_month or dt.date.today().strftime("%Y-%m"))
    periods = _months_in_window(reference, meses)
    totals: dict[str, int] = defaultdict(int)
    for row in db.get_ingresos():
        if row["persona"] == persona and row["mes"] in periods:
            totals[row["mes"]] += int(row["valor"])
    observed = [{"mes": period, "monto": totals[period], "origen": "confirmado"}
                for period in periods if period in totals]
    missing = [period for period in periods if period not in totals]
    values = [item["monto"] for item in observed]
    average = round(sum(values) / len(values)) if values else 0
    middle = round(median(values)) if values else 0
    deviation = round(pstdev(values), 2) if len(values) > 1 else 0.0
    variation = round(deviation / average, 4) if average else None

    if len(values) < 3:
        trend = "insuficientes_datos"
    else:
        # Regresión lineal sobre el orden cronológico real; nunca sobre los montos ordenados.
        positions = list(range(len(values)))
        mean_x = sum(positions) / len(positions)
        mean_y = sum(values) / len(values)
        denominator = sum((position - mean_x) ** 2 for position in positions)
        slope = sum((position - mean_x) * (value - mean_y) for position, value in zip(positions, values)) / denominator
        tolerance = max(average * 0.02, 1)
        trend = "creciente" if slope > tolerance else "decreciente" if slope < -tolerance else "estable"

    outliers = []
    if len(values) >= 3:
        base = middle
        for item in observed:
            if base and abs(item["monto"] - base) / base >= 0.50:
                outliers.append(item["mes"])
    if not values:
        confidence, reason = "Desconocida", "No hay ingresos confirmados de esta persona en el período solicitado."
    elif len(values) == 1:
        confidence, reason = "Baja", "Solo hay un mes confirmado; no alcanza para inferir un patrón."
    elif len(values) == 2 or variation is None or variation > 0.30 or outliers:
        confidence, reason = "Baja", "El historial es corto o variable; el promedio solo sirve como referencia, no como promesa."
    elif len(values) >= 3 and not missing and variation <= 0.15:
        confidence, reason = "Alta", "Hay al menos tres meses consecutivos y los montos son consistentes."
    else:
        confidence, reason = "Media", "Hay varios ingresos confirmados, pero existen meses faltantes o variación moderada."

    names = {SAMUEL: "Samuel", SARA: "Sara"}
    current = totals.get(reference, 0)
    return {
        "persona": persona,
        "persona_nombre": names.get(persona, persona),
        "ingreso_promedio": average,
        "ingreso_mediana": middle,
        "ingreso_minimo": min(values) if values else 0,
        "ingreso_maximo": max(values) if values else 0,
        "desviacion": deviation,
        "coeficiente_variacion": variation,
        "tendencia": trend,
        "meses_con_datos": len(values),
        "meses_solicitados": meses,
        "confianza": confidence,
        "razon_confianza": reason,
        "datos_mes_a_mes": observed,
        "dia_estimado_llegada": None,
        "fecha_estimada_proximo_ingreso": None,
        "siguiente_ingreso_estimado": middle or None,
        "ingreso_actual_confirmado": current or None,
        "dato_faltante_mes_actual": reference not in totals,
        "datos_incompletos": missing,
        "outliers": outliers,
        "nota": "Las fechas de llegada no se estiman porque SQLite registra ingresos por mes, no por día. El siguiente ingreso es una estimación basada en la mediana y no reemplaza un ingreso confirmado.",
    }


def snapshot_database() -> str:
    """Huella completa de SQLite para proteger escenarios de cualquier escritura."""
    with db.get_conn() as conn:
        content = "\n".join(conn.iterdump()).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _distribucion_hipotetica(monto: int, responsabilidad: str | None) -> tuple[int, int]:
    if responsabilidad is None or responsabilidad == "compartido":
        return monto // 2, monto - monto // 2
    persona = validar_persona(responsabilidad)
    return (monto, 0) if persona == SAMUEL else (0, monto)


def _estado_tarjeta_despues_compra(tarjeta: dict[str, Any], monto: int) -> dict[str, Any]:
    deuda = int(tarjeta["saldo_deuda"]) + monto
    cupo = int(tarjeta["cupo_total"])
    uso = deuda / cupo if cupo else 1.0
    return {"tarjeta_id": tarjeta["id"], "tarjeta_nombre": tarjeta["nombre"],
            "deuda_antes": tarjeta["saldo_deuda"], "deuda_despues": deuda,
            "cupo_antes": tarjeta["cupo_disponible"], "cupo_despues": cupo - deuda,
            "utilizacion_antes": tarjeta["utilizacion"], "utilizacion_despues": uso,
            "pago_minimo_antes": tarjeta["pago_minimo"], "pago_minimo_despues": None,
            "puede_pagar_minimo_registrado": None,
            "fecha_pago": tarjeta.get("fecha_pago"),
            "interes_mensual": tarjeta["interes_mensual"],
            "estado": "crítica" if uso >= float(RULES["card_use_critical"]) else "alta" if uso >= float(RULES["card_use_high"]) else "atención" if uso >= float(RULES["card_use_attention"]) else "saludable",
            "nota_pago_minimo": "El mínimo futuro no se estima: depende de la política y extracto del banco, no de una regla inventada."}


def _consecuencias_segundo_orden(*, caja_despues: int, margen: int, flujo_despues: int,
                                 impacto_tarjeta: dict[str, Any] | None, pagos_minimos: int) -> list[str]:
    consequences: list[str] = []
    if caja_despues < 0:
        consequences.append("🔴 La compra dejaría la caja libre en negativo; requeriría usar una reserva o nueva deuda.")
    elif caja_despues < margen:
        consequences.append("🟠 La compra es posible, pero dejaría menos margen que el colchón de seguridad registrado.")
    if flujo_despues < 0:
        consequences.append("🟠 El flujo del mes quedaría en déficit; habría que reducir otros gastos, ahorro previsto o esperar un ingreso confirmado.")
    if impacto_tarjeta:
        use = impacto_tarjeta["utilizacion_despues"]
        if use >= float(RULES["card_use_critical"]):
            consequences.append(f"🔴 {impacto_tarjeta['tarjeta_nombre']} quedaría en {use:.0%} de uso, con muy poco cupo para imprevistos.")
        elif use >= float(RULES["card_use_high"]):
            consequences.append(f"🟠 {impacto_tarjeta['tarjeta_nombre']} quedaría en {use:.0%} de uso; la próxima obligación sería más apretada.")
        if pagos_minimos and flujo_despues < pagos_minimos:
            consequences.append("⚠️ El flujo estimado no alcanza a cubrir los mínimos de tarjeta ya registrados.")
    return consequences


def simular_compra(monto: int, mes: str, concepto: str = "compra", *, quien_paga: str | None = None,
                   responsabilidad: str | None = None, usar_tarjeta: int | None = None) -> dict[str, Any]:
    """Simula una compra sobre una foto de solo lectura; nunca modifica SQLite.

    ``quien_paga`` representa quién desembolsa efectivo; ``responsabilidad``
    representa quién asume el consumo. Si no se declara responsabilidad se usa
    50/50 y se reporta como una suposición, para no confundir ambas dimensiones.
    """
    if not isinstance(monto, int) or monto <= 0:
        raise ValueError("El monto debe ser un entero positivo.")
    mes = validar_mes(mes)
    concepto = str(concepto or "compra").strip() or "compra"
    payer = validar_persona(quien_paga) if quien_paga else None
    if responsabilidad is not None and responsabilidad != "compartido":
        responsabilidad = validar_persona(responsabilidad)
    before_db = snapshot_database()
    state = financial_state(mes)
    # La caja real no se obtiene de liquidez económica: una compra con tarjeta
    # ya reduce responsabilidad, pero todavía no ha salido dinero del banco.
    cash_before = sum(item["caja"] for item in calc.caja_real_por_persona().values()) - int(state["savings"]["total"])
    economic_before = int(state["liquidity"]["available"])
    flow_before = int(state["flow"]["ahorro"])
    margin = int(state["liquidity"]["security_margin"])
    cards = state["debt"]["cards"]
    responsibility_p1, responsibility_p2 = _distribucion_hipotetica(monto, responsabilidad)
    card_impact = None
    card = None
    if usar_tarjeta is not None:
        card = next((item for item in cards if item["id"] == usar_tarjeta), None)
        if card is None:
            raise ValueError("La tarjeta seleccionada no existe.")
        card_impact = _estado_tarjeta_despues_compra(card, monto)
    cash_impact = 0 if card else -monto
    cash_after = cash_before + cash_impact
    # Compras de tarjeta no son salida de caja hasta que haya un pago real.
    flow_after = flow_before + cash_impact
    debt_after = int(state["debt"]["cards_total"]) + (monto if card else 0)
    balance = state["couple_historical"]
    paid_p1 = monto if not card and payer == SAMUEL else 0
    paid_p2 = monto if not card and payer == SARA else 0
    balance_p1_after = int(balance[f"balance_neto_{SAMUEL}"]) + paid_p1 - responsibility_p1
    balance_p2_after = int(balance[f"balance_neto_{SARA}"]) + paid_p2 - responsibility_p2
    if card_impact:
        card_impact["puede_pagar_minimo_registrado"] = int(card_impact["pago_minimo_antes"]) <= max(flow_before, 0)
        card_impact["capacidad_pago_futuro"] = max(flow_before - int(state["flow"]["minimum_card_payments"]), 0)
    alternatives: list[dict[str, Any]] = []
    reference_person = responsabilidad if responsabilidad in PERSONAS_VALIDAS else payer
    if reference_person:
        history = ingresos_historicos(reference_person, as_of_month=mes)
        estimate = history["siguiente_ingreso_estimado"]
        if estimate and history["confianza"] in {"Alta", "Media"}:
            alternatives.append({"tipo": "esperar_ingreso", "opcion": f"Esperar al próximo ingreso estimado de {history['persona_nombre']}",
                                 "monto_estimado": estimate, "caja_estimada_despues": cash_after + estimate,
                                 "confianza": history["confianza"],
                                 "razon": "No hay fecha diaria registrada; el monto es una mediana histórica, no un ingreso confirmado."})
    needed = max(margin - cash_after, 0)
    discretionary = int(state["flow"]["discretionary_expenses"])
    if needed and discretionary:
        alternatives.append({"tipo": "reducir_discrecionales", "opcion": "Reducir gasto discrecional este mes",
                             "recorte_necesario": min(needed, discretionary), "disponible_registrado": discretionary,
                             "razon": "Ese recorte recuperaría el margen de seguridad sin tocar deuda ni ahorro reservado.", "confianza": "Alta"})
    if card:
        options = [item for item in cards if item["id"] != card["id"] and item["cupo_disponible"] >= monto and item["interes_mensual"] < card["interes_mensual"]]
        if options:
            option = min(options, key=lambda item: (item["interes_mensual"], item["utilizacion"]))
            alternatives.append({"tipo": "otra_tarjeta", "opcion": f"Usar {option['nombre']}", "interes_mensual": option["interes_mensual"],
                                 "utilizacion_estimada": (option["saldo_deuda"] + monto) / option["cupo_total"],
                                 "ahorro_interes_mensual_estimado": round((card["interes_mensual"] - option["interes_mensual"]) * monto / 100),
                                 "razon": "Alternativa calculada con cupo y tasa actualmente registrados.", "confianza": "Media"})
    horizon = 30
    if card and card.get("fecha_pago"):
        due = dt.date.fromisoformat(card["fecha_pago"])
        horizon = max(30, min(90, (due - dt.date.today()).days if due >= dt.date.today() else 60))
    consequences = _consecuencias_segundo_orden(caja_despues=cash_after, margen=margin, flujo_despues=flow_after,
                                                  impacto_tarjeta=card_impact, pagos_minimos=int(state["flow"]["minimum_card_payments"]))
    possible = cash_after >= 0 and (not card_impact or card_impact["cupo_despues"] >= 0)
    safe = possible and cash_after >= margin and (not card_impact or card_impact["utilizacion_despues"] < float(RULES["card_use_high"])) and flow_after >= 0
    label = "🟢 Puedes hacerlo" if safe else "🟡 Puedes hacerlo, pero..." if possible and not consequences else "🟠 Yo esperaría" if possible else "🔴 No lo recomiendo"
    result = {"modifica_base": False, "integridad_sqlite": True, "concepto": concepto, "monto": monto,
              "posible": possible, "recomendacion": label, "horizonte_dias": horizon,
              "suposiciones": (["La responsabilidad se distribuyó 50/50 porque no se indicó una distribución."] if responsabilidad is None else []),
              "estado_actual": {"caja_disponible": cash_before, "liquidez_economica": economic_before, "ahorro_mensual": flow_before, "deuda_total_tarjetas": state["debt"]["cards_total"], "margen_seguridad": margin},
              "estado_despues": {"caja_disponible": cash_after, "liquidez_economica": economic_before - monto, "caja_suficiente": cash_after >= margin, "ahorro_mensual": flow_after, "deuda_total_tarjetas": debt_after},
              "impacto_tarjeta": card_impact, "alternativas": alternatives, "consecuencias": consequences,
              "impacto_pareja": {"saldo_samuel_antes": balance[f"balance_neto_{SAMUEL}"], "saldo_sara_antes": balance[f"balance_neto_{SARA}"],
                                  "saldo_samuel_despues": balance_p1_after, "saldo_sara_despues": balance_p2_after,
                                  "pagador": payer, "responsabilidad_samuel": responsibility_p1, "responsabilidad_sara": responsibility_p2}}
    after_db = snapshot_database()
    if before_db != after_db:
        raise AssertionError("Fallo crítico: una simulación intentó modificar SQLite.")
    return result


def simular_priorizacion_tarjetas(mes: str, presupuesto_disponible: int, diferir: list[int],
                                  priorizar: list[int] | None = None) -> dict[str, Any]:
    """Simula diferir unas tarjetas y concentrar pagos en otras, sin escribir.

    Los intereses estimados se muestran solo para las tarjetas diferidas. No
    se calculan multas ni mora: dependen del contrato y del extracto de cada
    entidad, datos que LÚMINA no almacena.
    """
    mes = validar_mes(mes)
    if isinstance(presupuesto_disponible, bool) or not isinstance(presupuesto_disponible, int) or presupuesto_disponible < 0:
        raise ValueError("El presupuesto disponible debe ser un entero no negativo.")
    priorizacion_explicita = priorizar is not None
    if not isinstance(diferir, list) or (priorizar is not None and not isinstance(priorizar, list)):
        raise ValueError("Diferir y priorizar deben ser listas de IDs de tarjetas.")
    priorizar = [] if priorizar is None else priorizar
    for etiqueta, ids in (("diferir", diferir), ("priorizar", priorizar)):
        if any(isinstance(tarjeta_id, bool) or not isinstance(tarjeta_id, int) for tarjeta_id in ids):
            raise ValueError(f"Los IDs de {etiqueta} deben ser enteros.")
        if len(ids) != len(set(ids)):
            raise ValueError(f"No repitas tarjetas en {etiqueta}.")
    if set(diferir) & set(priorizar):
        raise ValueError("Una tarjeta no puede diferirse y priorizarse a la vez.")

    before_db = snapshot_database()
    tarjetas_activas = calc.resumen_tarjetas(solo_activas=True)
    por_id = {tarjeta["id"]: tarjeta for tarjeta in tarjetas_activas}
    solicitadas = set(diferir) | set(priorizar)
    inexistentes = sorted(solicitadas - set(por_id))
    if inexistentes:
        raise ValueError(f"No existe una tarjeta activa con ID: {', '.join(map(str, inexistentes))}.")
    tarjetas = [tarjeta for tarjeta in tarjetas_activas if int(tarjeta["saldo_deuda"]) > 0]
    sin_deuda = sorted(tarjeta_id for tarjeta_id in solicitadas if not int(por_id[tarjeta_id]["saldo_deuda"]))
    if sin_deuda:
        raise ValueError(f"Estas tarjetas no tienen deuda para simular: {', '.join(map(str, sin_deuda))}.")

    diferidas_ids = set(diferir)
    pagables = [tarjeta for tarjeta in tarjetas if tarjeta["id"] not in diferidas_ids]
    minimos_necesarios = sum(min(int(tarjeta["pago_minimo"]), int(tarjeta["saldo_deuda"])) for tarjeta in pagables)
    posible = presupuesto_disponible >= minimos_necesarios
    advertencia_diferir = ("Diferir el pago mínimo puede generar mora, reporte a centrales o cargos del banco. "
                           "LÚMINA no los calcula porque no tiene la política de esa entidad.")
    diferidas: list[dict[str, Any]] = []
    for tarjeta in tarjetas:
        if tarjeta["id"] not in diferidas_ids:
            continue
        saldo = int(tarjeta["saldo_deuda"])
        interes = round(saldo * float(tarjeta["interes_mensual"]) / 100)
        diferidas.append({"tarjeta_id": tarjeta["id"], "tarjeta_nombre": tarjeta["nombre"],
                          "saldo_actual": saldo, "interes_generado_este_mes": interes,
                          "saldo_estimado_proximo_mes": saldo + interes, "advertencia": advertencia_diferir})

    pagos: list[dict[str, Any]] = []
    if posible:
        restante = presupuesto_disponible
        por_pago: dict[int, dict[str, Any]] = {}
        for tarjeta in pagables:
            saldo = int(tarjeta["saldo_deuda"])
            minimo = min(int(tarjeta["pago_minimo"]), saldo)
            fila = {"tarjeta_id": tarjeta["id"], "tarjeta_nombre": tarjeta["nombre"], "pago_minimo": minimo,
                    "pago_extra_asignado": 0, "pago_total": minimo, "saldo_despues": saldo - minimo,
                    "utilizacion_despues": (saldo - minimo) / int(tarjeta["cupo_total"]) if tarjeta["cupo_total"] else 0.0}
            pagos.append(fila)
            por_pago[tarjeta["id"]] = fila
            restante -= minimo
        objetivo_ids = priorizar if priorizacion_explicita else [tarjeta["id"] for tarjeta in sorted(
            pagables, key=lambda tarjeta: (-float(tarjeta["interes_mensual"]), -int(tarjeta["saldo_deuda"]), tarjeta["id"])
        )]
        for tarjeta_id in objetivo_ids:
            fila = por_pago.get(tarjeta_id)
            if fila is None or restante <= 0:
                continue
            extra = min(restante, int(fila["saldo_despues"]))
            fila["pago_extra_asignado"] = extra
            fila["pago_total"] += extra
            fila["saldo_despues"] -= extra
            tarjeta = por_id[tarjeta_id]
            fila["utilizacion_despues"] = fila["saldo_despues"] / int(tarjeta["cupo_total"]) if tarjeta["cupo_total"] else 0.0
            restante -= extra
        presupuesto_usado = presupuesto_disponible - restante
        presupuesto_sobrante = restante
    else:
        # Sin alcanzar todos los mínimos no suponemos qué banco acepta un pago parcial.
        for tarjeta in pagables:
            saldo = int(tarjeta["saldo_deuda"])
            minimo = min(int(tarjeta["pago_minimo"]), saldo)
            pagos.append({"tarjeta_id": tarjeta["id"], "tarjeta_nombre": tarjeta["nombre"], "pago_minimo": minimo,
                          "pago_extra_asignado": 0, "pago_total": 0, "saldo_despues": saldo,
                          "utilizacion_despues": tarjeta["utilizacion"]})
        presupuesto_usado = 0
        presupuesto_sobrante = presupuesto_disponible

    consecuencias: list[str] = []
    if not posible:
        consecuencias.append(f"🔴 Faltan {minimos_necesarios - presupuesto_disponible:,} COP para cubrir los mínimos de las tarjetas no diferidas.")
    for tarjeta in diferidas:
        consecuencias.append(f"⚠️ {tarjeta['tarjeta_nombre']} sumaría aproximadamente {tarjeta['interes_generado_este_mes']:,} COP de interés este mes si se difiere.")
    for tarjeta in pagos:
        if tarjeta["utilizacion_despues"] >= float(RULES["card_use_high"]):
            consecuencias.append(f"🟠 {tarjeta['tarjeta_nombre']} seguiría en {tarjeta['utilizacion_despues']:.0%} de utilización después del pago simulado.")
    result = {"modifica_base": False, "integridad_sqlite": True, "mes": mes, "posible": posible,
              "tarjetas_diferidas": diferidas, "tarjetas_pagadas": pagos,
              "presupuesto_disponible": presupuesto_disponible, "minimos_necesarios": minimos_necesarios,
              "deficit_minimos": max(minimos_necesarios - presupuesto_disponible, 0),
              "presupuesto_usado": presupuesto_usado, "presupuesto_sobrante": presupuesto_sobrante,
              "interes_total_generado_tarjetas_diferidas": sum(tarjeta["interes_generado_este_mes"] for tarjeta in diferidas),
              "consecuencias": consecuencias,
              "nota": "Este simulador no conoce las políticas de mora de cada banco; verifiquen directamente con la entidad antes de diferir un pago mínimo real."}
    after_db = snapshot_database()
    if before_db != after_db:
        raise AssertionError("Fallo crítico: una simulación intentó modificar SQLite.")
    return result


def income_reference(month: str, person: str | None = None) -> dict[str, Any]:
    """Referencia conservadora de ingresos confirmados, sin inventar meses.

    Con menos de dos meses no proyecta: devuelve la pregunta concreta que el
    Advisor puede hacer. Con historial suficiente expone promedio, rango y un
    valor conservador (el menor ingreso reciente), no un promedio ciego.
    """
    if person:
        rows = [item for item in db.get_ingresos() if item["persona"] == person and item["mes"] <= month]
    else:
        rows = [item for item in db.get_ingresos() if item["mes"] <= month]
    by_month: dict[str, int] = defaultdict(int)
    for item in rows:
        by_month[item["mes"]] += int(item["valor"])
    history = [{"mes": key, "monto": by_month[key], "origen": "confirmado"} for key in sorted(by_month)]
    values = [item["monto"] for item in history]
    current = by_month.get(month, 0)
    if not values:
        return {"estado": "desconocido", "historial": [], "ingreso_actual_confirmado": 0,
                "referencia_conservadora": None, "confianza": "Baja",
                "pregunta": "Para proyectar con realismo, ¿cuánto dinero esperan recibir normalmente al mes?"}
    if len(values) == 1:
        return {"estado": "parcial", "historial": history, "ingreso_actual_confirmado": current,
                "referencia_conservadora": values[0], "promedio": values[0], "minimo": values[0], "maximo": values[0],
                "confianza": "Baja", "pregunta": "Solo tengo un mes confirmado. ¿Cuánto recibieron el mes anterior y hace dos meses?"}
    recent = values[-3:]
    trend = "subiendo" if len(recent) >= 2 and recent[-1] > recent[-2] else "bajando" if len(recent) >= 2 and recent[-1] < recent[-2] else "estable"
    return {"estado": "historico", "historial": history, "ingreso_actual_confirmado": current,
            "referencia_conservadora": min(recent), "promedio": round(sum(recent) / len(recent)),
            "minimo": min(recent), "maximo": max(recent), "tendencia": trend,
            "confianza": "Alta" if len(values) >= 3 else "Media", "pregunta": None}


def data_quality(month: str) -> dict[str, Any]:
    months = activity_months()
    complete = [period for period in months if db.get_ingresos(period) and db.get_gastos(period)]
    cards = calc.resumen_tarjetas()
    missing = []
    if not db.get_ingresos(month): missing.append("ingresos del mes")
    if not db.get_gastos(month): missing.append("gastos del mes")
    if any(card["saldo_deuda"] and not card["interes_mensual"] for card in cards): missing.append("tasas de interés de alguna deuda")
    if any(card["saldo_deuda"] and not card.get("fecha_pago") for card in cards): missing.append("fechas de pago de alguna deuda")
    if any(card.get("saldo_historico_pendiente") for card in cards): missing.append("origen de deuda histórica")
    if len(complete) >= int(RULES["history_months_high_confidence"]) and not missing:
        level = "Alta"
    elif db.get_ingresos(month) and db.get_gastos(month):
        level = "Media"
    else:
        level = "Baja"
    return {"level": level, "months_recorded": len(months), "complete_months": len(complete), "missing": missing,
            "current_month_partial": month == dt.date.today().isoformat()[:7] and dt.date.today().day < 25,
            "explanation": "La confianza refleja cantidad, consistencia y datos faltantes; no mide certeza sobre el futuro."}


def emergency_fund(month: str) -> dict[str, Any]:
    funds = calc.resumen_ahorros()["fondos"]
    fund = next((item for item in funds if "emerg" in item["nombre"].casefold()), None)
    mandatory = sum(item["valor"] for item in db.get_gastos(month) if item["prioridad"] != PRIORIDAD_DISCRECIONAL)
    if not mandatory:
        mandatory = sum(item["valor"] for item in db.get_gastos(previous_month(month)) if item["prioridad"] != PRIORIDAD_DISCRECIONAL)
    current = fund["saldo"] if fund else 0
    target_months = int(RULES["emergency_target_months"])
    target = (fund["meta"] if fund and fund["meta"] else mandatory * target_months)
    deposits = 0
    if fund:
        deposits = sum(x["monto"] if x["tipo"] == "DEPOSITO" else -x["monto"] for x in db.get_movimientos_ahorro(fund["id"], month))
    return {"found": fund is not None, "fund_id": fund["id"] if fund else None,
            "name": fund["nombre"] if fund else "Fondo de emergencia", "current": current, "target": target,
            "monthly_base": mandatory, "coverage_months": current / mandatory if mandatory else 0.0,
            "target_months": target_months, "shortfall": max(target - current, 0),
            "progress": min(current / target, 1.0) if target else 0.0, "monthly_net_deposit": deposits,
            "assumption": "Se identifica solo una cajita nombrada como emergencia; la referencia es su meta o tres meses de gastos obligatorios registrados."}


def financial_state(month: str, person: str | None = None) -> dict[str, Any]:
    """Foto financiera normalizada, de solo lectura, para pareja o una persona."""
    if person:
        personal = calc.dashboard_personal(person, month)
        return {"scope": person, "month": month, "liquidity": {"available": personal["liquidez"], "free": personal["liquidez"] - personal["ahorrado"]},
                "flow": {"income": personal["ingresos"], "expenses": personal["gastos"], "savings_capacity": personal["capacidad_ahorro"], "card_payments": personal["pagos_tarjeta"]},
                "debt": {"total": personal["deuda"], "cards": personal["tarjetas"]}, "savings": {"total": personal["ahorrado"], "funds": personal["cajitas"], "goals": personal["metas"]},
                "data_quality": data_quality(month)}
    flow = calc.flujo_caja_mes(month); liquidity = calc.liquidez_por_persona(month); cards = calc.resumen_tarjetas(); external = calc.resumen_deudas_terceros()
    savings = calc.resumen_ahorros(); goals = calc.progreso_metas(); assets = calc.patrimonio_liquido(month)
    balance = calc.balance_historico_pareja(month); balance_historical = calc.balance_historico_pareja()
    mandatory = sum(x["valor"] for x in db.get_gastos(month) if x["prioridad"] != PRIORIDAD_DISCRECIONAL)
    discretionary = sum(x["valor"] for x in db.get_gastos(month) if x["prioridad"] == PRIORIDAD_DISCRECIONAL)
    minimums = sum(min(card["pago_minimo"], card["saldo_deuda"]) for card in cards)
    margin = round(flow["ingresos"] * float(RULES["safe_margin_income_pct"]))
    fixed_expenses = calc.resumen_gastos_fijos(month)
    return {"scope": "pareja", "month": month,
            "liquidity": {"by_person": liquidity, "available": calc.liquidez_total(month),
                          "accumulated": assets["liquidez_operativa"], "reserved": savings["total"],
                          "free": assets["disponible_gastos_recurrentes"], "security_margin": margin},
            "flow": {**flow, "mandatory_expenses": mandatory, "discretionary_expenses": discretionary, "minimum_card_payments": minimums},
            "debt": {"total": sum(card["saldo_deuda"] for card in cards) + external["por_pagar"], "cards_total": sum(card["saldo_deuda"] for card in cards),
                      "external_payable": external["por_pagar"], "external_receivable": external["por_cobrar"], "external": external,
                      "cards": cards, "interest_estimated": sum(card["interes_estimado"] for card in cards)},
            "savings": {"total": savings["total"], "funds": savings["fondos"], "goals": goals, "emergency": emergency_fund(month)},
            "wealth": assets, "fixed_expenses": fixed_expenses, "couple": balance, "couple_historical": balance_historical,
            "data_quality": data_quality(month), "income_reference": income_reference(month),
            "income_history": {persona: ingresos_historicos(persona, as_of_month=month) for persona in PERSONAS_VALIDAS},
            "reconciliation": {persona: calc.reconciliar_saldo_personal(persona, month) for persona in PERSONAS_VALIDAS}}


def debt_projection(card_id: int, extra_payment: int = 0) -> dict[str, Any]:
    """Compara mínimo y mínimo+extra usando el motor único de dinero."""
    card = next((item for item in calc.resumen_tarjetas() if item["id"] == card_id), None)
    if card is None:
        raise ValueError("Tarjeta no encontrada.")
    if extra_payment < 0:
        raise ValueError("El pago adicional no puede ser negativo.")
    balance = cop(card["saldo_deuda"])
    rate = porcentaje_a_decimal(card.get("interes_mensual", 0))
    minimum = min(max(cop(card.get("pago_minimo", 0)), 0), balance)
    current_months, current_interest = proyectar_una_deuda(balance, rate, minimum)
    first_interest = __import__("lumina.core.motor.dinero", fromlist=["interes_cop"]).interes_cop(balance, rate)
    improved_payment = min(balance + first_interest, minimum + cop(extra_payment))
    improved_months, improved_interest = proyectar_una_deuda(balance, rate, improved_payment)
    return {
        "card_id": card_id, "card": card["nombre"], "balance": balance,
        "monthly_rate": card["interes_mensual"], "minimum_payment": minimum,
        "extra_payment": cop(extra_payment), "improved_payment": improved_payment,
        "months_current": current_months, "months_improved": improved_months,
        "months_saved": max((current_months or 0) - (improved_months or 0), 0) if current_months else None,
        "interest_current": current_interest, "interest_improved": improved_interest,
        "interest_saved": max(current_interest - improved_interest, 0),
        "confidence": "Media" if card.get("interes_mensual") and minimum else "Baja",
        "assumption": "Tasa mensual y pagos constantes; no hay compras nuevas, cargos, seguros ni cambios de tasa.",
    }

def _card_monthly_delta(card: dict[str, Any], month: str) -> dict[str, Any]:
    activity = calc.resumen_mensual_tarjeta(card["id"], month)
    net = activity["compras"] + activity["intereses"] + activity["cargos"] - activity["pagos"]
    return {"activity": activity, "net_growth": net, "payments_cover_purchases": activity["pagos"] >= activity["compras"] + activity["intereses"] + activity["cargos"]}


def monthly_trends(month: str) -> dict[str, Any]:
    months = [value for value in activity_months() if value <= month][-4:]
    expenses = [int(calc.flujo_caja_mes(value)["salidas"]) for value in months]
    savings = [int(calc.flujo_caja_mes(value)["ahorro"]) for value in months]
    return {"months": months, "expenses": expenses, "savings": savings,
            "expenses_rising_three_months": len(expenses) >= 3 and expenses[-3] < expenses[-2] < expenses[-1],
            "savings_falling_three_months": len(savings) >= 3 and savings[-3] > savings[-2] > savings[-1]}


def anomalies(month: str) -> list[dict[str, Any]]:
    prior = [value for value in activity_months() if value < month][-3:]
    if not prior: return []
    current: dict[str, int] = defaultdict(int); history: dict[str, list[int]] = defaultdict(list)
    for item in db.get_gastos(month): current[item["categoria"]] += item["valor"]
    for period in prior:
        totals: dict[str, int] = defaultdict(int)
        for item in db.get_gastos(period): totals[item["categoria"]] += item["valor"]
        for category in set(current) | set(totals): history[category].append(totals[category])
    result = []
    for category, amount in current.items():
        average = sum(history[category]) / len(history[category]) if history[category] else 0
        if average and amount >= average * float(RULES["anomaly_multiplier"]):
            result.append({"category": category, "current": amount, "average": round(average), "multiplier": amount / average,
                           "message": "Es una señal para revisar si hubo un evento extraordinario o cambió el comportamiento; no se asume que sea un error."})
    return sorted(result, key=lambda item: item["current"], reverse=True)


def audit_integrity(month: str | None = None) -> dict[str, Any]:
    """Controles de solo lectura para detectar inconsistencias antes de aconsejar."""
    issues: list[dict[str, Any]] = []
    try:
        balance = calc.balance_historico_pareja()
        if not balance["cuadra"]: issues.append({"area": "pareja", "severity": "critical", "detail": "El balance de pareja no cuadra."})
    except Exception as exc:
        issues.append({"area": "pareja", "severity": "critical", "detail": str(exc)})
    for card in calc.resumen_tarjetas():
        known = (
            sum(item["valor_pendiente"] for item in db.get_compras_tarjeta(card["id"]))
            + sum(item["valor_pendiente"] for item in db.get_ajustes_tarjeta(card["id"]) if item["estado"] == "ACTIVO" and item["variacion"] > 0)
            + card["saldo_historico_pendiente"]
        )
        if card["saldo_deuda"] < 0 or card["saldo_deuda"] > card["cupo_total"]:
            issues.append({"area": "tarjeta", "severity": "critical", "detail": f"{card['nombre']} tiene saldo o cupo inconsistente."})
        if known > card["saldo_deuda"]:
            issues.append({"area": "tarjeta", "severity": "attention", "detail": f"{card['nombre']} tiene movimientos pendientes por encima del saldo registrado."})
    bd = db.verificar_bd_integridad()
    if not bd.get("ok"):
        issues.append({"area": "base_datos", "severity": "critical", "detail": "La auditoría de SQLite detectó inconsistencias; revisar detalles antes de operar."})
    for goal in calc.progreso_metas():
        if goal["actual"] > goal["monto_objetivo"] and not goal["cumplida"]:
            issues.append({"area": "meta", "severity": "attention", "detail": f"La meta {goal['nombre']} requiere revisión de progreso."})
    today = dt.date.today().isoformat()
    signatures: set[tuple[str, int, str, int | None]] = set()
    for expense in db.get_gastos():
        if expense.get("fecha") and expense["fecha"] > today:
            issues.append({"area": "fechas", "severity": "attention", "detail": f"El gasto «{expense['nombre']}» tiene fecha futura ({expense['fecha']})."})
        if expense["categoria"].strip().casefold() in {"sin categoria", "sin categoría", "desconocida", "unknown"}:
            issues.append({"area": "categorias", "severity": "attention", "detail": f"El gasto «{expense['nombre']}» necesita una categoría más específica."})
        signature = (expense["fecha"] or expense["mes"], expense["valor"], expense["nombre"].strip().casefold(), expense.get("tarjeta_id"))
        if signature in signatures:
            issues.append({"area": "duplicados", "severity": "attention", "detail": f"Posible gasto duplicado: «{expense['nombre']}» por {expense['valor']}. No se modificó automáticamente."})
        signatures.add(signature)
    for reversal in db.get_reversiones_tarjeta():
        if reversal["tipo_original"] == "PAGO":
            original = next((item for item in db.get_pagos_deuda(incluir_reversados=True) if item["id"] == reversal["original_id"]), None)
            expected_sign = 1
        else:
            original = next((item for item in db.get_compras_tarjeta(incluir_reversadas=True) if item["id"] == reversal["original_id"]), None)
            expected_sign = -1
        if original is None or original["estado"] != "REVERSADO" or reversal["monto_inverso"] * expected_sign <= 0:
            issues.append({"area": "reversiones", "severity": "critical", "detail": f"La reversa de tarjeta #{reversal['id']} no tiene un original activo/reversado consistente."})
    for reversal in db.verificar_reversiones_movimientos():
        issues.append({"area": "reversiones", "severity": "critical", "detail": f"La reversa #{reversal['reversion_id']} no tiene un original reversado consistente."})
    return {"ok": not issues, "issues": issues, "month": month,
            "checked": ["ingresos/gastos activos", "tarjetas y pendientes", "ahorros derivados", "metas", "balance de pareja", "fechas, categorías y duplicados", "reversiones de tarjeta y movimientos"]}


def diagnose(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Diagnósticos estructurados: observación → causa → riesgo → acción."""
    if state["scope"] != "pareja": return []
    flow, liquidity, debt, savings = state["flow"], state["liquidity"], state["debt"], state["savings"]
    findings: list[dict[str, Any]] = []
    def add(priority: str, code: str, title: str, evidence: dict[str, Any], cause: str, risk: str, action: str, impact: int = 0) -> None:
        findings.append({"priority": priority, "code": code, "title": title, "evidence": evidence, "cause": cause,
                         "risk": risk, "action": action,
                         "alternatives": ["Reducir el monto", "Reprogramar la decisión", "Compensar con un recorte o ingreso confirmado"],
                         "if_ignored": risk, "expected_result": "Protege liquidez y reduce exposición según los datos registrados.", "impact": impact})
    if flow["ahorro"] < 0:
        add("critical", "cash_deficit", "El flujo del mes está en déficit", {"income": flow["ingresos"], "outflows": flow["salidas"], "deficit": -flow["ahorro"]}, "Las salidas de caja superan los ingresos registrados.", "El déficit puede empujar gastos esenciales hacia crédito o retiros de reserva.", "Pausa gasto discrecional y cubre primero obligaciones mínimas.", -flow["ahorro"])
    elif liquidity["free"] < flow["minimum_card_payments"]:
        add("high", "committed_liquidity", "La liquidez libre no cubre todos los mínimos", {"free": liquidity["free"], "minimums": flow["minimum_card_payments"]}, "Las reservas y pagos mínimos consumen el efectivo disponible.", "Un próximo pago puede requerir crédito adicional o comprometer una cajita.", "Reserva los mínimos antes de comprometer una compra nueva.", flow["minimum_card_payments"] - liquidity["free"])
    fixed = state.get("fixed_expenses")
    if fixed and fixed["porcentaje_ingreso"] is not None and fixed["porcentaje_ingreso"] >= float(RULES.get("fixed_expenses_high_pct", 0.5)):
        add("high", "fixed_expenses_high", "Los gastos fijos comprometen la mayor parte del ingreso",
            {"total_mensual": fixed["total_mensual_equivalente"], "porcentaje": fixed["porcentaje_ingreso"]},
            "Las obligaciones recurrentes activas equivalen a una porción alta del ingreso mensual registrado.",
            "Deja poco margen para imprevistos, ahorro o pagos adicionales de deuda.",
            "Revisa cuáles gastos fijos pueden renegociarse o eliminarse antes de asumir nuevos compromisos.",
            fixed["total_mensual_equivalente"])
    if fixed and fixed["pendiente_este_mes"] > 0 and fixed["cantidad_activos"]:
        add("medium", "fixed_expenses_pending", "Hay gastos fijos del mes aún sin registrar como pagados",
            {"pendiente": fixed["pendiente_este_mes"], "total": fixed["total_mensual_equivalente"]},
            "El compromiso mensual de gastos fijos activos no coincide con lo ya registrado como gasto este mes.",
            "El disponible mostrado puede parecer mayor de lo real si ese pago todavía no ocurre.",
            "Registra el pago cuando ocurra o confirma si ya se hizo con otro nombre.", fixed["pendiente_este_mes"])
    if debt["external_payable"]:
        add("medium", "external_debt", "Hay una obligación pendiente con terceros", {"por_pagar": debt["external_payable"], "por_cobrar": debt["external_receivable"]}, "Existe una cuenta externa activa registrada.", "Puede presionar la liquidez aunque no sea una deuda de tarjeta.", "Define fecha y prioridad de pago con el banco o persona correspondiente.", debt["external_payable"])
    for card in debt["cards"]:
        detail = _card_monthly_delta(card, state["month"])
        if card["utilizacion"] >= float(RULES["card_use_critical"]):
            add("critical", "card_critical", f"{card['nombre']} tiene utilización crítica", {"utilization": card["utilizacion"], "balance": card["saldo_deuda"], "limit": card["cupo_total"]}, "El saldo ocupa la mayor parte del cupo registrado.", "Reduce el margen para imprevistos y puede aumentar dependencia del crédito.", "Evita compras nuevas y prioriza un abono adicional después del mínimo.", card["saldo_deuda"])
        elif card["utilizacion"] >= float(RULES["card_use_high"]):
            add("high", "card_high", f"{card['nombre']} tiene utilización alta", {"utilization": card["utilizacion"], "balance": card["saldo_deuda"]}, "La deuda representa más de 70% del cupo registrado.", "Una compra nueva deja menos margen y aumenta el riesgo de intereses.", "Define un pago adicional antes del próximo corte.", card["saldo_deuda"])
        if detail["net_growth"] > 0:
            add("high" if card["utilizacion"] >= .5 else "medium", "card_growing", f"La deuda de {card['nombre']} está creciendo", {**detail, "balance": card["saldo_deuda"]}, "Compras, intereses y cargos superaron pagos del período.", "Mantener este patrón prolonga deuda y costo financiero.", "Compara un abono adicional con las nuevas compras discrecionales.", detail["net_growth"])
        if card["saldo_deuda"] and not card["interes_mensual"]:
            add("medium", "missing_rate", f"Falta la tasa de {card['nombre']}", {"balance": card["saldo_deuda"]}, "No hay tasa mensual registrada.", "No se puede cuantificar con confianza el costo de mantener la deuda.", "Registra la tasa del extracto antes de elegir una estrategia de pago.")
    emergency = savings["emergency"]
    if emergency["found"] and emergency["coverage_months"] < float(RULES["minimum_emergency_months"]):
        add("high", "emergency_low", "El fondo de emergencia cubre menos de un mes", emergency, "La reserva registrada es baja frente al gasto obligatorio.", "Un imprevisto puede terminar en tarjeta o retiro de una meta.", "Crea un aporte recurrente pequeño mientras cubres mínimos de deuda.", emergency["shortfall"])
    required_goals = sum(int(item["ahorro_necesario_mensual"] or 0) for item in savings["goals"] if not item["cumplida"])
    if required_goals > max(flow["ahorro"], 0):
        add("medium", "goals_compete", "Las metas compiten por el mismo flujo", {"required_monthly": required_goals, "available_flow": max(flow["ahorro"], 0), "goals": len(savings["goals"])}, "Los aportes requeridos superan la capacidad de ahorro registrada.", "No todas las metas llegarán a tiempo con el ritmo actual.", "Prioriza una o dos metas y ajusta fechas o aportes de las demás.", required_goals - max(flow["ahorro"], 0))
    if debt["interest_estimated"] and emergency["shortfall"] and flow["ahorro"] > 0:
        add("medium", "debt_vs_savings", "Hay que equilibrar reserva y deuda", {"estimated_interest": debt["interest_estimated"], "emergency_shortfall": emergency["shortfall"], "flow": flow["ahorro"]}, "Hay deuda con costo estimado y una reserva incompleta.", "Dirigir todo a un solo destino puede dejar expuesta una necesidad importante.", "Mantén un colchón mínimo y dirige el excedente a la tarjeta con mayor tasa.")
    trends = monthly_trends(state["month"])
    if trends["expenses_rising_three_months"]:
        add("medium", "expenses_rising", "Los gastos suben tres meses seguidos", {"months": trends["months"], "expenses": trends["expenses"]}, "Las salidas registradas muestran una secuencia ascendente.", "Si continúa, puede reducir capacidad de ahorro aunque el ingreso no cambie.", "Revisa las categorías que crecieron antes de ampliar compromisos.")
    for item in anomalies(state["month"]):
        add("low", "spending_anomaly", f"{item['category']} está por encima de su patrón", item, "El gasto actual supera el promedio reciente de la categoría.", "Puede ser extraordinario o una tendencia que conviene entender.", "Confirma si fue un evento puntual antes de cambiar el presupuesto.", item["current"])
    return sorted(findings, key=lambda item: ({"critical": 0, "high": 1, "medium": 2, "low": 3, "positive": 4}[item["priority"]], -item["impact"]))
