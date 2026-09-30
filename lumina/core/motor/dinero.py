"""Helpers financieros deterministas para dinero y tasas.

Regla: los valores monetarios se representan como enteros COP. Las tasas que
entran como porcentajes o puntos básicos se convierten a Decimal en el borde.
Nunca se usa round() de float para dinero.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CENTAVO = Decimal("1")


def decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def cop(value: Any) -> int:
    """Convierte un valor monetario a COP entero con half-up."""
    return int(decimal(value).quantize(CENTAVO, rounding=ROUND_HALF_UP))


def interes_cop(saldo: int, tasa_mensual: Any) -> int:
    """Calcula interés mensual en COP entero con redondeo half-up."""
    if saldo <= 0:
        return 0
    return cop(Decimal(saldo) * decimal(tasa_mensual))


def ea_pb_a_mensual(tasa_ea_pb: Any) -> Decimal:
    """Convierte tasa efectiva anual en puntos básicos a tasa mensual efectiva."""
    ea = decimal(tasa_ea_pb) / Decimal("10000")
    if ea <= 0:
        return Decimal("0")
    return (Decimal("1") + ea) ** (Decimal("1") / Decimal("12")) - Decimal("1")


def porcentaje_a_decimal(porcentaje: Any) -> Decimal:
    """Convierte 2.5 (%) en Decimal 0.025 sin pasar por float."""
    return decimal(porcentaje) / Decimal("100")


def cuota_con_residuo(saldo: int, cuotas: int, tasa_mensual: Any = 0) -> list[int]:
    """Distribuye una deuda en cuotas enteras; la última absorbe el residuo."""
    if saldo < 0 or cuotas <= 0:
        raise ValueError("Saldo y número de cuotas deben ser válidos.")
    tasa = decimal(tasa_mensual)
    if tasa == 0:
        base, resto = divmod(saldo, cuotas)
        return [base] * (cuotas - 1) + [base + resto]
    pago = cop(Decimal(saldo) * tasa * (Decimal("1") + tasa) ** cuotas /
               ((Decimal("1") + tasa) ** cuotas - Decimal("1")))
    pagos = [pago] * cuotas
    residuo = saldo - sum(pagos)
    pagos[-1] += residuo
    return pagos
