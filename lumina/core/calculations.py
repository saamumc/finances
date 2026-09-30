"""Motor de cálculo financiero de solo lectura.

Este módulo no persiste datos ni modifica SQLite. Separa consumo,
responsabilidad económica, titularidad de tarjeta y aporte de liquidez para
evitar que un pago de tarjeta vuelva a contabilizar una compra ya reconocida.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from math import ceil
from typing import Any

from . import database as db
from ..constants import (
    ESTADO_ACTIVO,
    IntegrityError,
    METODO_TARJETA,
    MOV_AHORRO_DEPOSITO,
    SAMUEL,
    SARA,
    PERSONAS_VALIDAS,
    RESP_COMPARTIDO,
    RESP_P1,
    RESP_P2,
    ValidationError,
    validar_monto_no_negativo,
    validar_persona,
    validar_responsabilidad,
)


def fmt_cop(valor: object) -> str:
    """Formatea un entero COP sin introducir decimales ni floats."""
    monto = validar_monto_no_negativo(valor)
    return f"${monto:,}".replace(",", ".")


def obtener_porcentajes(responsabilidad: str) -> tuple[Decimal, Decimal]:
    """Devuelve la distribución por defecto P1/P2 para una responsabilidad."""
    validar_responsabilidad(responsabilidad)
    if responsabilidad == RESP_P1:
        return Decimal("1"), Decimal("0")
    if responsabilidad == RESP_P2:
        return Decimal("0"), Decimal("1")
    return Decimal("0.5"), Decimal("0.5")


def calcular_responsabilidad(valor: object, responsabilidad: str) -> tuple[int, int]:
    """Calcula la distribución predeterminada de un gasto en COP entero.

    Los gastos compartidos se reparten 50/50 y el peso impar restante se
    asigna a P2. Para distribuciones no 50/50 se deben usar explícitamente
    ``monto_p1`` y ``monto_p2`` al registrar el gasto.
    """
    monto = validar_monto_no_negativo(valor)
    p1, _ = obtener_porcentajes(responsabilidad)
    monto_p1 = int((Decimal(monto) * p1).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return monto_p1, monto - monto_p1


def _monto_de(movimiento: dict[str, Any], persona: str) -> int:
    return movimiento["monto_p1"] if persona == SAMUEL else movimiento["monto_p2"]


def _aporte_pago(pago: dict[str, Any], persona: str) -> int:
    return pago["monto_aportado_p1"] if persona == SAMUEL else pago["monto_aportado_p2"]


def _nuevo_detalle() -> dict[str, dict[str, int]]:
    return {
        persona: {
            "aportado": 0,
            "consumido": 0,
            "consumo_historico_atribuido": 0,
            "deuda_pendiente_compras": 0,
            "deuda_historica_pendiente": 0,
            "deuda_ajustes_pendiente": 0,
            "deuda_pendiente": 0,
            "liquidaciones_pagadas": 0,
            "liquidaciones_recibidas": 0,
            "balance_neto": 0,
        }
        for persona in PERSONAS_VALIDAS
    }


# ---------------------------------------------------------------------------
# Liquidez
# ---------------------------------------------------------------------------

def liquidez_por_persona(mes: str | None = None) -> dict[str, dict[str, int]]:
    """Calcula liquidez económica: ingresos menos lo que cada persona asume.

    La salida de caja y la responsabilidad no son sinónimos. Por eso una
    compra pagada por Samuel pero asumida 25/75 disminuye su liquidez en 25%
    y la de Sara en 75%, aun si fue hecha con tarjeta. Los pagos de tarjeta se
    exponen como trazabilidad de caja, pero no se restan una segunda vez.
    """
    resultado = {
        persona: {
            "ingresos": 0,
            "gastos_directos": 0,
            "gastos_responsabilidad": 0,
            "pagos_tarjeta_aportados": 0,
            "liquidez": 0,
        }
        for persona in PERSONAS_VALIDAS
    }
    for ingreso in db.get_ingresos(mes=mes):
        resultado[ingreso["persona"]]["ingresos"] += ingreso["valor"]
    for gasto in db.get_gastos(mes=mes):
        for persona in PERSONAS_VALIDAS:
            parte = _monto_de(gasto, persona)
            resultado[persona]["gastos_directos"] += parte
            resultado[persona]["gastos_responsabilidad"] += parte
    for pago in db.get_pagos_deuda(mes=mes):
        for persona in PERSONAS_VALIDAS:
            resultado[persona]["pagos_tarjeta_aportados"] += _aporte_pago(pago, persona)
    for datos in resultado.values():
        datos["liquidez"] = datos["ingresos"] - datos["gastos_responsabilidad"]
    return resultado


def caja_real_por_persona(mes: str | None = None) -> dict[str, dict[str, int]]:
    """Caja efectivamente desembolsada, separada de la liquidez económica."""
    resultado = {
        persona: {"ingresos": 0, "gastos_pagados": 0, "pagos_tarjeta": 0, "caja": 0}
        for persona in PERSONAS_VALIDAS
    }
    for ingreso in db.get_ingresos(mes=mes):
        resultado[ingreso["persona"]]["ingresos"] += ingreso["valor"]
    for gasto in db.get_gastos(mes=mes):
        if gasto["metodo_pago"] != METODO_TARJETA:
            resultado[gasto["pagador"]]["gastos_pagados"] += gasto["valor"]
    for pago in db.get_pagos_deuda(mes=mes):
        for persona in PERSONAS_VALIDAS:
            resultado[persona]["pagos_tarjeta"] += _aporte_pago(pago, persona)
    for datos in resultado.values():
        datos["caja"] = datos["ingresos"] - datos["gastos_pagados"] - datos["pagos_tarjeta"]
    return resultado


def reconciliar_saldo_personal(persona: str, mes: str | None = None) -> dict[str, Any]:
    """Explica la diferencia entre liquidez económica y caja operativa.

    LÚMINA no guarda un saldo bancario manual: ambos valores se reconstruyen
    desde movimientos activos. La liquidez económica asigna cada consumo a
    quien lo asumió; la caja, en cambio, descuenta a quien efectivamente pagó.
    Mostrar esta conciliación evita llamar ``dinero disponible`` a dos
    conceptos distintos y permite revisar cada diferencia sin modificar datos.
    """
    validar_persona(persona)
    liquidez = liquidez_por_persona(mes)[persona]
    caja = caja_real_por_persona(mes)[persona]
    gastos_pagados = caja["gastos_pagados"]
    responsabilidad_directa = sum(
        _monto_de(gasto, persona)
        for gasto in db.get_gastos(mes=mes)
        if gasto["metodo_pago"] != METODO_TARJETA
    )
    responsabilidad_tarjeta = sum(
        _monto_de(gasto, persona)
        for gasto in db.get_gastos(mes=mes)
        if gasto["metodo_pago"] == METODO_TARJETA
    )
    movimientos_ahorro = db.get_movimientos_ahorro(mes=mes)
    ahorro_neto = sum(
        movimiento["monto"] if movimiento["tipo"] == MOV_AHORRO_DEPOSITO else -movimiento["monto"]
        for movimiento in movimientos_ahorro if movimiento.get("aportante") == persona
    )
    liquidaciones = db.get_liquidaciones(mes=mes)
    liquidaciones_netas = sum(
        -movimiento["monto"] if movimiento["deudor"] == persona else movimiento["monto"] if movimiento["acreedor"] == persona else 0
        for movimiento in liquidaciones
    )
    saldo_reconstruido = caja["caja"] - ahorro_neto + liquidaciones_netas
    diferencia = liquidez["liquidez"] - saldo_reconstruido
    adelanto_compartidos = gastos_pagados - responsabilidad_directa
    componentes = [
        {"concepto": "Ingresos registrados", "monto": caja["ingresos"], "efecto_caja": caja["ingresos"]},
        {"concepto": "Gastos pagados directamente", "monto": gastos_pagados, "efecto_caja": -gastos_pagados},
        {"concepto": "Pagos de tarjeta realizados", "monto": caja["pagos_tarjeta"], "efecto_caja": -caja["pagos_tarjeta"]},
        {"concepto": "Aportes netos a cajitas", "monto": ahorro_neto, "efecto_caja": -ahorro_neto},
        {"concepto": "Liquidaciones recibidas/pagadas", "monto": liquidaciones_netas, "efecto_caja": liquidaciones_netas},
    ]
    causas = []
    if caja["pagos_tarjeta"]:
        causas.append({"tipo": "pago_tarjeta", "monto": caja["pagos_tarjeta"],
                       "detalle": "Es caja que salió para pagar tarjetas; no es un gasto de consumo nuevo."})
    if adelanto_compartidos:
        causas.append({"tipo": "adelanto_compartido", "monto": adelanto_compartidos,
                       "detalle": "Es la diferencia entre lo que pagaste directamente y la parte económica que te corresponde."})
    if responsabilidad_tarjeta:
        causas.append({"tipo": "consumo_tarjeta", "monto": -responsabilidad_tarjeta,
                       "detalle": "Es consumo asumido con tarjeta: reduce la liquidez económica antes de que salga caja."})
    if ahorro_neto:
        causas.append({"tipo": "ahorro", "monto": ahorro_neto,
                       "detalle": "El dinero movido a una cajita deja de estar disponible en caja, aunque sigue siendo patrimonio."})
    if liquidaciones_netas:
        causas.append({"tipo": "liquidacion", "monto": -liquidaciones_netas,
                       "detalle": "Las liquidaciones mueven efectivo entre Samuel y Sara."})
    return {
        "persona": persona, "mes": mes, "saldo_mostrado_anterior": liquidez["liquidez"],
        "saldo_reconstruido": saldo_reconstruido, "diferencia": diferencia,
        "caja_operativa": saldo_reconstruido, "liquidez_economica": liquidez["liquidez"],
        "componentes": componentes, "causas": causas,
        "estado": "explicado" if diferencia == sum(c["monto"] for c in causas) else "requiere_revision",
        "nota": "El saldo reconstruido parte de los movimientos registrados; LÚMINA no inventa un saldo inicial bancario.",
    }


def liquidez_total(mes: str | None = None) -> int:
    return sum(datos["liquidez"] for datos in liquidez_por_persona(mes).values())


# ---------------------------------------------------------------------------
# Tarjetas
# ---------------------------------------------------------------------------

def resumen_tarjeta(tarjeta: dict[str, Any]) -> dict[str, Any]:
    """Resume una tarjeta sin confundir cupo disponible con liquidez."""
    cupo_total = tarjeta["cupo_total"]
    saldo_deuda = tarjeta["saldo_deuda"]
    compras = db.get_compras_tarjeta(tarjeta_id=tarjeta["id"])
    pagos = [p for p in db.get_pagos_deuda() if p["tarjeta_id"] == tarjeta["id"]]
    responsabilidad = deuda_pendiente_por_responsabilidad(tarjeta["id"])
    intereses = sum(m["valor_original"] for m in compras if m.get("tipo", "COMPRA") == "INTERES")
    cargos = sum(m["valor_original"] for m in compras if m.get("tipo", "COMPRA") == "CARGO")
    compras_comerciales = [m for m in compras if m.get("tipo", "COMPRA") == "COMPRA"]
    cuota_pendiente = sum(ceil(m["valor_pendiente"] * m["cuotas_totales"] / m["valor_original"])
                          for m in compras_comerciales if m["valor_pendiente"])
    interes_estimado = int((Decimal(saldo_deuda) * Decimal(str(tarjeta["interes_mensual"])) / Decimal("100"))
                            .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {
        "id": tarjeta["id"],
        "nombre": tarjeta["nombre"],
        "propietario": tarjeta["propietario"],
        "banco": tarjeta.get("banco"),
        "tipo": tarjeta.get("tipo"),
        "ultimos_4": tarjeta.get("ultimos_4"),
        "fecha_corte": tarjeta.get("fecha_corte"),
        "fecha_pago": tarjeta.get("fecha_pago"),
        "activa": bool(tarjeta["activa"]),
        "cupo_total": cupo_total,
        "saldo_deuda": saldo_deuda,
        "cupo_disponible": cupo_total - saldo_deuda,
        "utilizacion": saldo_deuda / cupo_total if cupo_total > 0 else 0.0,
        "pago_minimo": tarjeta["pago_minimo"],
        "interes_mensual": tarjeta["interes_mensual"],
        "interes_estimado": interes_estimado,
        "deuda_persona1": responsabilidad[SAMUEL],
        "deuda_persona2": responsabilidad[SARA],
        "total_pagado": sum(p["monto"] for p in pagos),
        "pagado_persona1": sum(p["monto_aportado_p1"] for p in pagos),
        "pagado_persona2": sum(p["monto_aportado_p2"] for p in pagos),
        "compras_registradas": sum(m["valor_original"] for m in compras_comerciales),
        "intereses_registrados": intereses,
        "cargos_registrados": cargos,
        "compras_pendientes": sum(1 for m in compras_comerciales if m["valor_pendiente"] > 0),
        "cuotas_pendientes": cuota_pendiente,
        "deuda_historica": tarjeta.get("saldo_historico_pendiente", 0),
        "saldo_historico_pendiente": tarjeta.get("saldo_historico_pendiente", 0),
    }


def resumen_tarjetas(solo_activas: bool = False) -> list[dict[str, Any]]:
    return [resumen_tarjeta(tarjeta) for tarjeta in db.get_tarjetas(solo_activas=solo_activas)]


def deuda_total_tarjetas(solo_activas: bool = False) -> int:
    return sum(tarjeta["saldo_deuda"] for tarjeta in db.get_tarjetas(solo_activas=solo_activas))


def _deuda_compras_conocidas(tarjeta_id: int | None = None) -> dict[str, int]:
    resultado = {persona: 0 for persona in PERSONAS_VALIDAS}
    for compra in db.get_compras_tarjeta(tarjeta_id=tarjeta_id):
        original = compra["valor_original"]
        if original <= 0:
            raise IntegrityError(f"Compra de tarjeta inválida #{compra['id']}: valor original no positivo.")
        if compra["monto_p1"] + compra["monto_p2"] != original:
            raise IntegrityError(f"Compra de tarjeta inválida #{compra['id']}: distribución inconsistente.")
        pendiente = compra["valor_pendiente"]
        if pendiente < 0 or pendiente > original:
            raise IntegrityError(f"Compra de tarjeta inválida #{compra['id']}: pendiente fuera de rango.")
        # La regla del dominio conserva la proporción original. El residuo COP
        # se asigna a P2 para que las dos partes sumen exactamente el pendiente.
        pendiente_p1 = round(pendiente * (compra["monto_p1"] / original))
        resultado[SAMUEL] += pendiente_p1
        resultado[SARA] += pendiente - pendiente_p1
    return resultado


def _deuda_historica_por_titular(tarjeta_id: int | None = None) -> dict[str, int]:
    """Atribuye deuda histórica sin desglose al titular legal, provisionalmente.

    No representa una compra nueva ni afirma quién consumió esa deuda. Es una
    política de presentación necesaria mientras no existe una distribución
    histórica explícita; solo usa ``saldo_historico_pendiente`` y jamás el
    acumulado ``monto_historico_aplicado``.
    """
    resultado = {persona: 0 for persona in PERSONAS_VALIDAS}
    tarjetas = db.get_tarjetas()
    for tarjeta in tarjetas:
        if tarjeta_id is not None and tarjeta["id"] != tarjeta_id:
            continue
        pendiente = tarjeta.get("saldo_historico_pendiente", 0)
        if pendiente < 0:
            raise IntegrityError(f"Tarjeta #{tarjeta['id']} tiene saldo histórico negativo.")
        resultado[tarjeta["propietario"]] += pendiente
    return resultado


def _consumo_historico_atribuido(tarjeta_id: int | None = None) -> dict[str, int]:
    """Devuelve el consumo original que explica una deuda histórica.

    Una deuda inicial de 1.000 con un abono posterior de 400 debe dejar
    consumo=1.000, deuda pendiente=600 y aporte=400. Registrar únicamente el
    saldo pendiente como consumo rompe la igualdad del libro por el valor ya
    abonado. La atribución sigue siendo provisional al titular hasta que se
    registre un desglose histórico más preciso.
    """
    resultado = {persona: 0 for persona in PERSONAS_VALIDAS}
    for tarjeta in db.get_tarjetas():
        if tarjeta_id is not None and tarjeta["id"] != tarjeta_id:
            continue
        original = tarjeta.get("saldo_inicial_historico", 0)
        pendiente = tarjeta.get("saldo_historico_pendiente", 0)
        if original < pendiente:
            raise IntegrityError(f"Tarjeta #{tarjeta['id']} tiene deuda histórica pendiente mayor a su saldo inicial.")
        resultado[tarjeta["propietario"]] += original
    return resultado


def _deuda_ajustes_por_responsabilidad(tarjeta_id: int | None = None) -> dict[str, int]:
    """Distribuye únicamente la porción pendiente de ajustes positivos."""
    resultado = {persona: 0 for persona in PERSONAS_VALIDAS}
    for ajuste in db.get_ajustes_tarjeta(tarjeta_id=tarjeta_id):
        if ajuste["variacion"] <= 0 or not ajuste["valor_pendiente"]:
            continue
        p1 = int((Decimal(ajuste["valor_pendiente"]) * Decimal(ajuste["monto_p1"]) /
                  Decimal(ajuste["variacion"])).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        resultado[SAMUEL] += p1
        resultado[SARA] += ajuste["valor_pendiente"] - p1
    return resultado


def deuda_pendiente_por_responsabilidad(tarjeta_id: int | None = None) -> dict[str, int]:
    """Distribuye deuda vigente: compras conocidas más saldo histórico pendiente."""
    conocidas = _deuda_compras_conocidas(tarjeta_id)
    historica = _deuda_historica_por_titular(tarjeta_id)
    ajustes = _deuda_ajustes_por_responsabilidad(tarjeta_id)
    return {persona: conocidas[persona] + historica[persona] + ajustes[persona] for persona in PERSONAS_VALIDAS}


def resumen_mensual_tarjeta(tarjeta_id: int, mes: str) -> dict[str, int | str]:
    """Actividad registrada del período; no inventa un extracto bancario cerrado."""
    movimientos = [m for m in db.get_compras_tarjeta(tarjeta_id=tarjeta_id) if m.get("mes") == mes]
    pagos = [p for p in db.get_pagos_deuda(mes=mes) if p["tarjeta_id"] == tarjeta_id]
    tarjeta = next((t for t in db.get_tarjetas() if t["id"] == tarjeta_id), None)
    if tarjeta is None:
        raise ValidationError("Tarjeta no encontrada.")
    compras = sum(m["valor_original"] for m in movimientos if m.get("tipo", "COMPRA") == "COMPRA")
    intereses = sum(m["valor_original"] for m in movimientos if m.get("tipo") == "INTERES")
    cargos = sum(m["valor_original"] for m in movimientos if m.get("tipo") == "CARGO")
    ajustes = sum(a["variacion"] for a in db.get_ajustes_tarjeta(tarjeta_id) if a["mes"] == mes)
    total_pagos = sum(p["monto"] for p in pagos)
    final = tarjeta["saldo_deuda"]
    inicial = final - compras - intereses - cargos - ajustes + total_pagos
    return {"mes": mes, "deuda_inicial": inicial, "compras": compras, "intereses": intereses,
            "cargos": cargos, "ajustes": ajustes, "pagos": total_pagos, "deuda_final": final,
            "variacion": final - inicial}


def tendencia_tarjeta(tarjeta_id: int, limite: int = 6) -> list[dict[str, int | str]]:
    """Reconstruye saldos mensuales con movimientos registrados, sin estimar datos del banco."""
    tarjeta = next((t for t in db.get_tarjetas() if t["id"] == tarjeta_id), None)
    if tarjeta is None:
        raise ValidationError("Tarjeta no encontrada.")
    por_mes: dict[str, int] = defaultdict(int)
    if tarjeta.get("saldo_inicial_historico") and tarjeta.get("fecha_saldo_inicial"):
        por_mes[tarjeta["fecha_saldo_inicial"][:7]] += tarjeta["saldo_inicial_historico"]
    for movimiento in db.get_compras_tarjeta(tarjeta_id=tarjeta_id):
        if movimiento.get("mes"):
            por_mes[movimiento["mes"]] += movimiento["valor_original"]
    for ajuste in db.get_ajustes_tarjeta(tarjeta_id):
        por_mes[ajuste["mes"]] += ajuste["variacion"]
    for pago in db.get_pagos_deuda():
        if pago["tarjeta_id"] == tarjeta_id:
            por_mes[pago["mes"]] -= pago["monto"]
    saldo = 0
    resultado: list[dict[str, int | str]] = []
    for periodo in sorted(por_mes):
        saldo += por_mes[periodo]
        resultado.append({"mes": periodo, "deuda": saldo, "variacion": por_mes[periodo]})
    return resultado[-limite:]


def estrategias_tarjetas() -> dict[str, list[dict[str, Any]]]:
    """Órdenes de simulación: no registran pagos ni dictan una decisión."""
    activas = [t for t in resumen_tarjetas(solo_activas=True) if t["saldo_deuda"] > 0]
    return {
        "avalancha": sorted(activas, key=lambda t: (-t["interes_mensual"], -t["saldo_deuda"], t["id"])),
        "snowball": sorted(activas, key=lambda t: (t["saldo_deuda"], -t["interes_mensual"], t["id"])),
    }


def historial_tarjeta(tarjeta_id: int, incluir_reversados: bool = True) -> list[dict[str, Any]]:
    """Timeline homogénea basada en fuentes existentes, sin duplicar movimientos."""
    eventos: list[dict[str, Any]] = []
    for movimiento in db.get_compras_tarjeta(tarjeta_id, incluir_reversados):
        tipo = movimiento.get("tipo", "COMPRA")
        eventos.append({"tipo": tipo, "id": movimiento["id"], "fecha": movimiento["fecha"] or movimiento.get("mes"),
                        "descripcion": movimiento["descripcion"], "monto": movimiento["valor_original"],
                        "efecto_deuda": movimiento["valor_original"] if movimiento["estado"] == ESTADO_ACTIVO else 0,
                        "responsabilidad": movimiento["responsabilidad"], "monto_p1": movimiento["monto_p1"],
                        "monto_p2": movimiento["monto_p2"], "estado": movimiento["estado"],
                        "cuotas_totales": movimiento["cuotas_totales"], "valor_pendiente": movimiento["valor_pendiente"]})
    for pago in db.get_pagos_deuda(incluir_reversados=incluir_reversados):
        if pago["tarjeta_id"] == tarjeta_id:
            eventos.append({"tipo": "PAGO", "id": pago["id"], "fecha": pago["fecha"],
                            "descripcion": pago.get("concepto") or "Pago de tarjeta", "monto": pago["monto"],
                            "efecto_deuda": -pago["monto"] if pago["estado"] == ESTADO_ACTIVO else 0,
                            "aporte_p1": pago["monto_aportado_p1"], "aporte_p2": pago["monto_aportado_p2"],
                            "estado": pago["estado"]})
    for ajuste in db.get_ajustes_tarjeta(tarjeta_id, incluir_reversados):
        eventos.append({"tipo": "AJUSTE", "id": ajuste["id"], "fecha": ajuste["fecha"], "descripcion": ajuste["motivo"],
                        "monto": abs(ajuste["variacion"]), "efecto_deuda": ajuste["variacion"] if ajuste["estado"] == ESTADO_ACTIVO else 0,
                        "monto_p1": ajuste["monto_p1"], "monto_p2": ajuste["monto_p2"], "estado": ajuste["estado"]})
    return sorted(eventos, key=lambda e: (e["fecha"] or "", e["id"]), reverse=True)


# ---------------------------------------------------------------------------
def ingresos_promedio_tres_meses(persona: str, mes: str) -> int:
    """Promedio entero de ingresos activos de los tres meses incluido mes."""
    validar_persona(persona)
    base = dt.date.fromisoformat(mes + "-01")
    meses = []
    for offset in (2, 1, 0):
        indice = base.year * 12 + base.month - 1 - offset
        year, month0 = divmod(indice, 12)
        meses.append(f"{year:04d}-{month0 + 1:02d}")
    total = 0
    for item_mes in meses:
        total += sum(
            int(i["valor"]) for i in db.get_ingresos(item_mes)
            if i["persona"] == persona and i["estado"] == ESTADO_ACTIVO
        )
    return int(Decimal(total) / Decimal(3) + Decimal("0.5"))


def reparto_pareja(monto: int, mes: str, modelo: dict | None = None) -> dict[str, Any]:
    """Calcula un reparto vigente; no crea ni modifica liquidaciones."""
    from .motor.pareja import repartir

    vigente = modelo or db.get_modelo_pareja(mes)
    if not vigente:
        return {
            "estado": "requiere_confirmacion",
            "modelo": None,
            "monto": int(monto),
            "monto_p1": 0,
            "monto_p2": 0,
            "nota": "No existe un modelo de pareja vigente para este mes.",
        }

    i1 = ingresos_promedio_tres_meses(SAMUEL, mes) if vigente["base_proporcional"] == "ingreso_promedio" else 0
    i2 = ingresos_promedio_tres_meses(SARA, mes) if vigente["base_proporcional"] == "ingreso_promedio" else 0
    if vigente["modelo"] == "proporcional" and vigente["base_proporcional"] == "ingreso_mes":
        i1 = sum(int(x["valor"]) for x in db.get_ingresos(mes) if x["persona"] == SAMUEL and x["estado"] == ESTADO_ACTIVO)
        i2 = sum(int(x["valor"]) for x in db.get_ingresos(mes) if x["persona"] == SARA and x["estado"] == ESTADO_ACTIVO)

    aportes = db.get_aportes_pozo(mes)
    pozo1 = sum(int(x["monto"]) for x in aportes if x["persona"] == SAMUEL)
    pozo2 = sum(int(x["monto"]) for x in aportes if x["persona"] == SARA)
    return repartir(
        int(monto),
        vigente["modelo"],
        ingreso_p1=i1,
        ingreso_p2=i2,
        aporte_pozo_p1=pozo1 if pozo1 else int(vigente.get("pozo_aporte_p1") or 0),
        aporte_pozo_p2=pozo2 if pozo2 else int(vigente.get("pozo_aporte_p2") or 0),
    )


# Balance de pareja
# ---------------------------------------------------------------------------

def balance_historico_pareja(mes: str | None = None) -> dict[str, Any]:
    """Calcula el balance entre las dos personas sin doble conteo.

    ``mes`` filtra movimientos registrados en ese período. La deuda pendiente
    es el estado vigente, por lo que esta vista mensual es una lectura de
    actividad del mes, no una reconstrucción histórica a una fecha pasada.
    Para la invariante completa del libro use ``mes=None``.
    """
    datos = _nuevo_detalle()
    for gasto in db.get_gastos(mes=mes):
        if gasto["metodo_pago"] != METODO_TARJETA:
            datos[gasto["pagador"]]["aportado"] += gasto["valor"]
        for persona in PERSONAS_VALIDAS:
            datos[persona]["consumido"] += _monto_de(gasto, persona)

    # Intereses y cargos viven en el mismo soporte amortizable de tarjeta,
    # pero no son gastos comerciales. Se incorporan aquí para conservar la
    # invariante: consumo/ajuste reconocido + deuda pendiente = 0 por pareja.
    for movimiento in db.get_compras_tarjeta():
        if movimiento.get("tipo", "COMPRA") == "COMPRA" or (mes is not None and movimiento.get("mes") != mes):
            continue
        datos[SAMUEL]["consumido"] += movimiento["monto_p1"]
        datos[SARA]["consumido"] += movimiento["monto_p2"]
    for ajuste in db.get_ajustes_tarjeta():
        if mes is not None and ajuste["mes"] != mes:
            continue
        signo = 1 if ajuste["variacion"] > 0 else -1
        datos[SAMUEL]["consumido"] += signo * ajuste["monto_p1"]
        datos[SARA]["consumido"] += signo * ajuste["monto_p2"]

    for pago in db.get_pagos_deuda(mes=mes):
        for persona in PERSONAS_VALIDAS:
            datos[persona]["aportado"] += _aporte_pago(pago, persona)

    conocidas = _deuda_compras_conocidas()
    historica = _deuda_historica_por_titular()
    consumo_historico = _consumo_historico_atribuido()
    ajustes = _deuda_ajustes_por_responsabilidad()
    for persona in PERSONAS_VALIDAS:
        datos[persona]["deuda_pendiente_compras"] = conocidas[persona]
        datos[persona]["deuda_historica_pendiente"] = historica[persona]
        datos[persona]["deuda_ajustes_pendiente"] = ajustes[persona]
        datos[persona]["deuda_pendiente"] = conocidas[persona] + historica[persona] + ajustes[persona]
        # No hay gasto histórico individualizado. Se incorpora el saldo
        # original como consumo derivado para que los abonos ya realizados
        # tengan su contrapartida contable sin crear compras ficticias.
        datos[persona]["consumo_historico_atribuido"] = consumo_historico[persona]
        datos[persona]["consumido"] += consumo_historico[persona]

    for liquidacion in db.get_liquidaciones(mes=mes):
        datos[liquidacion["deudor"]]["liquidaciones_pagadas"] += liquidacion["monto"]
        datos[liquidacion["acreedor"]]["liquidaciones_recibidas"] += liquidacion["monto"]

    for persona, detalle in datos.items():
        detalle["balance_neto"] = (
            detalle["aportado"]
            - detalle["consumido"]
            + detalle["deuda_pendiente"]
            + detalle["liquidaciones_pagadas"]
            - detalle["liquidaciones_recibidas"]
        )

    total = sum(datos[persona]["balance_neto"] for persona in PERSONAS_VALIDAS)
    # Antes esta funcion lanzaba una excepcion y tumbaba TODA la app (dashboard,
    # asesor, simulador) si el libro no cuadraba exactamente a cero. audit_integrity()
    # ya esta preparada para reportar esto como hallazgo critico sin bloquear el resto
    # de la app, asi que aqui solo se deja constancia en "cuadra"/"descuadre".
    salida: dict[str, Any] = {"detalle": datos, "cuadra": total == 0, "descuadre": total, "mes": mes}
    for persona in PERSONAS_VALIDAS:
        salida[f"aportado_{persona}"] = datos[persona]["aportado"]
        salida[f"consumido_{persona}"] = datos[persona]["consumido"]
        salida[f"deuda_pendiente_{persona}"] = datos[persona]["deuda_pendiente"]
        salida[f"balance_neto_{persona}"] = datos[persona]["balance_neto"]
    return salida


def explicar_balance(persona: str, mes: str | None = None) -> dict[str, Any]:
    """Devuelve todas las líneas que componen el balance de una persona."""
    validar_persona(persona)
    lineas: list[dict[str, Any]] = []
    nombre_persona = "Samuel" if persona == SAMUEL else "Sara"
    tarjetas = {tarjeta["id"]: tarjeta["nombre"] for tarjeta in db.get_tarjetas()}
    for gasto in db.get_gastos(mes=mes):
        responsabilidad = _monto_de(gasto, persona)
        aporte = gasto["valor"] if gasto["metodo_pago"] != METODO_TARJETA and gasto["pagador"] == persona else 0
        if responsabilidad or aporte:
            if gasto["metodo_pago"] == METODO_TARJETA:
                tarjeta = tarjetas.get(gasto["tarjeta_id"], "tarjeta eliminada")
                detalle = (f"Compra «{gasto['nombre']}» en «{tarjeta}»: "
                           f"a {nombre_persona} le corresponde {fmt_cop(responsabilidad)}.")
            else:
                detalle = (f"Gasto directo «{gasto['nombre']}»: {nombre_persona} pagó {fmt_cop(aporte)} "
                           f"y le corresponde {fmt_cop(responsabilidad)}.")
            lineas.append({
                "tipo": "gasto",
                "id": gasto["id"],
                "detalle": detalle,
                "efecto": aporte - responsabilidad,
            })
    for movimiento in db.get_compras_tarjeta():
        if movimiento.get("tipo", "COMPRA") == "COMPRA" or (mes is not None and movimiento.get("mes") != mes):
            continue
        responsabilidad = movimiento["monto_p1"] if persona == SAMUEL else movimiento["monto_p2"]
        if responsabilidad:
            lineas.append({"tipo": movimiento["tipo"].lower(), "id": movimiento["id"],
                            "detalle": f"{movimiento['tipo'].title()} de tarjeta #{movimiento['id']}: responsabilidad={responsabilidad}",
                            "efecto": -responsabilidad})
    for ajuste in db.get_ajustes_tarjeta():
        if mes is not None and ajuste["mes"] != mes:
            continue
        monto = ajuste["monto_p1"] if persona == SAMUEL else ajuste["monto_p2"]
        if monto:
            signo = 1 if ajuste["variacion"] > 0 else -1
            lineas.append({"tipo": "ajuste_tarjeta", "id": ajuste["id"],
                            "detalle": f"Ajuste de saldo #{ajuste['id']}: variación={ajuste['variacion']}",
                            "efecto": -signo * monto})
    for pago in db.get_pagos_deuda(mes=mes):
        aporte = _aporte_pago(pago, persona)
        if aporte:
            tarjeta = tarjetas.get(pago["tarjeta_id"], "tarjeta eliminada")
            lineas.append({
                "tipo": "pago_tarjeta",
                "id": pago["id"],
                "detalle": f"Pago de «{tarjeta}»: {nombre_persona} aportó {fmt_cop(aporte)}.",
                "efecto": aporte,
            })
    for liquidacion in db.get_liquidaciones(mes=mes):
        if liquidacion["deudor"] == persona:
            lineas.append({"tipo": "liquidacion_pagada", "id": liquidacion["id"], "detalle": f"Liquidación pagada={liquidacion['monto']}", "efecto": liquidacion["monto"]})
        elif liquidacion["acreedor"] == persona:
            lineas.append({"tipo": "liquidacion_recibida", "id": liquidacion["id"], "detalle": f"Liquidación recibida={liquidacion['monto']}", "efecto": -liquidacion["monto"]})

    conocidas = _deuda_compras_conocidas()[persona]
    if conocidas:
        lineas.append({"tipo": "deuda_pendiente_compras", "id": None, "detalle": f"Deuda de compras conocidas pendiente={conocidas}", "efecto": conocidas})
    historica = _deuda_historica_por_titular()[persona]
    consumo_historico = _consumo_historico_atribuido()[persona]
    if consumo_historico:
        lineas.append({"tipo": "consumo_historico_atribuido", "id": None, "detalle": f"Consumo inicial derivado de deuda histórica={consumo_historico}", "efecto": -consumo_historico})
    if historica:
        lineas.append({"tipo": "deuda_historica_pendiente", "id": None, "detalle": f"Deuda histórica pendiente atribuida provisionalmente={historica}", "efecto": historica})
    ajustes = _deuda_ajustes_por_responsabilidad()[persona]
    if ajustes:
        lineas.append({"tipo": "deuda_ajustes_pendiente", "id": None,
                        "detalle": f"Deuda pendiente originada en ajustes explícitos={ajustes}", "efecto": ajustes})

    total = balance_historico_pareja(mes)[f"balance_neto_{persona}"]
    suma_lineas = sum(linea["efecto"] for linea in lineas)
    if suma_lineas != total:
        raise IntegrityError(f"Explicación inconsistente: {suma_lineas} != {total}.")
    return {"persona": persona, "lineas": lineas, "balance_neto": total}


def flujo_caja_mes(mes: str) -> dict[str, int | float | str]:
    """Resume caja del mes, sin incluir compras con tarjeta aún no pagadas."""
    caja = caja_real_por_persona(mes)
    ingresos = sum(dato["ingresos"] for dato in caja.values())
    salidas = sum(dato["gastos_pagados"] + dato["pagos_tarjeta"] for dato in caja.values())
    ahorro = ingresos - salidas
    return {"mes": mes, "ingresos": ingresos, "salidas": salidas, "ahorro": ahorro,
            "tasa_ahorro": ahorro / ingresos if ingresos else 0.0}


def resumen_ahorros() -> dict[str, Any]:
    """Saldo reservado por cajita; no altera la liquidez ni el consumo."""
    fondos = db.get_ahorros()
    return {"fondos": fondos, "total": sum(fondo["saldo"] for fondo in fondos)}


def progreso_metas_ahorro() -> list[dict[str, Any]]:
    """Compatibilidad: progreso de las cajitas con referencia propia."""
    resultado: list[dict[str, Any]] = []
    for fondo in db.get_ahorros():
        meta, saldo = fondo["meta"], fondo["saldo"]
        movimientos = db.get_movimientos_ahorro(ahorro_id=fondo["id"])
        aportes_por_mes: dict[str, int] = defaultdict(int)
        for movimiento in movimientos:
            if movimiento["tipo"] == "DEPOSITO":
                aportes_por_mes[movimiento["mes"]] += movimiento["monto"]
        promedio = sum(aportes_por_mes.values()) / len(aportes_por_mes) if aportes_por_mes else 0.0
        faltante = max(meta - saldo, 0)
        meses = 0 if meta and faltante == 0 else ceil(faltante / promedio) if promedio else None
        resultado.append({**fondo, "progreso": min(saldo / meta, 1.0) if meta else None,
                          "faltante": faltante, "aporte_mensual_promedio": promedio,
                          "meses_restantes_estimados": meses})
    return resultado


def total_reservado() -> int:
    return resumen_ahorros()["total"]


def aporte_por_persona(ahorro_id: int) -> dict[str, int]:
    fondo = next((f for f in db.get_ahorros(solo_activos=False) if f["id"] == ahorro_id), None)
    if fondo is None:
        raise ValidationError("Cajita no encontrada.")
    return {SAMUEL: fondo["aporte_p1"], SARA: fondo["aporte_p2"]}


def ahorro_mensual(mes: str) -> int:
    return sum(m["monto"] if m["tipo"] == "DEPOSITO" else -m["monto"]
               for m in db.get_movimientos_ahorro(mes=mes))


def promedio_ahorro() -> float:
    meses = {m["mes"] for m in db.get_movimientos_ahorro()}
    return sum(ahorro_mensual(mes) for mes in meses) / len(meses) if meses else 0.0


def progreso_metas() -> list[dict[str, Any]]:
    """Avance de objetivos separados, usando solo la cajita vinculada."""
    hoy = dt.date.today()
    salida: list[dict[str, Any]] = []
    for meta in db.get_metas():
        fondo = next((f for f in db.get_ahorros() if f["id"] == meta["ahorro_id"]), None) if meta["ahorro_id"] else None
        actual = fondo["saldo"] if fondo else 0
        faltante = max(meta["monto_objetivo"] - actual, 0)
        meses_restantes: int | None = None
        necesario: int | None = None
        if meta["fecha_objetivo"] and faltante:
            fecha = dt.date.fromisoformat(meta["fecha_objetivo"])
            meses_restantes = max((fecha.year - hoy.year) * 12 + fecha.month - hoy.month + (1 if fecha.day >= hoy.day else 0), 0)
            necesario = faltante if meses_restantes == 0 else ceil(faltante / meses_restantes)
        promedio = 0.0
        if fondo:
            por_mes: dict[str, int] = defaultdict(int)
            for movimiento in db.get_movimientos_ahorro(fondo["id"]):
                if movimiento["tipo"] == "DEPOSITO":
                    por_mes[movimiento["mes"]] += movimiento["monto"]
            promedio = sum(por_mes.values()) / len(por_mes) if por_mes else 0.0
        salida.append({**meta, "actual": actual, "faltante": faltante,
                       "progreso": min(actual / meta["monto_objetivo"], 1.0) if meta["monto_objetivo"] else 0.0,
                       "meses_restantes": meses_restantes, "ahorro_necesario_mensual": necesario,
                       "ahorro_promedio_mensual": promedio,
                       "atrasada": bool(necesario and promedio < necesario),
                       "cumplida": bool(meta["monto_objetivo"] and actual >= meta["monto_objetivo"])})
    return salida


def resumen_deudas_terceros() -> dict[str, Any]:
    detalle = db.get_prestamos_terceros()
    por_cobrar = sum(p["saldo_pendiente"] for p in detalle if p["tipo"] == "POR_COBRAR")
    por_pagar = sum(p["saldo_pendiente"] for p in detalle if p["tipo"] == "POR_PAGAR")
    por_persona = {persona: {"por_cobrar": 0, "por_pagar": 0} for persona in PERSONAS_VALIDAS}
    for cuenta in detalle:
        titularidad = cuenta.get("titularidad", cuenta["propietario"])
        if titularidad == RESP_COMPARTIDO:
            p1 = cuenta["saldo_pendiente"] // 2
            partes = {SAMUEL: p1, SARA: cuenta["saldo_pendiente"] - p1}
        else:
            partes = {titularidad: cuenta["saldo_pendiente"]}
        clave = "por_cobrar" if cuenta["tipo"] == "POR_COBRAR" else "por_pagar"
        for persona, monto in partes.items():
            por_persona[persona][clave] += monto
    for resumen in por_persona.values():
        resumen["posicion_neta"] = resumen["por_cobrar"] - resumen["por_pagar"]
    return {"por_cobrar": por_cobrar, "por_pagar": por_pagar,
            "posicion_neta": por_cobrar - por_pagar, "por_persona": por_persona, "detalle": detalle}


FRECUENCIA_A_FACTOR_MENSUAL: dict[str, Decimal] = {
    "mensual": Decimal("1"), "bimestral": Decimal("1") / Decimal("2"),
    "trimestral": Decimal("1") / Decimal("3"), "semestral": Decimal("1") / Decimal("6"),
    "anual": Decimal("1") / Decimal("12"),
}


def resumen_gastos_fijos(mes: str | None = None) -> dict[str, Any]:
    """Agrega las obligaciones recurrentes activas a su equivalente mensual.

    Una obligación anual o trimestral no cuesta lo mismo cada mes: se
    normaliza para que el compromiso mensual sea comparable con el ingreso.
    Si se indica ``mes``, también estima cuánto de ese compromiso ya quedó
    registrado como gasto real ese mes (por coincidencia de nombre).
    """
    fijos = db.get_gastos_fijos(solo_activos=True)
    detalle: list[dict[str, Any]] = []
    total = total_p1 = total_p2 = 0
    for gasto in fijos:
        factor = FRECUENCIA_A_FACTOR_MENSUAL[gasto["frecuencia"]]
        equivalente = int((Decimal(gasto["valor"]) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        equivalente_p1 = int((Decimal(gasto["monto_p1"]) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        equivalente_p2 = equivalente - equivalente_p1
        total += equivalente; total_p1 += equivalente_p1; total_p2 += equivalente_p2
        detalle.append({**gasto, "equivalente_mensual": equivalente,
                        "equivalente_mensual_p1": equivalente_p1, "equivalente_mensual_p2": equivalente_p2})
    registrado_mes = 0
    if mes:
        nombres = {gasto["nombre"].strip().casefold() for gasto in fijos}
        registrado_mes = sum(g["valor"] for g in db.get_gastos(mes=mes) if g["nombre"].strip().casefold() in nombres)
    ingreso_mes = sum(i["valor"] for i in db.get_ingresos(mes)) if mes else 0
    return {
        "mes": mes, "gastos": sorted(detalle, key=lambda g: g["equivalente_mensual"], reverse=True),
        "cantidad_activos": len(fijos),
        "total_mensual_equivalente": total, "total_mensual_persona1": total_p1, "total_mensual_persona2": total_p2,
        "registrado_este_mes": registrado_mes, "pendiente_este_mes": max(total - registrado_mes, 0),
        "porcentaje_ingreso": (total / ingreso_mes) if ingreso_mes else None,
    }


def patrimonio_liquido(mes: str | None = None) -> dict[str, int]:
    """Patrimonio operativo acumulado desde los movimientos activos.

    Esta cifra es distinta del flujo o la liquidez *del mes*: incluye todos
    los registros vigentes y por eso puede compararse correctamente con el
    saldo actual de cajitas y obligaciones. Si se indica ``mes``, el
    disponible también descuenta los gastos fijos de ese mes que aún no se
    han registrado como gasto real, para no prometer dinero ya comprometido.
    """
    liquidez = liquidez_total()
    terceros = resumen_deudas_terceros()
    reservado = total_reservado()
    fijos = resumen_gastos_fijos(mes)
    pendiente_fijos = fijos["pendiente_este_mes"] if mes else 0
    return {"liquidez_operativa": liquidez, "reservado_metas": reservado,
            "gastos_fijos_pendientes": pendiente_fijos,
            "disponible_gastos_recurrentes": liquidez - reservado - pendiente_fijos,
            "cuentas_por_cobrar": terceros["por_cobrar"], "cuentas_por_pagar": terceros["por_pagar"],
            "patrimonio_liquido": liquidez + terceros["por_cobrar"] - terceros["por_pagar"]}


def resumen_presupuestos(mes: str) -> dict[str, Any]:
    filas = db.get_presupuestos(mes)
    for fila in filas:
        fila["restante"] = fila["monto"] - fila["gastado"]
        fila["uso"] = fila["gastado"] / fila["monto"] if fila["monto"] else 0.0
    return {"mes": mes, "presupuestos": filas,
            "total_presupuestado": sum(f["monto"] for f in filas),
            "total_gastado": sum(f["gastado"] for f in filas)}


def capacidad_ahorro_estimada(mes: str) -> int:
    return max(int(flujo_caja_mes(mes)["ahorro"]), 0)


# ---------------------------------------------------------------------------
# Vistas personales (solo lectura)
# ---------------------------------------------------------------------------

def _mes_anterior(mes: str) -> str:
    ano, numero = (int(parte) for parte in mes.split("-"))
    return f"{ano - 1:04d}-12" if numero == 1 else f"{ano:04d}-{numero - 1:02d}"


def dashboard_personal(persona: str, mes: str) -> dict[str, Any]:
    """Perspectiva individual derivada del libro existente.

    El consumo se toma de ``monto_p1/monto_p2`` (no del total pagado), por lo
    que un gasto 70/30 solo aparece por su parte en cada tablero. La liquidez
    conserva la definición actual: caja aportada por la persona, no cupo de
    tarjeta. No persiste ni crea movimientos.
    """
    validar_persona(persona)
    anterior = _mes_anterior(mes)
    ingresos = [i for i in db.get_ingresos(mes) if i["persona"] == persona]
    ingresos_anterior = sum(i["valor"] for i in db.get_ingresos(anterior) if i["persona"] == persona)
    gastos = db.get_gastos(mes)
    consumo_por_categoria: dict[str, int] = defaultdict(int)
    gasto_personal = 0
    for gasto in gastos:
        parte = _monto_de(gasto, persona)
        gasto_personal += parte
        consumo_por_categoria[gasto["categoria"]] += parte
    gasto_anterior = sum(_monto_de(gasto, persona) for gasto in db.get_gastos(anterior))
    liquidez = liquidez_por_persona(mes)[persona]
    reconciliacion = reconciliar_saldo_personal(persona, mes)
    deuda = deuda_pendiente_por_responsabilidad()[persona]
    terceros = resumen_deudas_terceros()["por_persona"][persona]

    # Una cajita personal es la que tiene a la persona como titular. Las
    # cajitas compartidas se mantienen en la vista conjunta: repartir su saldo
    # actual sin una regla explícita inventaría propiedad.
    cajitas = [f for f in progreso_metas_ahorro() if f.get("titular", f["propietario"]) == persona]
    ids_cajitas = {f["id"] for f in cajitas}
    ahorro_mes = sum(
        movimiento["monto"] if movimiento["tipo"] == "DEPOSITO" else -movimiento["monto"]
        for movimiento in db.get_movimientos_ahorro(mes=mes)
        if movimiento["ahorro_id"] in ids_cajitas
    )
    metas = [meta for meta in progreso_metas() if meta.get("ahorro_id") in ids_cajitas]
    tarjetas = []
    for tarjeta in resumen_tarjetas():
        deuda_personal = tarjeta[f"deuda_{persona}"]
        if tarjeta["propietario"] == persona or deuda_personal:
            tarjetas.append({**tarjeta, "deuda_personal": deuda_personal,
                             "pago_minimo_personal": min(tarjeta["pago_minimo"], deuda_personal)})
    capacidad = max(reconciliacion["caja_operativa"], 0)
    fijos = resumen_gastos_fijos(mes)
    gastos_fijos_persona = fijos["total_mensual_persona1"] if persona == SAMUEL else fijos["total_mensual_persona2"]
    return {
        "persona": persona, "mes": mes, "liquidez": liquidez["liquidez"],
        "caja_disponible": reconciliacion["caja_operativa"], "reconciliacion": reconciliacion,
        "ingresos": sum(i["valor"] for i in ingresos), "ingresos_anterior": ingresos_anterior,
        "gastos": gasto_personal, "gastos_anterior": gasto_anterior,
        "gastos_por_categoria": sorted(({"categoria": categoria, "valor": valor} for categoria, valor in consumo_por_categoria.items()), key=lambda x: x["valor"], reverse=True),
        "deuda": deuda, "deuda_terceros": terceros["por_pagar"], "por_cobrar_terceros": terceros["por_cobrar"],
        "ahorrado": sum(c["saldo"] for c in cajitas), "ahorro_mes": ahorro_mes,
        "capacidad_ahorro": capacidad, "pagos_tarjeta": liquidez["pagos_tarjeta_aportados"],
        "cajitas": cajitas, "metas": metas, "tarjetas": tarjetas,
        "gastos_fijos_mensuales": gastos_fijos_persona,
        "nota_cajitas_compartidas": "Las cajitas compartidas se muestran en Nuestro dinero para no asignar un saldo personal sin una regla registrada.",
    }
