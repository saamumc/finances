"""Financial OS: planificación integral desde deuda hasta patrimonio e inversión.

Este módulo reúne cálculos de alto nivel sobre las fuentes de verdad existentes.
No cambia el aspecto de la aplicación y no crea movimientos al calcular.
Dinero en COP entero; las tasas de inversión se expresan en puntos básicos.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from . import calculations as calc
from . import database as db
from .motor.deuda import simular_cascada


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _money(value: Any) -> int:
    return max(0, int(value or 0))


def dinero(value: Any) -> str:
    """Formatea un monto COP para mensajes del Financial OS."""
    return f"${_money(value):,}".replace(",", ".")


def resumen_inversiones() -> dict[str, Any]:
    """Foto derivada de inversiones activas y sus movimientos."""
    with db.get_conn() as conn:
        inversiones = [dict(row) for row in conn.execute(
            "SELECT * FROM inversiones WHERE activa=1 ORDER BY nombre COLLATE NOCASE"
        ).fetchall()]
        movimientos = [dict(row) for row in conn.execute(
            """SELECT mi.*, i.nombre, i.clase, i.titular
               FROM movimientos_inversion mi
               JOIN inversiones i ON i.id=mi.inversion_id
               WHERE i.activa=1 AND mi.estado='ACTIVO'
               ORDER BY mi.fecha, mi.id"""
        ).fetchall()]
    detalle = []
    total_aportes = total_retiros = total_valoraciones = 0
    for inv in inversiones:
        rows = [m for m in movimientos if m["inversion_id"] == inv["id"]]
        aportes = sum(_money(m["monto"]) for m in rows if m["tipo"] == "APORTE")
        retiros = sum(_money(m["monto"]) for m in rows if m["tipo"] == "RETIRO")
        valoraciones = sum(int(m["monto"] or 0) for m in rows if m["tipo"] == "VALORACION")
        capital_neto = aportes - retiros
        valor_actual = capital_neto + valoraciones
        total_aportes += aportes
        total_retiros += retiros
        total_valoraciones += valoraciones
        detalle.append({
            "id": inv["id"], "nombre": inv["nombre"], "clase": inv["clase"],
            "entidad": inv["entidad"], "titular": inv["titular"],
            "riesgo": inv["riesgo"], "liquidez": inv["liquidez"],
            "aportes": aportes, "retiros": retiros, "valoraciones": valoraciones,
            "capital_neto": capital_neto, "valor_actual": valor_actual,
            "rentabilidad_absoluta": valoraciones,
        })
    valor = sum(x["valor_actual"] for x in detalle)
    rentabilidad = total_valoraciones
    return {
        "total": valor, "aportes": total_aportes, "retiros": total_retiros,
        "valoraciones": total_valoraciones, "rentabilidad_absoluta": rentabilidad,
        "inversiones": detalle,
        "cantidad": len(detalle),
        "nota": "La rentabilidad mostrada es la variación registrada como VALORACION; no se inventa una tasa cuando faltan fechas o precios."
    }


def crear_inversion(*, nombre: str, clase: str, titular: str, riesgo: str,
                    liquidez: str = "media", entidad: str | None = None,
                    horizonte_meses: int | None = None,
                    comision_pb_anual: int = 0,
                    fecha_apertura: str | None = None,
                    fecha_vencimiento: str | None = None) -> int:
    clases = {"cdt", "fic", "etf", "accion", "bono", "efectivo", "pension", "otro"}
    if clase not in clases:
        raise ValueError("Clase de inversión no válida.")
    if titular not in {"persona1", "persona2", "compartido"}:
        raise ValueError("Titular no válido.")
    if riesgo not in {"bajo", "medio", "alto"}:
        raise ValueError("Riesgo no válido.")
    if liquidez not in {"alta", "media", "baja"}:
        raise ValueError("Liquidez no válida.")
    if horizonte_meses is not None and horizonte_meses <= 0:
        raise ValueError("El horizonte debe ser positivo.")
    if comision_pb_anual < 0:
        raise ValueError("La comisión no puede ser negativa.")
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO inversiones
               (nombre, clase, entidad, titular, horizonte_meses, liquidez, riesgo,
                comision_pb_anual, fecha_apertura, fecha_vencimiento, activa, creado_en, transaction_uuid)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (nombre.strip(), clase, entidad, titular, horizonte_meses, liquidez, riesgo,
             comision_pb_anual, fecha_apertura or dt.date.today().isoformat(),
             fecha_vencimiento, _now(), db._new_uuid()),
        )
        return int(cur.lastrowid)


def registrar_movimiento_inversion(inversion_id: int, tipo: str, monto: int,
                                   fecha: str | None = None) -> int:
    if tipo not in {"APORTE", "RETIRO", "VALORACION"}:
        raise ValueError("Tipo de movimiento de inversión no válido.")
    if monto < 0 or (tipo != "VALORACION" and monto == 0):
        raise ValueError("El monto no es válido.")
    with db.get_conn() as conn:
        existe = conn.execute(
            "SELECT id FROM inversiones WHERE id=? AND activa=1", (inversion_id,)
        ).fetchone()
        if existe is None:
            raise ValueError("La inversión no existe o está inactiva.")
        cur = conn.execute(
            """INSERT INTO movimientos_inversion
               (inversion_id, fecha, tipo, monto, estado, transaction_uuid)
               VALUES (?,?,?,?, 'ACTIVO', ?)""",
            (inversion_id, fecha or dt.date.today().isoformat(), tipo, int(monto), db._new_uuid()),
        )
        return int(cur.lastrowid)


def desactivar_inversion(inversion_id: int) -> None:
    with db.get_conn() as conn:
        cur = conn.execute("UPDATE inversiones SET activa=0 WHERE id=? AND activa=1", (inversion_id,))
        if cur.rowcount == 0:
            raise ValueError("La inversión no existe o ya está inactiva.")


def _otras_deudas() -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM deudas WHERE activa=1 AND saldo>0 ORDER BY saldo DESC, id"
        ).fetchall()
    return [dict(r) for r in rows]

def registrar_deuda(*, acreedor: str, tipo: str, titular: str, saldo: int,
                    tasa_ea_pb: int = 0, pago_minimo: int = 0,
                    dia_pago: int | None = None, fecha_fin: str | None = None) -> int:
    if saldo < 0 or tasa_ea_pb < 0 or pago_minimo < 0:
        raise ValueError("Saldo, tasa y pago mínimo no pueden ser negativos.")
    if titular not in {"persona1", "persona2", "compartido"}:
        raise ValueError("Titular no válido.")
    if not acreedor.strip() or not tipo.strip():
        raise ValueError("Acreedor y tipo son obligatorios.")
    if dia_pago is not None and not 1 <= dia_pago <= 31:
        raise ValueError("Día de pago inválido.")
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO deudas
               (acreedor, tipo, titular, saldo, tasa_ea_pb, pago_minimo, pago_actual,
                dia_pago, fecha_fin, activa, creado_en, transaction_uuid)
               VALUES (?,?,?,?,?,?,0,?,?,1,?,?)""",
            (acreedor.strip(), tipo.strip(), titular, int(saldo), int(tasa_ea_pb),
             int(pago_minimo), dia_pago, fecha_fin, _now(), db._new_uuid()),
        )
        return int(cur.lastrowid)


def resumen_deudas() -> dict[str, Any]:
    tarjetas = [x for x in calc.resumen_tarjetas(solo_activas=True) if x["saldo_deuda"] > 0]
    otras = _otras_deudas()
    tarjeta_total = sum(int(x["saldo_deuda"]) for x in tarjetas)
    otras_total = sum(int(x["saldo"]) for x in otras)
    return {
        "total": tarjeta_total + otras_total,
        "tarjetas": tarjetas,
        "otras": otras,
        "tarjetas_total": tarjeta_total,
        "otras_total": otras_total,
        "cantidad": len(tarjetas) + len(otras),
    }


def _deuda_integral_rows() -> list[dict[str, Any]]:
    """Normaliza tarjetas y deudas estructuradas en una sola vista de pago."""
    rows = []
    for card in calc.resumen_tarjetas(solo_activas=True):
        if int(card["saldo_deuda"]) > 0:
            rows.append({
                "id": f"tarjeta:{card['id']}", "origen": "tarjeta", "nombre": card["nombre"],
                "saldo": int(card["saldo_deuda"]),
                "tasa_mensual": max(0.0, float(card.get("interes_mensual") or 0) / 100),
                "minimo": min(int(card.get("pago_minimo") or 0), int(card["saldo_deuda"])),
            })
    for debt in _otras_deudas():
        rows.append({
            "id": f"deuda:{debt['id']}", "origen": "deuda", "nombre": f"{debt['acreedor']} · {debt['tipo']}",
            "saldo": int(debt["saldo"]),
            "tasa_mensual": max(0.0, float(debt.get("tasa_ea_pb") or 0) / 10000 / 12),
            "minimo": min(int(debt.get("pago_minimo") or 0), int(debt["saldo"])),
        })
    return rows


def _simular_deuda_integral(mensual_disponible: int, estrategia: str) -> dict[str, Any]:
    if estrategia not in {"avalancha", "bola_de_nieve"}:
        raise ValueError("Estrategia no válida.")
    budget = max(0, int(mensual_disponible))
    rows = _deuda_integral_rows()
    initial = sum(x["saldo"] for x in rows)
    minimums = sum(x["minimo"] for x in rows)
    if not rows:
        return {"estrategia": estrategia, "viable": True, "meses": 0, "deuda_inicial": 0,
                "intereses_proyectados": 0, "presupuesto_mensual": budget, "minimos": 0,
                "fecha_libre": dt.date.today().strftime("%Y-%m"), "detalle": []}
    if budget < minimums:
        return {"estrategia": estrategia, "viable": False, "meses": None, "deuda_inicial": initial,
                "intereses_proyectados": 0, "presupuesto_mensual": budget, "minimos": minimums,
                "fecha_libre": None, "detalle": rows,
                "nota": "El presupuesto no alcanza los mínimos registrados; no se inventa una fecha de salida."}
    work = [dict(x) for x in rows]
    total_interest = 0
    months = 0
    while any(x["saldo"] > 0 for x in work) and months < 600:
        months += 1
        for item in work:
            if item["saldo"] > 0 and item["tasa_mensual"] > 0:
                interest = int(round(item["saldo"] * item["tasa_mensual"]))
                item["saldo"] += interest
                item["intereses"] = item.get("intereses", 0) + interest
                total_interest += interest
        available = budget
        for item in work:
            payment = min(item["minimo"], item["saldo"])
            item["saldo"] -= payment
            item["pagado"] = item.get("pagado", 0) + payment
            available -= payment
        active = [x for x in work if x["saldo"] > 0]
        if estrategia == "avalancha":
            active.sort(key=lambda x: (-x["tasa_mensual"], -x["saldo"], x["id"]))
        else:
            active.sort(key=lambda x: (x["saldo"], -x["tasa_mensual"], x["id"]))
        for item in active:
            if available <= 0:
                break
            extra = min(available, item["saldo"])
            item["saldo"] -= extra
            item["pagado"] = item.get("pagado", 0) + extra
            available -= extra
    viable = not any(x["saldo"] > 0 for x in work)
    start = dt.date.today().replace(day=1)
    free_date = None
    if viable:
        index = start.year * 12 + start.month - 1 + months
        year, month = divmod(index, 12)
        free_date = f"{year:04d}-{month + 1:02d}"
    return {
        "estrategia": estrategia, "viable": viable, "meses": months if viable else None,
        "deuda_inicial": initial, "intereses_proyectados": total_interest,
        "presupuesto_mensual": budget, "minimos": minimums, "fecha_libre": free_date,
        "detalle": work,
        "nota": "Simulación matemática con saldos y tasas registradas. No incluye cargos, compras futuras ni cambios de tasa."
    }


def plan_deuda_integral(mensual_disponible: int) -> dict[str, Any]:
    """Planifica tarjetas y otras deudas en una sola cascada."""
    return {
        "presupuesto_mensual": max(0, int(mensual_disponible)),
        "deuda_total": sum(x["saldo"] for x in _deuda_integral_rows()),
        "avalancha": _simular_deuda_integral(mensual_disponible, "avalancha"),
        "bola_de_nieve": _simular_deuda_integral(mensual_disponible, "bola_de_nieve"),
        "ordenes": {
            "avalancha": "Mayor tasa mensual primero.",
            "bola_de_nieve": "Menor saldo primero.",
        },
    }


def trayectoria_patrimonio(month: str, meses: int = 12) -> dict[str, Any]:
    """Construye una trayectoria comparable usando únicamente datos disponibles.

    Algunas vistas patrimoniales del motor son acumuladas/globales, por lo que
    no deben repetirse como si fueran saldos históricos mensuales. Para evitar
    presentar una falsa serie histórica, cada punto conserva solo magnitudes
    que pueden reconstruirse de forma segura por período. El patrimonio neto
    se muestra únicamente para el mes de referencia.
    """
    if meses <= 0 or meses > 60:
        raise ValueError("El horizonte debe estar entre 1 y 60 meses.")
    year, number = (int(x) for x in month.split("-"))
    rows = []
    for offset in range(meses - 1, -1, -1):
        index = year * 12 + number - 1 - offset
        y, m = divmod(index, 12)
        period = f"{y:04d}-{m + 1:02d}"
        flujo = calc.flujo_caja_mes(period)
        liquidez = calc.liquidez_total(period)
        ahorro_mes = calc.ahorro_mensual(period)
        is_reference = period == month
        patrimonio = calc.patrimonio_liquido(period) if is_reference else None
        rows.append({
            "mes": period,
            "patrimonio_neto": int(patrimonio["patrimonio_liquido"]) if patrimonio is not None else None,
            "liquidez": int(liquidez),
            "ahorro": int(ahorro_mes),
            "deuda": int(resumen_deudas()["total"]) if is_reference else None,
            "inversion": int(resumen_inversiones()["total"]) if is_reference else None,
            "referencia_actual": is_reference,
            "ingresos": int(flujo.get("ingresos", 0)),
            "salidas": int(flujo.get("salidas", 0)),
        })
    cambios = []
    for anterior, actual in zip(rows, rows[1:]):
        variacion = None
        if anterior["patrimonio_neto"] is not None and actual["patrimonio_neto"] is not None:
            variacion = actual["patrimonio_neto"] - anterior["patrimonio_neto"]
        cambios.append({"desde": anterior["mes"], "hasta": actual["mes"],
                        "variacion_patrimonio": variacion})
    return {"mes_referencia": month, "meses": rows, "variaciones": cambios,
            "nota": "La serie histórica solo muestra como patrimonio las magnitudes que pueden reconstruirse sin inventar saldos. Los meses anteriores al de referencia exponen flujo, liquidez y ahorro del período; el patrimonio actual se muestra únicamente en el mes consultado."}


def resumen_tarjetas_operativo(month: str) -> dict[str, Any]:
    """Resume la operación mensual de tarjetas sin modificar la base de datos.

    El saldo es el saldo actual registrado por el motor. Los pagos y sus
    aportes se filtran por mes para distinguir deuda pendiente de caja pagada.
    """
    tarjetas = calc.resumen_tarjetas(solo_activas=True)
    pagos = db.get_pagos_deuda(mes=month)
    pagos_por_tarjeta: dict[int, list[dict[str, Any]]] = {}
    for pago in pagos:
        tarjeta_id = pago.get("tarjeta_id")
        if tarjeta_id is not None:
            pagos_por_tarjeta.setdefault(int(tarjeta_id), []).append(pago)

    detalle = []
    deuda_total = pago_minimo_total = pagado_mes = 0
    pagado_p1 = pagado_p2 = 0
    intereses_estimados = 0
    cupo_total = cupo_disponible = 0
    for card in tarjetas:
        saldo = _money(card.get("saldo_deuda"))
        minimo = min(_money(card.get("pago_minimo")), saldo)
        card_pagos = pagos_por_tarjeta.get(int(card["id"]), [])
        pagado = sum(_money(p.get("monto")) for p in card_pagos)
        aporte_p1 = sum(_money(p.get("monto_aportado_p1")) for p in card_pagos)
        aporte_p2 = sum(_money(p.get("monto_aportado_p2")) for p in card_pagos)
        faltante = max(0, minimo - pagado)
        interes = _money(card.get("interes_estimado"))
        intereses_estimados += interes
        deuda_total += saldo
        pago_minimo_total += minimo
        pagado_mes += pagado
        pagado_p1 += aporte_p1
        pagado_p2 += aporte_p2
        cupo_total += _money(card.get("cupo_total"))
        cupo_disponible += _money(card.get("cupo_disponible"))
        detalle.append({
            "id": int(card["id"]),
            "nombre": card.get("nombre"),
            "dueño": card.get("titular"),
            "titular": card.get("titular"),
            "deuda": saldo,
            "pago_minimo": minimo,
            "pagado_mes": pagado,
            "faltante_minimo": faltante,
            "interes_mensual": card.get("interes_mensual", 0),
            "interes_estimado": interes,
            "cupo_total": _money(card.get("cupo_total")),
            "cupo_disponible": _money(card.get("cupo_disponible")),
            "utilizacion": float(card.get("utilizacion") or 0),
            "pagado_persona1": aporte_p1,
            "pagado_persona2": aporte_p2,
        })
    return {
        "mes": month,
        "deuda_total": deuda_total,
        "pago_minimo_total": pago_minimo_total,
        "pagado_mes": pagado_mes,
        "faltante_minimos": max(0, pago_minimo_total - pagado_mes),
        "pagado_persona1": pagado_p1,
        "pagado_persona2": pagado_p2,
        "intereses_estimados": intereses_estimados,
        "cupo_total": cupo_total,
        "cupo_disponible": cupo_disponible,
        "utilizacion_global": (deuda_total / cupo_total) if cupo_total else 0.0,
        "tarjetas": detalle,
        "nota": "Lectura operativa del mes; no registra pagos, compras ni transferencias automáticamente.",
    }


def plan_mensual_deuda(month: str, disponible: int) -> dict[str, Any]:
    """Distribuye de forma informativa el margen mensual entre mínimos y deuda."""
    disponible = max(0, int(disponible))
    tarjetas = resumen_tarjetas_operativo(month)
    deudas = _deuda_integral_rows()
    total_minimos = sum(int(item["minimo"]) for item in deudas)
    cubre = disponible >= total_minimos
    return {
        "mes": month,
        "disponible": disponible,
        "total_minimos": total_minimos,
        "extra_sobre_minimos": max(0, disponible - total_minimos),
        "faltante_minimos": max(0, total_minimos - disponible),
        "cubre_minimos": cubre,
        "minimos_tarjetas": int(tarjetas["pago_minimo_total"]),
        "minimos_otras_deudas": max(0, total_minimos - int(tarjetas["pago_minimo_total"])),
        "deuda_total": int(sum(item["saldo"] for item in deudas)),
        "nota": "Plan informativo: no ejecuta pagos. El extra se puede dirigir a la deuda prioritaria después de cubrir mínimos.",
    }

def asignacion_margen(month: str, margen: int | None = None) -> dict[str, Any]:
    """Propone un presupuesto de margen por fases sin ejecutar ninguna operación."""
    flujo = calc.flujo_caja_mes(month)
    libre = max(0, int(flujo.get("ahorro", 0))) if margen is None else max(0, int(margen))
    deudas = resumen_deudas()
    emergencia = fondo_emergencia(month)
    tarjetas = resumen_tarjetas_operativo(month)
    if deudas["total"] > 0:
        prioridad = "deuda"
        objetivo = deudas["total"]
    elif emergencia["faltante_base"] > 0:
        prioridad = "emergencia"
        objetivo = emergencia["faltante_base"]
    else:
        prioridad = "patrimonio"
        objetivo = max(0, int(resumen_inversiones()["total"]))
    return {
        "mes": month, "margen_disponible": libre, "prioridad_principal": prioridad,
        "objetivo_pendiente": int(objetivo), "minimos_tarjetas": int(tarjetas["pago_minimo_total"]),
        "asignacion_sugerida": {
            "minimos_deuda": min(int(libre), sum(x["minimo"] for x in _deuda_integral_rows())),
            "extra_deuda": max(0, libre - sum(x["minimo"] for x in _deuda_integral_rows())) if deudas["total"] else 0,
            "emergencia": max(0, libre - sum(x["minimo"] for x in _deuda_integral_rows())) if not deudas["total"] and emergencia["faltante_base"] > 0 else 0,
            "inversion": libre if not deudas["total"] and emergencia["faltante_base"] <= 0 else 0,
            "gasto_discrecional": 0,
        },
        "nota": "La asignación es una propuesta de planificación; no constituye una transferencia ni un pago automático."
    }

def plan_deuda(mensual_disponible: int) -> dict[str, Any]:
    """Compara cascadas con todas las tarjetas y deudas v15 sin alterar SQLite."""
    cards = _simular_deuda_integral(max(0, int(mensual_disponible)), "avalancha")
    snow = _simular_deuda_integral(max(0, int(mensual_disponible)), "bola_de_nieve")
    return {
        "presupuesto_mensual": max(0, int(mensual_disponible)),
        "avalancha": cards,
        "bola_de_nieve": snow,
        "criterio": {
            "avalancha": "prioriza la tasa mensual más alta",
            "bola_de_nieve": "prioriza el saldo más pequeño",
        },
        "integral": plan_deuda_integral(mensual_disponible),
    }


def fondo_emergencia(month: str) -> dict[str, Any]:
    ahorro = calc.resumen_ahorros()
    fondos = ahorro.get("fondos") or []
    emergencia_fondos = [f for f in fondos if str(f.get("nombre", "")).strip().casefold() in {"emergencia", "fondo de emergencia", "fondo emergencia"}]
    current = sum(_money(f.get("saldo")) for f in emergencia_fondos)
    fijos = calc.resumen_gastos_fijos(month)
    mandatory = int(fijos.get("total_mensual_equivalente") or 0)
    coverage = (current / mandatory) if mandatory > 0 else 0.0
    # Si existe configuración v15 explícita, respétala; de lo contrario 1/3/6.
    with db.get_conn() as conn:
        cfg = conn.execute("SELECT meses_minimo, meses_base, meses_robusto FROM fondo_emergencia_config ORDER BY id DESC LIMIT 1").fetchone()
    min_m, base_m, robust_m = (int(cfg[0]), int(cfg[1]), int(cfg[2])) if cfg else (1, 3, 6)
    targets = {
        "minimo": max(0, mandatory * min_m),
        "base": max(0, mandatory * base_m),
        "robusto": max(0, mandatory * robust_m),
    }
    return {
        "actual": current, "cobertura_meses": coverage,
        "gasto_obligatorio_referencia": int(mandatory),
        "objetivos": targets,
        "faltante_base": max(0, targets["base"] - current),
        "faltante_robusto": max(0, targets["robusto"] - current),
        "nota": "Los objetivos son referencias de planificación. La cobertura existente proviene del cálculo de cajitas."
    }


def proyeccion_inversion(aporte_mensual: int, meses: int, tasa_anual_pb: int) -> dict[str, Any]:
    if aporte_mensual < 0 or meses <= 0 or tasa_anual_pb < 0:
        raise ValueError("Aporte, meses o tasa no válidos.")
    # Capitalización mensual con tasa anual nominal aproximada, redondeando cada mes en COP.
    saldo = 0
    aportado = 0
    tasa_mensual = tasa_anual_pb / 10000 / 12
    filas = []
    for mes in range(1, meses + 1):
        saldo += int(aporte_mensual)
        aportado += int(aporte_mensual)
        saldo = int(round(saldo * (1 + tasa_mensual)))
        if mes == 1 or mes % 12 == 0 or mes == meses:
            filas.append({"mes": mes, "aportado": aportado, "valor_proyectado": saldo,
                          "ganancia_proyectada": saldo - aportado})
    return {
        "aporte_mensual": int(aporte_mensual), "meses": meses,
        "tasa_anual_pb": int(tasa_anual_pb), "aportado": aportado,
        "valor_proyectado": saldo, "ganancia_proyectada": saldo - aportado,
        "evolucion": filas,
        "nota": "Escenario matemático, no rendimiento garantizado. La tasa es un supuesto introducido por el usuario."
    }


def readiness(month: str) -> dict[str, Any]:
    flujo = calc.flujo_caja_mes(month)
    patrimonio = calc.patrimonio_liquido(month)
    deuda = resumen_deudas()
    emergencia = fondo_emergencia(month)
    inversiones = resumen_inversiones()
    ahorro_total = int(calc.resumen_ahorros().get("total", 0))
    # El margen mensual se basa en el ahorro real del mes; las reservas ya acumuladas no se cuentan como ingreso disponible.
    libre = max(0, int(flujo.get("ahorro", 0)))
    razones = []
    if deuda["tarjetas_total"] > 0:
        razones.append("Hay deuda activa de tarjetas.")
    if emergencia["cobertura_meses"] < 3:
        razones.append("El fondo de emergencia registrado aún cubre menos de 3 meses.")
    if libre <= 0:
        razones.append("No hay margen operativo positivo registrado.")
    listo = not razones
    return {
        "listo_para_invertir": listo,
        "nivel": "base" if listo else "construccion",
        "margen_mensual": libre,
        "ahorro_acumulado": ahorro_total,
        "deuda_total": deuda["total"],
        "fondo_emergencia": emergencia,
        "inversiones": inversiones,
        "razones": razones,
        "flujo": flujo,
        "nota": "Es una señal de planificación basada en los datos registrados, no una garantía de que una inversión sea adecuada."
    }


def mapa_accion(month: str, presupuesto: int | None = None) -> dict[str, Any]:
    """Resume la siguiente fase financiera sin volver a invocar financial_os."""
    flujo = calc.flujo_caja_mes(month)
    libre = max(0, int(flujo.get("ahorro", 0))) if presupuesto is None else max(0, int(presupuesto))
    deudas = resumen_deudas()
    emergencia = fondo_emergencia(month)
    inversiones = resumen_inversiones()
    ahorros = calc.resumen_ahorros()
    fases = []
    if libre <= 0:
        fases.append({"fase": 1, "clave": "flujo", "estado": "bloqueado",
                      "accion": "Crear margen mensual positivo antes de aumentar obligaciones o inversiones."})
    elif deudas["total"] > 0:
        fases.append({"fase": 1, "clave": "deuda", "estado": "activa",
                      "accion": "Cubrir mínimos y dirigir el excedente a una estrategia de amortización."})
    elif emergencia["faltante_base"] > 0:
        fases.append({"fase": 1, "clave": "emergencia", "estado": "activa",
                      "accion": "Completar el fondo de emergencia base antes de aumentar riesgo."})
    else:
        fases.append({"fase": 1, "clave": "patrimonio", "estado": "activa",
                      "accion": "Asignar el margen sostenible entre metas e inversión según horizonte y liquidez."})
    if emergencia["faltante_base"] > 0:
        fases.append({"fase": 2, "clave": "emergencia", "estado": "pendiente",
                      "faltante": int(emergencia["faltante_base"])})
    if int(ahorros.get("total", 0)) > 0:
        fases.append({"fase": 3, "clave": "ahorro", "estado": "registrado",
                      "saldo": int(ahorros["total"])})
    fases.append({"fase": 4, "clave": "inversion",
                  "estado": "registrado" if inversiones["total"] > 0 else "pendiente",
                  "saldo": int(inversiones["total"])})
    return {
        "mes": month, "margen_mensual": libre, "deuda_total": int(deudas["total"]),
        "fondo_emergencia_actual": int(emergencia["actual"]),
        "inversion_actual": int(inversiones["total"]), "fases": fases,
        "nota": "Mapa informativo: no ejecuta pagos, transferencias ni inversiones."
    }


def radar_financiero(month: str) -> dict[str, Any]:
    """Radar operativo del mes: convierte la foto financiera en señales y una próxima acción.
    Es solo lectura. No modifica SQLite ni inventa datos faltantes.
    """
    flujo = calc.flujo_caja_mes(month)
    tarjetas = resumen_tarjetas_operativo(month)
    deudas = resumen_deudas()
    emergencia = fondo_emergencia(month)
    inversiones = resumen_inversiones()
    libre = max(0, int(flujo.get("ahorro", 0)))
    ingresos = max(0, int(flujo.get("ingresos", 0)))
    cupo = sum(int(x.get("cupo_total", 0)) for x in calc.resumen_tarjetas(solo_activas=True))
    deuda_tarjetas = int(tarjetas.get("deuda_total", 0))
    utilizacion = deuda_tarjetas / cupo if cupo > 0 else 0.0
    señales: list[dict[str, Any]] = []
    faltante = int(tarjetas.get("faltante_minimos", 0))
    if faltante > 0:
        señales.append({"clave":"minimos","estado":"alerta","titulo":"Mínimos de tarjetas","valor":dinero(faltante),
            "detalle":"Hay pagos mínimos registrados que todavía no están cubiertos.",
            "accion":"Cubrir los mínimos antes de enviar dinero extra a otras metas."})
    elif deuda_tarjetas > 0:
        señales.append({"clave":"minimos","estado":"ok","titulo":"Mínimos de tarjetas","valor":"cubiertos",
            "detalle":f"Pagado este mes: {dinero(tarjetas.get('pagado_mes', 0))}.",
            "accion":"El siguiente margen puede dirigirse a la deuda prioritaria."})
    else:
        señales.append({"clave":"minimos","estado":"ok","titulo":"Mínimos de tarjetas","valor":"sin deuda",
            "detalle":"No hay deuda activa de tarjetas registrada.",
            "accion":"Conservar el margen para emergencia, ahorro o patrimonio."})
    if utilizacion >= 0.80:
        uso_estado, uso_accion = "alerta", "Evitar aumentar el saldo si no es necesario y priorizar reducción."
    elif utilizacion >= 0.50:
        uso_estado, uso_accion = "atencion", "Vigilar compras nuevas y mantener la reducción de saldo."
    else:
        uso_estado, uso_accion = "ok", "Mantener el nivel de utilización bajo control."
    señales.append({"clave":"utilizacion","estado":uso_estado,"titulo":"Uso de tarjetas",
        "valor":f"{utilizacion:.0%}" if cupo else "—",
        "detalle":f"Utilización aproximada: {utilizacion:.0%} del cupo." if cupo else "No hay cupo registrado.",
        "accion":uso_accion})
    cobertura = float(emergencia.get("cobertura_meses", 0) or 0)
    faltante_em = int(emergencia.get("faltante_base", 0) or 0)
    señales.append({"clave":"emergencia","estado":("alerta" if cobertura < 1 else "atencion") if faltante_em else "ok",
        "titulo":"Fondo de emergencia","valor":f"{cobertura:.1f} meses",
        "detalle":f"Faltan {dinero(faltante_em)} para la meta base." if faltante_em else "La meta base registrada está cubierta.",
        "accion":"Reservar parte del margen mensual hasta alcanzar la meta base." if faltante_em else "Conservar la reserva y dirigir excedentes a objetivos posteriores."})
    tasa_ahorro = libre / ingresos if ingresos > 0 else 0
    señales.append({"clave":"margen","estado":"ok" if ingresos and tasa_ahorro >= .15 else "atencion" if ingresos and tasa_ahorro > 0 else "alerta",
        "titulo":"Margen mensual","valor":dinero(libre),
        "detalle":f"Margen del mes: {dinero(libre)} ({tasa_ahorro:.0%} de los ingresos registrados)." if ingresos else "No hay ingresos registrados para medir el margen del mes.",
        "accion":"Revisar gastos antes de comprometer el margen restante."})
    if deudas["total"] > 0:
        prioridad_valor, prioridad_accion, prioridad_estado = "Reducir deuda", "Después de cubrir mínimos, concentrar el extra en una sola deuda según la estrategia elegida.", "atencion"
    elif inversiones["total"] > 0:
        prioridad_valor, prioridad_accion, prioridad_estado = "Construir patrimonio", "Mantener aportes sostenibles y revisar la asignación periódicamente.", "ok"
    else:
        prioridad_valor, prioridad_accion, prioridad_estado = "Preparar el siguiente paso", "Usar el margen para fortalecer emergencia, ahorro y luego inversión.", "ok"
    señales.append({"clave":"prioridad","estado":prioridad_estado,"titulo":"Prioridad actual","valor":prioridad_valor,
        "detalle":"Lectura combinada de obligaciones y patrimonio registrados.","accion":prioridad_accion})
    prioridad = next((x for x in señales if x["estado"]=="alerta"), None) or next((x for x in señales if x["estado"]=="atencion"), None) or señales[-1]
    return {"mes":month,"señales":señales,"proxima_accion":prioridad["accion"],
        "proxima_accion_clave":prioridad["clave"],"resumen":f"{prioridad['titulo']}: {prioridad['valor']}.",
        "nota":"Radar derivado de movimientos y configuraciones registradas; es orientativo y no ejecuta pagos."}


def financial_os(month: str, presupuesto_deuda: int | None = None) -> dict[str, Any]:
    """Mapa completo: situación -> prioridades -> deuda -> emergencia -> ahorro -> inversión -> patrimonio."""
    estado = {
        "mes": month,
        "flujo": calc.flujo_caja_mes(month),
        "liquidez": calc.liquidez_por_persona(month),
        "patrimonio": calc.patrimonio_liquido(month),
        "tarjetas": calc.resumen_tarjetas(),
        "ahorros": calc.resumen_ahorros(),
        "deudas": resumen_deudas(),
        "inversiones": resumen_inversiones(),
        "emergencia": fondo_emergencia(month),
    }
    libre = max(0, int(estado["flujo"].get("ahorro", 0)))
    presupuesto = max(0, int(presupuesto_deuda if presupuesto_deuda is not None else libre))
    estado["plan_deuda"] = plan_deuda(presupuesto)
    estado["asignacion_margen"] = asignacion_margen(month, libre)
    estado["trayectoria_patrimonio"] = trayectoria_patrimonio(month, 12)
    estado["tarjetas_operativo"] = resumen_tarjetas_operativo(month)
    estado["plan_mensual_deuda"] = plan_mensual_deuda(month, presupuesto)
    estado["preparacion_inversion"] = readiness(month)
    estado["proyeccion"] = proyeccion_inversion(max(0, int(libre)), 60, 0) if libre > 0 else {"aporte_mensual": 0, "meses": 60, "tasa_anual_pb": 0, "aportado": 0, "valor_proyectado": 0, "ganancia_proyectada": 0, "evolucion": []}
    prioridades = []
    if estado["deudas"]["total"] > 0:
        prioridades.append({"orden": 1, "clave": "deuda", "titulo": "Ordenar deudas", "detalle": "Comparar pagos mínimos y estrategias antes de aumentar aportes de inversión."})
    if estado["deudas"]["total"] == 0 and estado["emergencia"]["faltante_base"] > 0:
        prioridades.append({"orden": 1, "clave": "emergencia", "titulo": "Construir fondo de emergencia", "detalle": "Completar la meta base usando la capacidad mensual real."})
    elif estado["deudas"]["total"] > 0 and estado["emergencia"]["faltante_base"] > 0:
        prioridades.append({"orden": 2, "clave": "emergencia", "titulo": "Construir fondo de emergencia", "detalle": "Completar la meta base usando la capacidad mensual real."})
    if libre > 0:
        prioridades.append({"orden": 3, "clave": "ahorro", "titulo": "Automatizar ahorro", "detalle": "Separar una cantidad sostenible sin dejar la liquidez mensual en cero."})
    prioridades.append({"orden": 4, "clave": "inversion", "titulo": "Construir patrimonio", "detalle": "Cuando la liquidez y las obligaciones estén cubiertas, modelar aportes de inversión."})
    estado["prioridades"] = prioridades
    estado["mapa_accion"] = mapa_accion(month, presupuesto)
    estado["radar_financiero"] = radar_financiero(month)
    estado["resumen"] = {
        "dinero_libre": libre,
        "deuda_total": estado["deudas"]["total"],
        "emergencia_cobertura": estado["emergencia"]["cobertura_meses"],
        "inversion_actual": estado["inversiones"]["total"],
        "ahorro_total": int(estado["ahorros"].get("total", 0)),
        "patrimonio_neto": int(estado["patrimonio"].get("patrimonio_neto", 0)),
    }
    return estado
