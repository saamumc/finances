"""Motor único de simulación de deuda.

No persiste datos y no conoce la UI. Recibe filas normalizadas y devuelve una
proyección determinista. Las estrategias usan tasas Decimal y redondeo
half-up en cada período.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .dinero import cop, interes_cop


def simular_cascada(
    filas: list[dict[str, Any]],
    presupuesto: int,
    estrategia: str,
    *,
    start_month: str | None = None,
    limite_meses: int = 600,
    etiqueta_avalancha: str = "avalancha",
    etiqueta_nieve: str = "bola_de_nieve",
) -> dict[str, Any]:
    if estrategia not in {etiqueta_avalancha, etiqueta_nieve}:
        raise ValueError("Estrategia de deuda no válida.")
    presupuesto = cop(presupuesto)
    if presupuesto < 0:
        raise ValueError("El presupuesto no puede ser negativo.")

    work = []
    for fila in filas:
        saldo = cop(fila.get("saldo", 0))
        if saldo <= 0:
            continue
        minimo = min(cop(fila.get("minimo", 0)), saldo)
        try:
            tasa = Decimal(str(fila.get("tasa_mensual", 0)))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("La tasa mensual de una deuda no es válida.") from exc
        if not tasa.is_finite() or tasa < 0:
            raise ValueError("La tasa mensual de una deuda debe ser finita y no negativa.")
        work.append({
            **fila,
            "saldo": saldo,
            "minimo": minimo,
            "tasa_mensual": tasa,
            "intereses": 0,
            "pagado": 0,
        })

    inicial = sum(x["saldo"] for x in work)
    minimos = sum(x["minimo"] for x in work)
    base_month = start_month or dt.date.today().strftime("%Y-%m")
    if not work:
        return {
            "viable": True, "meses": 0, "deuda_inicial": 0,
            "intereses_proyectados": 0, "presupuesto_mensual": presupuesto,
            "minimos": 0, "fecha_libre": base_month, "detalle": [],
            "estrategia": estrategia,
        }
    if presupuesto < minimos:
        return {
            "viable": False, "meses": None, "deuda_inicial": inicial,
            "intereses_proyectados": 0, "presupuesto_mensual": presupuesto,
            "minimos": minimos, "fecha_libre": None, "detalle": work,
            "estrategia": estrategia,
            "nota": "El presupuesto no alcanza los mínimos registrados; no se inventa una fecha de salida.",
        }

    total_interest = 0
    months = 0
    while any(x["saldo"] > 0 for x in work) and months < limite_meses:
        months += 1
        for item in work:
            if item["saldo"] > 0 and item["tasa_mensual"] > 0:
                interest = interes_cop(item["saldo"], item["tasa_mensual"])
                item["saldo"] += interest
                item["intereses"] += interest
                total_interest += interest

        available = presupuesto
        for item in work:
            payment = min(item["minimo"], item["saldo"])
            item["saldo"] -= payment
            item["pagado"] += payment
            available -= payment

        active = [x for x in work if x["saldo"] > 0]
        if estrategia == etiqueta_avalancha:
            active.sort(key=lambda x: (-x["tasa_mensual"], -x["saldo"], str(x["id"])))
        else:
            active.sort(key=lambda x: (x["saldo"], -x["tasa_mensual"], str(x["id"])))

        for item in active:
            if available <= 0:
                break
            extra = min(available, item["saldo"])
            item["saldo"] -= extra
            item["pagado"] += extra
            available -= extra

    viable = not any(x["saldo"] > 0 for x in work)
    free_date = None
    if viable:
        year, month = (int(x) for x in base_month.split("-"))
        index = year * 12 + month - 1 + max(months - 1, 0)
        year, month = divmod(index, 12)
        free_date = f"{year:04d}-{month + 1:02d}"

    return {
        "viable": viable,
        "meses": months if viable else None,
        "deuda_inicial": inicial,
        "intereses_proyectados": total_interest,
        "presupuesto_mensual": presupuesto,
        "minimos": minimos,
        "fecha_libre": free_date,
        "detalle": work,
        "estrategia": estrategia,
        "nota": "Simulación determinista con tasas y mínimos registrados; excluye compras futuras, cargos y cambios de tasa.",
    }


def proyectar_una_deuda(
    saldo: int,
    tasa_mensual: Any,
    pago: int,
    *,
    limite_meses: int = 600,
) -> tuple[int | None, int]:
    saldo = cop(saldo)
    pago = cop(pago)
    try:
        tasa = Decimal(str(tasa_mensual))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("La tasa mensual no es válida.") from exc
    if not tasa.is_finite() or tasa < 0:
        raise ValueError("La tasa mensual debe ser finita y no negativa.")
    if saldo <= 0:
        return 0, 0

    pendiente = saldo
    intereses = 0
    periodos = 0
    while pendiente > 0 and periodos < limite_meses:
        interes = interes_cop(pendiente, tasa)
        if pago <= interes:
            return None, intereses + interes
        intereses += interes
        pendiente = max(pendiente + interes - pago, 0)
        periodos += 1
    return (periodos if pendiente == 0 else None), intereses
