"""Motor determinista para reparto económico de pareja.

El modelo vigente se versiona por mes. Este módulo no persiste ni modifica
liquidaciones históricas.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

PERSONA1 = "persona1"
PERSONA2 = "persona2"


def _cop(value: Any) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def repartir(
    monto: int,
    modelo: str,
    *,
    ingreso_p1: int = 0,
    ingreso_p2: int = 0,
    aporte_pozo_p1: int = 0,
    aporte_pozo_p2: int = 0,
) -> dict[str, Any]:
    monto = _cop(monto)
    if monto < 0:
        raise ValueError("El monto no puede ser negativo.")
    if modelo not in {"5050", "proporcional", "pozo"}:
        raise ValueError("Modelo de pareja no válido.")

    i1, i2 = _cop(ingreso_p1), _cop(ingreso_p2)
    if modelo == "5050":
        p1 = monto // 2
        p2 = monto - p1
        estado = "ok"
        nota = "Reparto 50/50; la segunda persona absorbe el peso del peso si el monto es impar."
    elif modelo == "proporcional":
        total = i1 + i2
        if total <= 0:
            return {
                "modelo": modelo, "monto": monto, "monto_p1": 0, "monto_p2": 0,
                "porcentaje_p1": None, "porcentaje_p2": None,
                "estado": "requiere_confirmacion",
                "nota": "No hay ingresos disponibles para calcular una proporción.",
            }
        p1 = _cop(Decimal(monto) * Decimal(i1) / Decimal(total))
        p2 = monto - p1
        estado = "ok"
        nota = "Reparto proporcional a los ingresos usados por el cálculo."
    else:
        total_pozo = _cop(aporte_pozo_p1) + _cop(aporte_pozo_p2)
        if total_pozo <= 0:
            return {
                "modelo": modelo, "monto": monto, "monto_p1": 0, "monto_p2": 0,
                "porcentaje_p1": None, "porcentaje_p2": None,
                "estado": "requiere_confirmacion",
                "nota": "El pozo común no tiene aportes registrados.",
            }
        p1 = _cop(Decimal(monto) * Decimal(_cop(aporte_pozo_p1)) / Decimal(total_pozo))
        p2 = monto - p1
        estado = "ok"
        nota = "Reparto proporcional a los aportes registrados al pozo común."

    total = p1 + p2
    return {
        "modelo": modelo,
        "monto": monto,
        "monto_p1": p1,
        "monto_p2": p2,
        "porcentaje_p1": (p1 / monto) if monto else 0,
        "porcentaje_p2": (p2 / monto) if monto else 0,
        "estado": estado,
        "nota": nota,
        "invariante": total == monto,
    }
