"""Tipos y validaciones del dominio financiero de Sara y Yo."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Any, Final, TypeVar


class Persona(str, Enum):
    """Las únicas identidades de la aplicación.

    Los valores conservan la codificación histórica de SQLite para mantener
    compatibilidad con las bases existentes; la UI solo presenta Yo y Sara.
    """

    YO = "persona1"
    SARA = "persona2"


class Responsabilidad(str, Enum):
    YO = Persona.YO.value
    SARA = Persona.SARA.value
    COMPARTIDO = "compartido"


class MetodoPago(str, Enum):
    EFECTIVO = "efectivo"
    DEBITO = "debito"
    TARJETA = "tarjeta"


class Prioridad(str, Enum):
    OBLIGATORIO = "obligatorio"
    DISCRECIONAL = "discrecional"


class Estado(str, Enum):
    ACTIVO = "ACTIVO"
    REVERSADO = "REVERSADO"


class EstrategiaPago(str, Enum):
    AVALANCHA = "avalancha"
    SNOWBALL = "snowball"


class TipoDeuda(str, Enum):
    TARJETA = "tarjeta"
    PAREJA = "pareja"
    EXTERNA = "externa"


class TipoCuentaTercero(str, Enum):
    """Dirección de una obligación respecto a la pareja."""

    POR_COBRAR = "POR_COBRAR"
    POR_PAGAR = "POR_PAGAR"


class MovimientoTarjeta(str, Enum):
    COMPRA = "COMPRA"
    PAGO = "PAGO"
    INTERES = "INTERES"
    CARGO = "CARGO"
    REVERSION = "REVERSION"


class TipoMovimientoPareja(str, Enum):
    LIQUIDACION = "liquidacion"
    AJUSTE = "ajuste"


class MovimientoAhorro(str, Enum):
    DEPOSITO = "DEPOSITO"
    RETIRO = "RETIRO"


# Aliases de compatibilidad: el código nuevo debe usar los Enum anteriores.
YO: Final[str] = Persona.YO.value
SARA: Final[str] = Persona.SARA.value
PERSONA1: Final[str] = YO
PERSONA2: Final[str] = SARA
PERSONAS_VALIDAS: Final[tuple[str, str]] = (YO, SARA)
RESP_P1: Final[str] = Responsabilidad.YO.value
RESP_P2: Final[str] = Responsabilidad.SARA.value
RESP_COMPARTIDO: Final[str] = Responsabilidad.COMPARTIDO.value
METODO_EFECTIVO: Final[str] = MetodoPago.EFECTIVO.value
METODO_DEBITO: Final[str] = MetodoPago.DEBITO.value
METODO_TARJETA: Final[str] = MetodoPago.TARJETA.value
PRIORIDAD_OBLIGATORIO: Final[str] = Prioridad.OBLIGATORIO.value
PRIORIDAD_DISCRECIONAL: Final[str] = Prioridad.DISCRECIONAL.value
ESTADO_ACTIVO: Final[str] = Estado.ACTIVO.value
ESTADO_REVERSADO: Final[str] = Estado.REVERSADO.value
ESTRATEGIA_AVALANCHA: Final[str] = EstrategiaPago.AVALANCHA.value
ESTRATEGIA_SNOWBALL: Final[str] = EstrategiaPago.SNOWBALL.value
DEUDA_TARJETA: Final[str] = TipoDeuda.TARJETA.value
DEUDA_PAREJA: Final[str] = TipoDeuda.PAREJA.value
DEUDA_EXTERNA: Final[str] = TipoDeuda.EXTERNA.value
MOV_COMPRA: Final[str] = MovimientoTarjeta.COMPRA.value
MOV_PAGO: Final[str] = MovimientoTarjeta.PAGO.value
MOV_INTERES: Final[str] = MovimientoTarjeta.INTERES.value
MOV_CARGO: Final[str] = MovimientoTarjeta.CARGO.value
MOV_REVERSION: Final[str] = MovimientoTarjeta.REVERSION.value
TIPO_LIQUIDACION_PAREJA: Final[str] = TipoMovimientoPareja.LIQUIDACION.value
TIPO_AJUSTE_PAREJA: Final[str] = TipoMovimientoPareja.AJUSTE.value
MOV_AHORRO_DEPOSITO: Final[str] = MovimientoAhorro.DEPOSITO.value
MOV_AHORRO_RETIRO: Final[str] = MovimientoAhorro.RETIRO.value

PATRON_MES: Final[re.Pattern[str]] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_TEXTO_CORTO: Final[int] = 120
MAX_TEXTO_LARGO: Final[int] = 500
UMBRAL_UTILIZACION_TARJETA_ALTA: Final[Decimal] = Decimal("0.70")
UMBRAL_CARGA_DEUDA_INGRESO_ALTA: Final[Decimal] = Decimal("0.35")
UMBRAL_TASA_AHORRO_SALUDABLE: Final[Decimal] = Decimal("0.10")
UMBRAL_INTERES_MENSUAL_ALTO: Final[Decimal] = Decimal("2.0")
MESES_FONDO_EMERGENCIA_META: Final[int] = 3


class ValidationError(ValueError):
    """Error de validación de dominio."""


class IntegrityError(ValueError):
    """Error de integridad financiera."""


class DuplicateOperationError(ValueError):
    """La operación ya fue registrada con el mismo transaction_uuid."""


class InsufficientFundsError(ValueError):
    """No hay cupo, saldo o deuda suficiente para completar la operación."""


EnumT = TypeVar("EnumT", bound=Enum)


def _validar_enum(valor: str | EnumT, clase: type[EnumT], campo: str) -> str:
    try:
        return clase(valor).value  # type: ignore[arg-type, return-value]
    except (ValueError, TypeError):
        opciones = ", ".join(member.value for member in clase)
        raise ValidationError(f"{campo} inválido: {valor!r}. Válidos: {opciones}.") from None


def validar_persona(persona: str | Persona) -> str:
    return _validar_enum(persona, Persona, "Persona")


def validar_responsabilidad(responsabilidad: str | Responsabilidad) -> str:
    return _validar_enum(responsabilidad, Responsabilidad, "Responsabilidad")


def validar_metodo_pago(metodo: str | MetodoPago) -> str:
    return _validar_enum(metodo, MetodoPago, "Método de pago")


def validar_prioridad(prioridad: str | Prioridad) -> str:
    return _validar_enum(prioridad, Prioridad, "Prioridad")


def validar_estado(estado: str | Estado) -> str:
    return _validar_enum(estado, Estado, "Estado")


def validar_estrategia(estrategia: str | EstrategiaPago) -> str:
    return _validar_enum(estrategia, EstrategiaPago, "Estrategia")


def validar_tipo_deuda(tipo_deuda: str | TipoDeuda) -> str:
    return _validar_enum(tipo_deuda, TipoDeuda, "Tipo de deuda")


def validar_tipo_cuenta_tercero(tipo: str | TipoCuentaTercero) -> str:
    return _validar_enum(tipo, TipoCuentaTercero, "Tipo de cuenta de tercero")


def validar_movimiento_tarjeta(movimiento: str | MovimientoTarjeta) -> str:
    return _validar_enum(movimiento, MovimientoTarjeta, "Movimiento de tarjeta")


def validar_movimiento_pareja(movimiento: str | TipoMovimientoPareja) -> str:
    return _validar_enum(movimiento, TipoMovimientoPareja, "Movimiento de pareja")


def validar_movimiento_ahorro(movimiento: str | MovimientoAhorro) -> str:
    return _validar_enum(movimiento, MovimientoAhorro, "Movimiento de ahorro")


def validar_mes(mes: str) -> str:
    if not isinstance(mes, str) or not PATRON_MES.fullmatch(mes):
        raise ValidationError("El mes debe tener formato AAAA-MM.")
    return mes


def validar_fecha(fecha: str) -> str:
    if not isinstance(fecha, str):
        raise ValidationError("La fecha debe tener formato AAAA-MM-DD.")
    try:
        dt.date.fromisoformat(fecha)
    except ValueError:
        raise ValidationError("La fecha debe tener formato AAAA-MM-DD y ser válida.") from None
    return fecha


def validar_texto(texto: object, campo: str, *, maximo: int = MAX_TEXTO_CORTO,
                  obligatorio: bool = True) -> str:
    if not isinstance(texto, str):
        raise ValidationError(f"{campo} debe ser texto.")
    limpio = texto.strip()
    if obligatorio and not limpio:
        raise ValidationError(f"{campo} es obligatorio.")
    if len(limpio) > maximo:
        raise ValidationError(f"{campo} no puede superar {maximo} caracteres.")
    return limpio


def to_pesos(valor: Any) -> int:
    """Convierte un importe finito a pesos COP enteros, con redondeo half-up."""
    if valor is None or isinstance(valor, bool):
        raise ValidationError("El valor monetario es inválido.")
    try:
        monto = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"Valor monetario inválido: {valor!r}") from None
    if not monto.is_finite():
        raise ValidationError("El valor monetario debe ser finito.")
    return int(monto.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validar_monto_no_negativo(valor: Any) -> int:
    monto = to_pesos(valor)
    if monto < 0:
        raise ValidationError("El monto no puede ser negativo.")
    return monto


def validar_monto_positivo(valor: Any) -> int:
    monto = to_pesos(valor)
    if monto <= 0:
        raise ValidationError("El monto debe ser mayor que cero.")
    return monto
