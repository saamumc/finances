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
from .engine import unified_debt_payoff


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _money(value: Any) -> int:
    return max(0, int(value or 0))


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


def plan_deuda(mensual_disponible: int) -> dict[str, Any]:
    """Compara cascadas con todas las tarjetas y deudas v15 sin alterar SQLite."""
    cards = unified_debt_payoff(max(0, int(mensual_disponible)), "avalancha")
    snow = unified_debt_payoff(max(0, int(mensual_disponible)), "snowball")
    return {
        "presupuesto_mensual": max(0, int(mensual_disponible)),
        "avalancha": cards,
        "bola_de_nieve": snow,
        "criterio": {
            "avalancha": "prioriza la tasa mensual más alta",
            "bola_de_nieve": "prioriza el saldo más pequeño",
        },
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
    estado["resumen"] = {
        "dinero_libre": libre,
        "deuda_total": estado["deudas"]["total"],
        "emergencia_cobertura": estado["emergencia"]["cobertura_meses"],
        "inversion_actual": estado["inversiones"]["total"],
        "ahorro_total": int(estado["ahorros"].get("total", 0)),
        "patrimonio_neto": int(estado["patrimonio"].get("patrimonio_neto", 0)),
    }
    return estado
