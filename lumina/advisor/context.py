"""Contexto financiero canónico y memoria de sesión del asesor de LÚMINA.

Dos responsabilidades, ambas de solo lectura:

1. ``ContextoFinanciero``: una única foto coherente del estado financiero que
   todos los análisis consumen. Evita que cada función vuelva a consultar
   SQLite y garantiza que un mismo informe no mezcle dos fotos distintas.
2. ``SesionAsesor``: memoria conversacional acotada al proceso en curso, para
   entender «¿y a España?» después de «¿podemos viajar?». No persiste nada en
   disco ni en la base.

Ninguna clase de este módulo escribe en la base de datos.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from ..core import database as db

# Lecturas que el asesor repite muchas veces al construir una foto. Se memoizan
# solo mientras dura la construcción del contexto; fuera de esa ventana la capa
# de datos sigue siendo la única fuente de verdad.
_FUNCIONES_CACHEABLES: tuple[str, ...] = (
    "get_gastos", "get_ingresos", "get_tarjetas", "get_compras_tarjeta",
    "get_pagos_deuda", "get_movimientos_ahorro", "get_ahorros", "get_gastos_fijos",
    "get_metas", "get_liquidaciones", "get_prestamos_terceros", "get_ajustes_tarjeta",
)

_candado = threading.RLock()
_profundidad_cache = 0
_originales: dict[str, Callable[..., Any]] = {}
_memoria: dict[tuple[str, tuple, tuple], Any] = {}
ESTADISTICAS: dict[str, int] = {"llamadas": 0, "aciertos": 0}


def _clave(nombre: str, args: tuple, kwargs: dict) -> tuple[str, tuple, tuple]:
    return (nombre, args, tuple(sorted(kwargs.items())))


def _envolver(nombre: str, original: Callable[..., Any]) -> Callable[..., Any]:
    def envuelto(*args: Any, **kwargs: Any) -> Any:
        clave = _clave(nombre, args, kwargs)
        ESTADISTICAS["llamadas"] += 1
        if clave in _memoria:
            ESTADISTICAS["aciertos"] += 1
            return _memoria[clave]
        resultado = original(*args, **kwargs)
        _memoria[clave] = resultado
        return resultado
    envuelto.__name__ = getattr(original, "__name__", nombre)
    envuelto.__doc__ = getattr(original, "__doc__", None)
    envuelto._lumina_cache = True  # type: ignore[attr-defined]
    return envuelto


@contextmanager
def cache_lecturas() -> Iterator[None]:
    """Memoiza lecturas de la base mientras dura el bloque.

    Es reentrante: los bloques anidados comparten la misma memoria y solo el
    más externo restaura las funciones originales. Las listas devueltas se
    comparten entre llamadas, así que el código que las consume no debe
    mutarlas (el asesor no lo hace).
    """
    global _profundidad_cache
    with _candado:
        _profundidad_cache += 1
        primero = _profundidad_cache == 1
        if primero:
            for nombre in _FUNCIONES_CACHEABLES:
                original = getattr(db, nombre, None)
                if callable(original) and not getattr(original, "_lumina_cache", False):
                    _originales[nombre] = original
                    setattr(db, nombre, _envolver(nombre, original))
    try:
        yield
    finally:
        with _candado:
            _profundidad_cache -= 1
            if _profundidad_cache <= 0:
                _profundidad_cache = 0
                for nombre, original in _originales.items():
                    setattr(db, nombre, original)
                _originales.clear()
                _memoria.clear()


def invalidar_cache() -> None:
    """Limpia la memoria de lecturas (tras registrar un movimiento real)."""
    with _candado:
        _memoria.clear()


# ---------------------------------------------------------------------------
# Contexto financiero
# ---------------------------------------------------------------------------

@dataclass
class ContextoFinanciero:
    """Foto única del estado financiero de un mes, con secciones perezosas.

    Cada sección se calcula una sola vez y se reutiliza. Así un informe
    completo no vuelve a recorrer el historial de gastos diez veces ni corre
    el riesgo de mezclar dos fotos tomadas en instantes distintos.
    """

    mes: str
    creado_en: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    _secciones: dict[str, Any] = field(default_factory=dict, repr=False)
    _constructores: dict[str, Callable[["ContextoFinanciero"], Any]] = field(default_factory=dict, repr=False)

    def registrar(self, nombre: str, constructor: Callable[["ContextoFinanciero"], Any]) -> None:
        """Declara cómo se construye una sección; no la ejecuta todavía."""
        self._constructores[nombre] = constructor

    def seccion(self, nombre: str) -> Any:
        """Devuelve una sección, calculándola la primera vez que se pide."""
        if nombre not in self._secciones:
            constructor = self._constructores.get(nombre)
            if constructor is None:
                raise KeyError(f"Sección desconocida del contexto: {nombre!r}")
            with cache_lecturas():
                self._secciones[nombre] = constructor(self)
        return self._secciones[nombre]

    def precargar(self, *nombres: str) -> "ContextoFinanciero":
        """Calcula varias secciones bajo un único bloque de caché."""
        with cache_lecturas():
            for nombre in nombres:
                self.seccion(nombre)
        return self

    def calculadas(self) -> list[str]:
        return sorted(self._secciones)

    def disponibles(self) -> list[str]:
        return sorted(self._constructores)

    # Accesos nombrados: leen mejor que seccion("perfil") en el código cliente.
    @property
    def perfil(self) -> dict[str, Any]:
        return self.seccion("perfil")

    @property
    def gastos_fijos_detectados(self) -> dict[str, Any]:
        return self.seccion("gastos_fijos_detectados")

    @property
    def cotejo_gastos_fijos(self) -> dict[str, Any]:
        return self.seccion("cotejo_gastos_fijos")

    @property
    def salud(self) -> dict[str, Any]:
        return self.seccion("salud")

    @property
    def capacidad(self) -> dict[str, Any]:
        return self.seccion("capacidad")

    @property
    def anomalias(self) -> list[dict[str, Any]]:
        return self.seccion("anomalias")

    @property
    def tendencias(self) -> dict[str, Any]:
        return self.seccion("tendencias")

    @property
    def comparacion(self) -> dict[str, Any]:
        return self.seccion("comparacion")

    @property
    def pareja(self) -> dict[str, Any]:
        return self.seccion("pareja")

    @property
    def integridad(self) -> dict[str, Any]:
        return self.seccion("integridad")

    @property
    def riesgo(self) -> dict[str, Any]:
        return self.seccion("riesgo")

    @property
    def oportunidades(self) -> list[dict[str, Any]]:
        return self.seccion("oportunidades")

    @property
    def hallazgos(self) -> list[dict[str, Any]]:
        return self.seccion("hallazgos")

    @property
    def proximos(self) -> list[dict[str, Any]]:
        return self.seccion("proximos")

    def resumen_estructural(self) -> dict[str, Any]:
        """Qué contiene el contexto, para depurar sin calcular nada nuevo."""
        return {"mes": self.mes, "creado_en": self.creado_en,
                "secciones_calculadas": self.calculadas(),
                "secciones_disponibles": self.disponibles()}


# Caché de contextos por mes con vida corta: la app es local y de un solo
# usuario, así que basta con expirar por tiempo e invalidar tras una escritura.
_TTL_SEGUNDOS = 90.0
_contextos: dict[str, tuple[float, ContextoFinanciero]] = {}


def obtener_contexto(mes: str, *, constructor: Callable[[str], ContextoFinanciero],
                     refrescar: bool = False) -> ContextoFinanciero:
    """Devuelve el contexto del mes, reutilizándolo si sigue vigente."""
    with _candado:
        entrada = _contextos.get(mes)
        if entrada and not refrescar and (time.monotonic() - entrada[0]) < _TTL_SEGUNDOS:
            return entrada[1]
        contexto = constructor(mes)
        _contextos[mes] = (time.monotonic(), contexto)
        return contexto


def invalidar_contextos(mes: str | None = None) -> None:
    """Descarta contextos cacheados. Llamar tras registrar movimientos reales."""
    with _candado:
        if mes is None:
            _contextos.clear()
        else:
            _contextos.pop(mes, None)
    invalidar_cache()


# ---------------------------------------------------------------------------
# Memoria conversacional
# ---------------------------------------------------------------------------

@dataclass
class TurnoAsesor:
    """Un intercambio: lo que se preguntó y lo que el asesor concluyó."""

    pregunta: str
    intencion: str
    monto: int | None = None
    entidades: dict[str, Any] = field(default_factory=dict)
    respuesta: str = ""
    datos: dict[str, Any] = field(default_factory=dict, repr=False)
    momento: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))


@dataclass
class SesionAsesor:
    """Memoria de conversación acotada al proceso; no persiste en disco.

    Solo guarda lo necesario para resolver referencias de seguimiento: la
    última intención, el último monto y las entidades mencionadas. No guarda
    la foto financiera ni datos sensibles más allá de los turnos recientes.
    """

    mes: str
    limite_turnos: int = 8
    turnos: deque[TurnoAsesor] = field(default_factory=lambda: deque(maxlen=8))

    def __post_init__(self) -> None:
        if self.turnos.maxlen != self.limite_turnos:
            self.turnos = deque(self.turnos, maxlen=self.limite_turnos)

    def registrar(self, turno: TurnoAsesor) -> None:
        self.turnos.append(turno)

    @property
    def ultimo(self) -> TurnoAsesor | None:
        return self.turnos[-1] if self.turnos else None

    def ultima_intencion(self) -> str | None:
        return self.ultimo.intencion if self.ultimo else None

    def ultimo_monto(self) -> int | None:
        for turno in reversed(self.turnos):
            if turno.monto:
                return turno.monto
        return None

    def ultima_entidad(self, clave: str) -> Any:
        for turno in reversed(self.turnos):
            valor = turno.entidades.get(clave)
            if valor:
                return valor
        return None

    def historial(self) -> list[dict[str, Any]]:
        return [{"pregunta": turno.pregunta, "intencion": turno.intencion,
                 "monto": turno.monto, "momento": turno.momento} for turno in self.turnos]

    def limpiar(self) -> None:
        self.turnos.clear()
