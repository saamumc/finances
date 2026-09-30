"""Capa de razonamiento del asesor financiero de Samuel y Sara.

Este módulo no guarda nada ni reemplaza a ``calculations``/``financial_engine``:
lee la foto financiera real que producen esos módulos, la interpreta y devuelve
estructuras explicables (qué pasa → por qué pasa → qué hacer).

Reglas que no se negocian aquí:

* No se inventan saldos, precios, tasas ni fechas. Si falta un dato se declara
  como faltante y se pide.
* Titularidad de tarjeta != responsabilidad económica. Nunca se reparte una
  compra solo porque se hizo con la tarjeta del otro.
* Ninguna función de este archivo escribe en SQLite.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import ceil
from statistics import mean, median, pstdev
from typing import Any, Callable, Iterable, Sequence

from . import context as ctx
from . import findings as af
from ..core import calculations as calc
from ..core import database as db
from ..core import engine
from ..constants import (
    SAMUEL, SARA, PERSONAS_VALIDAS, PRIORIDAD_DISCRECIONAL,
    RESP_COMPARTIDO, validar_mes,
)

NOMBRES: dict[str, str] = {SAMUEL: "Samuel", SARA: "Sara"}

# Parámetros de producto del asesor. Están aquí para que cambiar un criterio
# sea una decisión visible y no una constante escondida en medio de una regla.
from ..core.motor.reglas import reglas

PARAMS = reglas()

CLASE_FIJO = "gasto_fijo"
CLASE_PROBABLE_FIJO = "probable_gasto_fijo"
CLASE_RECURRENTE = "recurrente"
CLASE_PROBABLE_RECURRENTE = "probable_recurrente"
CLASE_SUSCRIPCION = "suscripcion"
CLASE_CUOTA_DEUDA = "cuota_deuda"
CLASE_TRANSFERENCIA = "transferencia"
CLASE_APORTE_AHORRO = "aporte_ahorro"
CLASE_VARIABLE = "variable"
CLASE_UNICO = "unico"

RESPUESTA_SI = "SI"
RESPUESTA_NO = "NO"
RESPUESTA_REVISAR = "PROBABLE / REVISAR"

# Pistas léxicas. No deciden solos: solo suman o restan evidencia frente al
# comportamiento real de la serie de montos y meses.
_PISTAS_SUSCRIPCION = (
    "netflix", "spotify", "disney", "hbo", "max", "prime", "youtube", "crunchyroll",
    "apple", "icloud", "google one", "dropbox", "chatgpt", "openai", "canva",
    "suscripcion", "membresia", "plan ", "streaming", "deezer", "paramount", "star",
)
_PISTAS_SERVICIO = (
    "internet", "wifi", "agua", "acueducto", "luz", "energia", "gas", "telefono",
    "celular", "plan datos", "claro", "movistar", "tigo", "wom", "etb", "epm",
    "administracion", "arriendo", "alquiler", "seguro", "poliza", "eps", "salud",
    "gimnasio", "gym", "smartfit", "colegio", "matricula", "guarderia", "parqueadero",
)
_PISTAS_CUOTA = ("cuota", "credito", "prestamo", "financiacion", "leasing", "libranza")
_PISTAS_TRANSFERENCIA = ("transferencia", "traslado", "nequi", "daviplata", "envio", "giro", "retiro cajero")
_PISTAS_AHORRO = ("ahorro", "cajita", "fondo", "meta ", "alcancia", "inversion", "cdt")


# ---------------------------------------------------------------------------
# Utilidades básicas
# ---------------------------------------------------------------------------

# El formateo vive en advisor_findings para que hallazgos y narración usen
# exactamente la misma representación de cifras.
_cop = af.fmt_cop
_pct = af.fmt_pct


def _nombre(persona: str | None) -> str:
    return NOMBRES.get(persona or "", "la pareja")


def indice_mes(mes: str) -> int:
    ano, numero = (int(parte) for parte in mes.split("-"))
    return ano * 12 + numero


def mes_anterior(mes: str) -> str:
    ano, numero = (int(parte) for parte in mes.split("-"))
    return f"{ano - 1:04d}-12" if numero == 1 else f"{ano:04d}-{numero - 1:02d}"


def mes_siguiente(mes: str) -> str:
    ano, numero = (int(parte) for parte in mes.split("-"))
    return f"{ano + 1:04d}-01" if numero == 12 else f"{ano:04d}-{numero + 1:02d}"


def ventana_meses(mes_final: str, cantidad: int) -> list[str]:
    """Ventana calendario continua (incluye meses sin movimientos)."""
    indice = indice_mes(mes_final)
    periodos: list[str] = []
    for salto in range(cantidad - 1, -1, -1):
        base = indice - salto - 1
        ano, numero = divmod(base, 12)
        periodos.append(f"{ano:04d}-{numero + 1:02d}")
    return periodos


def meses_con_actividad() -> list[str]:
    return engine.activity_months()


def normalizar_texto(valor: str) -> str:
    """Minúsculas sin tildes ni signos: base para comparar comercios."""
    if not isinstance(valor, str):
        return ""
    plano = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    limpio = "".join(caracter if caracter.isalnum() or caracter.isspace() else " " for caracter in plano.lower())
    return " ".join(limpio.split())


def _clave_comercio(nombre: str) -> str:
    """Clave estable de comercio: quita numeración de cuotas y meses sueltos."""
    texto = normalizar_texto(nombre)
    texto = re.sub(r"\b\d+\s*/\s*\d+\b", " ", texto)          # 3/12
    texto = re.sub(r"\bcuota\s*\d+\b", " cuota ", texto)
    texto = re.sub(r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
                   r"septiembre|setiembre|octubre|noviembre|diciembre)\b", " ", texto)
    texto = re.sub(r"\b\d{4}\b", " ", texto)
    texto = re.sub(r"\b\d+\b", " ", texto)
    return " ".join(texto.split())


def _contiene(texto: str, pistas: Iterable[str]) -> bool:
    return any(pista in texto for pista in pistas)


def coeficiente_variacion(valores: Sequence[int | float]) -> float:
    """Dispersión relativa; 0 significa exactamente el mismo monto siempre."""
    numeros = [float(valor) for valor in valores if valor is not None]
    if len(numeros) < 2:
        return 0.0
    promedio = mean(numeros)
    if not promedio:
        return 0.0
    return pstdev(numeros) / abs(promedio)


def _clamp(valor: float, minimo: float = 0.0, maximo: float = 1.0) -> float:
    return max(minimo, min(maximo, valor))


def _meses(cantidad: Any) -> str:
    """Pluraliza correctamente: «1 mes» / «7 meses»."""
    try:
        numero = int(cantidad)
    except (TypeError, ValueError):
        return "un plazo no calculable"
    return "1 mes" if numero == 1 else f"{numero} meses"


def etiqueta_confianza(valor: float) -> str:
    if valor >= PARAMS["confianza_alta"]:
        return "Alta"
    if valor >= PARAMS["confianza_media"]:
        return "Media"
    return "Baja"


def _dividir(numerador: float, denominador: float) -> float | None:
    return (numerador / denominador) if denominador else None


def _primer_dia_habitual(fechas: Sequence[dt.date]) -> int | None:
    return round(mean([fecha.day for fecha in fechas])) if fechas else None


def _persona_de_gasto(gasto: dict[str, Any]) -> str:
    """Responsabilidad económica del gasto, no quién puso la tarjeta."""
    if gasto.get("responsabilidad") == RESP_COMPARTIDO:
        return "compartido"
    return gasto.get("responsabilidad") or RESP_COMPARTIDO


def _etiqueta_responsabilidad(valor: str) -> str:
    if valor == RESP_COMPARTIDO:
        return "Samuel y Sara"
    return NOMBRES.get(valor, "sin definir")


# ---------------------------------------------------------------------------
# Clasificación de series de gasto
# ---------------------------------------------------------------------------

@dataclass
class SerieGasto:
    """Todas las apariciones de un mismo concepto a lo largo del historial."""

    clave: str
    nombre: str
    categoria: str
    movimientos: list[dict[str, Any]] = field(default_factory=list)

    @property
    def valores(self) -> list[int]:
        return [int(item["valor"]) for item in self.movimientos]

    @property
    def meses(self) -> list[str]:
        return sorted({item["mes"] for item in self.movimientos})

    @property
    def fechas(self) -> list[dt.date]:
        salida: list[dt.date] = []
        for item in self.movimientos:
            if item.get("fecha"):
                try:
                    salida.append(dt.date.fromisoformat(item["fecha"]))
                except ValueError:
                    continue
        return sorted(salida)

    @property
    def ultimo_mes(self) -> str:
        return self.meses[-1] if self.meses else ""

    @property
    def promedio(self) -> int:
        valores = self.valores
        return round(mean(valores)) if valores else 0

    @property
    def mediana(self) -> int:
        valores = self.valores
        return round(median(valores)) if valores else 0

    @property
    def variacion(self) -> float:
        return coeficiente_variacion(self.valores)

    def modo(self, campo: str) -> Any:
        valores = [item.get(campo) for item in self.movimientos if item.get(campo) is not None]
        return Counter(valores).most_common(1)[0][0] if valores else None


def construir_series(*, hasta_mes: str | None = None, meses_historial: int | None = None,
                     categoria: str | None = None) -> list[SerieGasto]:
    """Agrupa gastos activos por comercio/categoría dentro de una ventana."""
    historial = meses_historial or int(PARAMS["recurrencia_historial_meses"])
    limite = indice_mes(hasta_mes) if hasta_mes else None
    minimo = (limite - historial + 1) if limite else None
    agrupado: dict[tuple[str, str], SerieGasto] = {}
    for gasto in db.get_gastos():
        indice = indice_mes(gasto["mes"])
        if limite is not None and not (minimo <= indice <= limite):
            continue
        if categoria and normalizar_texto(gasto["categoria"]) != normalizar_texto(categoria):
            continue
        clave = _clave_comercio(gasto["nombre"])
        if not clave:
            continue
        llave = (clave, normalizar_texto(gasto["categoria"]))
        serie = agrupado.get(llave)
        if serie is None:
            serie = SerieGasto(clave=clave, nombre=gasto["nombre"], categoria=gasto["categoria"])
            agrupado[llave] = serie
        serie.movimientos.append(gasto)
        serie.nombre = gasto["nombre"]
    return sorted(agrupado.values(), key=lambda serie: (-serie.promedio * len(serie.meses), serie.clave))


def _frecuencia_detectada(meses: Sequence[str]) -> tuple[str, float]:
    """Deduce la periodicidad a partir de los huecos entre apariciones."""
    if len(meses) < 2:
        return "indeterminada", 0.0
    indices = [indice_mes(mes) for mes in meses]
    huecos = [posterior - anterior for anterior, posterior in zip(indices, indices[1:])]
    if not huecos:
        return "indeterminada", 0.0
    hueco_tipico = median(huecos)
    regularidad = _clamp(1.0 - coeficiente_variacion(huecos))
    if hueco_tipico <= 1.2:
        return "mensual", regularidad
    if hueco_tipico <= 2.4:
        return "bimestral", regularidad
    if hueco_tipico <= 3.6:
        return "trimestral", regularidad
    if hueco_tipico <= 6.5:
        return "semestral", regularidad
    if hueco_tipico <= 12.5:
        return "anual", regularidad
    return "irregular", regularidad


def _puntaje_recurrencia(serie: SerieGasto, mes_referencia: str) -> dict[str, Any]:
    """Score explicable: cada componente aporta y se puede mostrar al usuario."""
    meses = serie.meses
    ocurrencias = len(meses)
    variacion = serie.variacion
    frecuencia, regularidad = _frecuencia_detectada(meses)
    texto = normalizar_texto(f"{serie.nombre} {serie.categoria}")

    # Un cobro fijo golpea una vez por período. Tres compras del mismo comercio
    # en el mismo mes describen un hábito de gasto, no una obligación fija.
    apariciones_por_mes = len(serie.movimientos) / len(meses) if meses else 0.0

    componentes: dict[str, float] = {}
    # Repetición: 3 meses distintos es el mínimo defendible; 6 ya es fuerte.
    componentes["repeticion"] = _clamp((ocurrencias - 2) / 4) * 0.30
    # Estabilidad del monto.
    if variacion <= PARAMS["variacion_estable"]:
        estabilidad = 1.0
    elif variacion <= PARAMS["variacion_moderada"]:
        estabilidad = 0.6
    elif variacion <= PARAMS["variacion_alta"]:
        estabilidad = 0.25
    else:
        estabilidad = 0.0
    componentes["estabilidad_monto"] = estabilidad * 0.25
    # Regularidad del calendario.
    componentes["regularidad"] = regularidad * 0.15 if frecuencia != "irregular" else 0.0
    # Unicidad: un cargo por período, no varios.
    if apariciones_por_mes <= 1.1:
        unicidad = 1.0
    elif apariciones_por_mes <= 1.5:
        unicidad = 0.5
    else:
        unicidad = 0.0
    componentes["unicidad_mensual"] = unicidad * 0.15
    # Pistas de nombre (suscripción o servicio domiciliario).
    pista = 0.0
    if _contiene(texto, _PISTAS_SUSCRIPCION):
        pista = 1.0
    elif _contiene(texto, _PISTAS_SERVICIO):
        pista = 0.8
    elif _contiene(texto, _PISTAS_CUOTA):
        pista = 0.5
    componentes["pista_nombre"] = pista * 0.10
    # Vigencia: si dejó de aparecer, la evidencia pierde fuerza.
    inactividad = indice_mes(mes_referencia) - indice_mes(serie.ultimo_mes) if serie.ultimo_mes else 99
    if inactividad <= 1:
        vigencia = 1.0
    elif inactividad <= int(PARAMS["meses_inactivo_para_dudar"]):
        vigencia = 0.6
    else:
        vigencia = 0.0
    componentes["vigencia"] = vigencia * 0.05
    puntaje = _clamp(sum(componentes.values()))
    return {"puntaje": round(puntaje, 4), "componentes": {k: round(v, 4) for k, v in componentes.items()},
            "frecuencia": frecuencia, "regularidad": round(regularidad, 4),
            "variacion": round(variacion, 4), "ocurrencias": ocurrencias,
            "apariciones_por_mes": round(apariciones_por_mes, 2),
            "meses_sin_aparecer": inactividad}


def clasificar_serie(serie: SerieGasto, mes_referencia: str) -> dict[str, Any]:
    """Devuelve la clase de gasto con su evidencia, sin convertir nada."""
    metricas = _puntaje_recurrencia(serie, mes_referencia)
    texto = normalizar_texto(f"{serie.nombre} {serie.categoria}")
    ocurrencias = metricas["ocurrencias"]
    variacion = metricas["variacion"]
    puntaje = metricas["puntaje"]

    frecuente_en_el_mes = metricas["apariciones_por_mes"] > 1.5
    if _contiene(texto, _PISTAS_AHORRO):
        clase = CLASE_APORTE_AHORRO
    elif _contiene(texto, _PISTAS_TRANSFERENCIA):
        clase = CLASE_TRANSFERENCIA
    elif _contiene(texto, _PISTAS_CUOTA) and ocurrencias >= 2:
        clase = CLASE_CUOTA_DEUDA
    elif ocurrencias <= 1:
        clase = CLASE_UNICO
    elif ocurrencias == 2:
        clase = CLASE_PROBABLE_RECURRENTE
    elif frecuente_en_el_mes:
        # Varias compras del mismo comercio dentro del mes: es gasto recurrente
        # de consumo, no una obligación fija que se pueda registrar como tal.
        clase = CLASE_RECURRENTE if variacion <= PARAMS["variacion_alta"] else CLASE_VARIABLE
    elif puntaje >= PARAMS["confianza_alta"] and _contiene(texto, _PISTAS_SUSCRIPCION):
        clase = CLASE_SUSCRIPCION
    elif puntaje >= PARAMS["confianza_alta"] and variacion <= PARAMS["variacion_moderada"]:
        clase = CLASE_FIJO
    elif puntaje >= PARAMS["confianza_media"]:
        clase = CLASE_PROBABLE_FIJO
    elif ocurrencias >= int(PARAMS["recurrencia_min_ocurrencias"]) and variacion > PARAMS["variacion_alta"]:
        clase = CLASE_VARIABLE
    elif ocurrencias >= int(PARAMS["recurrencia_min_ocurrencias"]):
        clase = CLASE_RECURRENTE
    else:
        clase = CLASE_VARIABLE
    return {**metricas, "clase": clase, "confianza": etiqueta_confianza(puntaje)}


def _evidencia_serie(serie: SerieGasto, analisis: dict[str, Any]) -> list[str]:
    meses = serie.meses
    evidencia = [
        f"Aparece en {len(meses)} meses distintos ({', '.join(meses[-6:])}).",
        f"Monto promedio {_cop(serie.promedio)} con variación de {_pct(analisis['variacion'], 1)}.",
        f"Patrón de aparición: {analisis['frecuencia']}.",
    ]
    fechas = serie.fechas
    dia = _primer_dia_habitual(fechas)
    if dia:
        evidencia.append(f"Suele registrarse alrededor del día {dia} del mes.")
    metodo = serie.modo("metodo_pago")
    if metodo:
        evidencia.append(f"Método de pago más frecuente: {metodo}.")
    if analisis["meses_sin_aparecer"] >= int(PARAMS["meses_inactivo_para_dudar"]):
        evidencia.append(f"No aparece desde hace {analisis['meses_sin_aparecer']} meses; conviene confirmar si sigue vigente.")
    return evidencia


# ---------------------------------------------------------------------------
# Detección automática de gastos fijos
# ---------------------------------------------------------------------------

def _gastos_fijos_registrados() -> list[dict[str, Any]]:
    return db.get_gastos_fijos(solo_activos=False)


def _coincide_con_gasto_fijo(clave: str, registrados: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Empareja por clave de comercio y, si no, por solapamiento de palabras."""
    tokens_clave = set(clave.split())
    for registrado in registrados:
        clave_registrada = _clave_comercio(registrado["nombre"])
        if not clave_registrada:
            continue
        if clave_registrada == clave:
            return registrado
        tokens_registrados = set(clave_registrada.split())
        if not tokens_clave or not tokens_registrados:
            continue
        interseccion = tokens_clave & tokens_registrados
        if interseccion and len(interseccion) >= min(len(tokens_clave), len(tokens_registrados)):
            return registrado
    return None


def _tarjeta_nombre(tarjeta_id: int | None) -> str | None:
    if not tarjeta_id:
        return None
    tarjeta = next((item for item in db.get_tarjetas() if item["id"] == tarjeta_id), None)
    return tarjeta["nombre"] if tarjeta else None


def _candidato_desde_serie(serie: SerieGasto, mes_referencia: str,
                           registrados: Sequence[dict[str, Any]]) -> dict[str, Any]:
    analisis = clasificar_serie(serie, mes_referencia)
    registrado = _coincide_con_gasto_fijo(serie.clave, registrados)
    responsabilidad = serie.modo("responsabilidad") or RESP_COMPARTIDO
    propietario = serie.modo("pagador") or SAMUEL
    tarjeta_id = serie.modo("tarjeta_id")
    valores = serie.valores
    return {
        "nombre": serie.nombre,
        "clave": serie.clave,
        "categoria": serie.categoria,
        "clase": analisis["clase"],
        "ocurrencias": analisis["ocurrencias"],
        "movimientos": len(serie.movimientos),
        "frecuencia": analisis["frecuencia"],
        "monto_promedio": serie.promedio,
        "monto_mediano": serie.mediana,
        "monto_minimo": min(valores) if valores else 0,
        "monto_maximo": max(valores) if valores else 0,
        "variacion_monto": analisis["variacion"],
        "apariciones_por_mes": analisis["apariciones_por_mes"],
        "regularidad": analisis["regularidad"],
        "ultima_aparicion": serie.ultimo_mes,
        "ultima_fecha": serie.fechas[-1].isoformat() if serie.fechas else None,
        "meses_detectados": serie.meses,
        "meses_sin_aparecer": analisis["meses_sin_aparecer"],
        "dia_estimado": _primer_dia_habitual(serie.fechas),
        "metodo_pago": serie.modo("metodo_pago"),
        "tarjeta_id": tarjeta_id,
        "tarjeta": _tarjeta_nombre(tarjeta_id),
        "pagador": propietario,
        "pagador_nombre": _nombre(propietario),
        "responsabilidad": responsabilidad,
        "responsabilidad_nombre": _etiqueta_responsabilidad(responsabilidad),
        "prioridad_registrada": serie.modo("prioridad"),
        "confianza": analisis["confianza"],
        "puntaje_confianza": analisis["puntaje"],
        "componentes_confianza": analisis["componentes"],
        "ya_registrado": registrado is not None,
        "gasto_fijo_id": registrado["id"] if registrado else None,
        "gasto_fijo_valor": registrado["valor"] if registrado else None,
        "impacto_mensual_estimado": serie.promedio if analisis["frecuencia"] == "mensual" else round(
            serie.promedio / max(_factor_meses(analisis["frecuencia"]), 1)),
        "evidencia": _evidencia_serie(serie, analisis),
    }


def _factor_meses(frecuencia: str) -> int:
    return {"mensual": 1, "bimestral": 2, "trimestral": 3, "semestral": 6, "anual": 12}.get(frecuencia, 1)


def detectar_gastos_fijos(mes: str | None = None, *, meses_historial: int | None = None,
                          incluir_registrados: bool = False) -> dict[str, Any]:
    """Candidatos a gasto fijo detectados en el historial real de gastos.

    Nunca crea el gasto fijo: devuelve evidencia para que la pareja confirme.
    """
    referencia = validar_mes(mes) if mes else (meses_con_actividad() or [dt.date.today().strftime("%Y-%m")])[-1]
    registrados = _gastos_fijos_registrados()
    series = construir_series(hasta_mes=referencia, meses_historial=meses_historial)
    candidatos: list[dict[str, Any]] = []
    descartados: list[dict[str, Any]] = []
    clases_relevantes = {CLASE_FIJO, CLASE_PROBABLE_FIJO, CLASE_SUSCRIPCION, CLASE_CUOTA_DEUDA}
    for serie in series:
        candidato = _candidato_desde_serie(serie, referencia, registrados)
        if candidato["clase"] in clases_relevantes and candidato["ocurrencias"] >= int(PARAMS["recurrencia_min_ocurrencias"]):
            if candidato["ya_registrado"] and not incluir_registrados:
                candidato["nota"] = "Ya existe un gasto fijo con este nombre; sirve para verificar el monto registrado."
            candidatos.append(candidato)
        elif candidato["ocurrencias"] >= 2:
            descartados.append({"nombre": candidato["nombre"], "clase": candidato["clase"],
                                "ocurrencias": candidato["ocurrencias"],
                                "razon": _razon_descarte(candidato)})
    candidatos.sort(key=lambda item: (-item["puntaje_confianza"], -item["impacto_mensual_estimado"]))
    nuevos = [item for item in candidatos if not item["ya_registrado"]]
    return {
        "mes_referencia": referencia,
        "candidatos": candidatos if incluir_registrados else nuevos,
        "todos": candidatos,
        "ya_registrados": [item for item in candidatos if item["ya_registrado"]],
        "descartados": descartados[:20],
        "total_mensual_estimado": sum(item["impacto_mensual_estimado"] for item in nuevos),
        "cantidad_nuevos": len(nuevos),
        "nota": "Son patrones detectados en los gastos registrados. No se convirtió ninguno en gasto fijo: "
                "la confirmación es de ustedes.",
    }


def _razon_descarte(candidato: dict[str, Any]) -> str:
    if candidato["clase"] in (CLASE_RECURRENTE, CLASE_VARIABLE) and candidato.get("apariciones_por_mes", 0) > 1.5:
        return ("Aparece varias veces dentro del mismo mes: es gasto recurrente de consumo, no un cobro fijo "
                "que se pague una vez por período.")
    if candidato["ocurrencias"] < int(PARAMS["recurrencia_min_ocurrencias"]):
        return f"Solo aparece {candidato['ocurrencias']} veces; dos apariciones no alcanzan para llamarlo fijo."
    if candidato["variacion_monto"] > PARAMS["variacion_alta"]:
        return f"El monto varía demasiado ({_pct(candidato['variacion_monto'], 1)}) para tratarlo como compromiso fijo."
    if candidato["clase"] == CLASE_TRANSFERENCIA:
        return "Parece un movimiento entre cuentas, no un gasto de consumo."
    if candidato["clase"] == CLASE_APORTE_AHORRO:
        return "Parece un aporte a ahorro; se maneja como reserva, no como gasto fijo."
    return "El patrón no es lo bastante regular para sostener la clasificación."


def analizar_posible_gasto_fijo(descripcion: str, *, monto: int | None = None,
                                categoria: str | None = None, mes: str | None = None) -> dict[str, Any]:
    """Responde «¿esto podría ser un gasto fijo?» con estructura y evidencia."""
    texto = (descripcion or "").strip()
    if not texto:
        raise ValueError("Indica el nombre o comercio del gasto que quieres revisar.")
    referencia = validar_mes(mes) if mes else (meses_con_actividad() or [dt.date.today().strftime("%Y-%m")])[-1]
    clave = _clave_comercio(texto)
    registrados = _gastos_fijos_registrados()
    series = construir_series(hasta_mes=referencia, categoria=categoria)
    tokens = set(clave.split())
    coincidencias = [serie for serie in series
                     if serie.clave == clave or (tokens and tokens & set(serie.clave.split()))]
    if not coincidencias:
        return {
            "consulta": texto, "es_candidato": False, "respuesta_corta": RESPUESTA_NO,
            "confianza": 0.0, "confianza_etiqueta": "Baja", "clasificacion": CLASE_UNICO,
            "razon": "No encuentro gastos registrados con ese nombre en el historial.",
            "evidencia": [], "ocurrencias": 0, "monto_promedio": monto or 0,
            "frecuencia": "indeterminada", "ya_registrado": _coincide_con_gasto_fijo(clave, registrados) is not None,
            "accion_sugerida": "registrar_primero",
            "respuesta": f"Todavía no tengo historial de «{texto}». Si es un cobro nuevo, regístralo un par de meses "
                         "y lo detecto solo; si ya sabes que se repite cada mes, puedes crearlo como gasto fijo directamente.",
            "datos_faltantes": ["historial del comercio"],
        }
    serie = max(coincidencias, key=lambda item: (len(item.meses), item.promedio))
    candidato = _candidato_desde_serie(serie, referencia, registrados)
    puntaje = candidato["puntaje_confianza"]
    clase = candidato["clase"]
    if clase in (CLASE_FIJO, CLASE_SUSCRIPCION) and puntaje >= PARAMS["confianza_alta"]:
        corta, accion = RESPUESTA_SI, "registrar"
    elif clase in (CLASE_PROBABLE_FIJO, CLASE_RECURRENTE, CLASE_CUOTA_DEUDA) or puntaje >= PARAMS["confianza_media"]:
        corta, accion = RESPUESTA_REVISAR, "revisar"
    else:
        corta, accion = RESPUESTA_NO, "no_registrar"
    if monto and candidato["monto_promedio"]:
        desvio = abs(monto - candidato["monto_promedio"]) / candidato["monto_promedio"]
        if desvio > PARAMS["variacion_alta"]:
            candidato["evidencia"].append(
                f"El monto que preguntas ({_cop(monto)}) se aleja {_pct(desvio, 0)} del promedio histórico.")
            if corta == RESPUESTA_SI:
                corta, accion = RESPUESTA_REVISAR, "revisar"
    razon = _razon_gasto_fijo(candidato, corta)
    return {
        "consulta": texto,
        "es_candidato": corta != RESPUESTA_NO,
        "respuesta_corta": corta,
        "confianza": puntaje,
        "confianza_etiqueta": candidato["confianza"],
        "clasificacion": clase,
        "razon": razon,
        "evidencia": candidato["evidencia"],
        "ocurrencias": candidato["ocurrencias"],
        "monto_promedio": candidato["monto_promedio"],
        "monto_minimo": candidato["monto_minimo"],
        "monto_maximo": candidato["monto_maximo"],
        "variacion_monto": candidato["variacion_monto"],
        "frecuencia": candidato["frecuencia"],
        "ultima_aparicion": candidato["ultima_aparicion"],
        "meses_detectados": candidato["meses_detectados"],
        "categoria": candidato["categoria"],
        "metodo_pago": candidato["metodo_pago"],
        "tarjeta": candidato["tarjeta"],
        "pagador": candidato["pagador_nombre"],
        "responsabilidad": candidato["responsabilidad_nombre"],
        "ya_registrado": candidato["ya_registrado"],
        "gasto_fijo_id": candidato["gasto_fijo_id"],
        "accion_sugerida": accion,
        "respuesta": _redactar_gasto_fijo(candidato, corta, razon),
        "candidato": candidato,
    }


def _razon_gasto_fijo(candidato: dict[str, Any], corta: str) -> str:
    if corta == RESPUESTA_SI:
        return (f"Aparece en {candidato['ocurrencias']} meses con frecuencia {candidato['frecuencia']} "
                f"y una variación de {_pct(candidato['variacion_monto'], 1)} sobre {_cop(candidato['monto_promedio'])}.")
    if corta == RESPUESTA_REVISAR:
        if candidato["variacion_monto"] > PARAMS["variacion_moderada"]:
            return (f"Se repite ({candidato['ocurrencias']} meses), pero el monto cambia bastante "
                    f"({_cop(candidato['monto_minimo'])} a {_cop(candidato['monto_maximo'])}): parece recurrente de monto variable.")
        if candidato["meses_sin_aparecer"] >= int(PARAMS["meses_inactivo_para_dudar"]):
            return f"Se repetía con regularidad, pero no aparece hace {candidato['meses_sin_aparecer']} meses."
        return f"Hay patrón, pero con {candidato['ocurrencias']} apariciones todavía no es concluyente."
    return _razon_descarte(candidato)


def _redactar_gasto_fijo(candidato: dict[str, Any], corta: str, razon: str) -> str:
    nombre = candidato["nombre"]
    if corta == RESPUESTA_SI:
        base = (f"Sí, lo trataría como gasto fijo. {nombre} {razon[0].lower() + razon[1:]} "
                f"No lo registré automáticamente porque prefiero que confirmes que sigue vigente.")
    elif corta == RESPUESTA_REVISAR:
        base = f"Probablemente, pero lo dejaría en revisión. {razon}"
    else:
        base = f"No lo llamaría gasto fijo todavía. {razon}"
    if candidato["ya_registrado"]:
        registrado = candidato["gasto_fijo_valor"]
        if registrado and abs(registrado - candidato["monto_promedio"]) > max(round(registrado * 0.1), 1000):
            base += (f" Ojo: ya está registrado como gasto fijo por {_cop(registrado)}, "
                     f"pero el promedio real es {_cop(candidato['monto_promedio'])}. Vale la pena actualizarlo.")
        else:
            base += " Ya está registrado como gasto fijo, así que no hay nada que crear."
    elif corta != RESPUESTA_NO:
        base += f" Si lo registran, el compromiso mensual subiría {_cop(candidato['impacto_mensual_estimado'])}."
    return base


# ---------------------------------------------------------------------------
# Perfil financiero completo
# ---------------------------------------------------------------------------

def _bloque_ingresos(mes: str) -> dict[str, Any]:
    """Ingresos confirmados del mes y su comportamiento reciente."""
    ingresos = db.get_ingresos(mes)
    por_persona = {persona: sum(item["valor"] for item in ingresos if item["persona"] == persona)
                   for persona in PERSONAS_VALIDAS}
    historial = {persona: engine.ingresos_historicos(persona, as_of_month=mes) for persona in PERSONAS_VALIDAS}
    referencia = engine.income_reference(mes)
    previo = db.get_ingresos(mes_anterior(mes))
    total_previo = sum(item["valor"] for item in previo)
    total = sum(por_persona.values())
    variacion = _dividir(total - total_previo, total_previo) if total_previo else None
    fuentes = Counter(item["concepto"].strip() for item in ingresos)
    estabilidad = "sin datos"
    serie = [registro["monto"] for registro in referencia.get("historial", [])][-6:]
    if len(serie) >= 3:
        dispersity = coeficiente_variacion(serie)
        estabilidad = "estable" if dispersity <= 0.10 else "variable" if dispersity <= 0.30 else "muy variable"
    return {
        "mes": mes, "total": total, "detalle": ingresos,
        "samuel": por_persona[SAMUEL], "sara": por_persona[SARA],
        "participacion_samuel": _dividir(por_persona[SAMUEL], total),
        "participacion_sara": _dividir(por_persona[SARA], total),
        "total_mes_anterior": total_previo, "variacion_vs_anterior": variacion,
        "fuentes": [{"concepto": nombre, "veces": veces} for nombre, veces in fuentes.most_common()],
        "estabilidad": estabilidad, "referencia": referencia, "historial_por_persona": historial,
        "confirmado": bool(total),
    }


def _bloque_gastos(mes: str) -> dict[str, Any]:
    """Gastos del mes divididos por prioridad, categoría, persona y método."""
    gastos = db.get_gastos(mes)
    total = sum(item["valor"] for item in gastos)
    obligatorios = sum(item["valor"] for item in gastos if item["prioridad"] != PRIORIDAD_DISCRECIONAL)
    discrecionales = total - obligatorios
    categorias: dict[str, int] = defaultdict(int)
    metodos: dict[str, int] = defaultdict(int)
    por_persona = {SAMUEL: 0, SARA: 0}
    compartido = 0
    for gasto in gastos:
        categorias[gasto["categoria"]] += gasto["valor"]
        metodos[gasto["metodo_pago"]] += gasto["valor"]
        por_persona[SAMUEL] += gasto["monto_p1"]
        por_persona[SARA] += gasto["monto_p2"]
        if gasto["responsabilidad"] == RESP_COMPARTIDO:
            compartido += gasto["valor"]
    fijos = calc.resumen_gastos_fijos(mes)
    detectados = detectar_gastos_fijos(mes)
    ranking = sorted(categorias.items(), key=lambda item: item[1], reverse=True)
    mayores = sorted(gastos, key=lambda item: item["valor"], reverse=True)[:5]
    return {
        "mes": mes, "total": total, "cantidad": len(gastos),
        "obligatorios": obligatorios, "discrecionales": discrecionales,
        "porcentaje_discrecional": _dividir(discrecionales, total),
        "por_categoria": [{"categoria": nombre, "monto": monto, "participacion": _dividir(monto, total)}
                          for nombre, monto in ranking],
        "por_metodo": dict(metodos),
        "consumo_samuel": por_persona[SAMUEL], "consumo_sara": por_persona[SARA],
        "gasto_compartido": compartido,
        "gastos_fijos_registrados": fijos,
        "gastos_fijos_detectados": detectados,
        "carga_fija": fijos["porcentaje_ingreso"],
        "mayores": [{"nombre": item["nombre"], "valor": item["valor"], "categoria": item["categoria"],
                     "fecha": item.get("fecha"), "metodo": item["metodo_pago"]} for item in mayores],
        "gasto_variable_estimado": max(total - fijos["registrado_este_mes"], 0),
    }


def _bloque_tarjetas(mes: str) -> dict[str, Any]:
    """Estado de cada tarjeta incluyendo actividad del mes y presión de pago."""
    tarjetas = calc.resumen_tarjetas()
    detalle: list[dict[str, Any]] = []
    for tarjeta in tarjetas:
        actividad = calc.resumen_mensual_tarjeta(tarjeta["id"], mes)
        crecimiento = actividad["compras"] + actividad["intereses"] + actividad["cargos"] - actividad["pagos"]
        minimo = min(tarjeta["pago_minimo"], tarjeta["saldo_deuda"])
        proyeccion = tarjeta["saldo_deuda"] + round(tarjeta["saldo_deuda"] * tarjeta["interes_mensual"] / 100) - minimo
        detalle.append({
            **tarjeta,
            "titular_nombre": _nombre(tarjeta["propietario"]),
            "actividad_mes": actividad,
            "crecimiento_neto_mes": crecimiento,
            "pago_minimo_efectivo": minimo,
            "deuda_proyectada_pagando_minimo": max(proyeccion, 0),
            "presion_pago": _dividir(minimo, max(sum(i["valor"] for i in db.get_ingresos(mes)), 1)),
            "datos_faltantes": [nombre for nombre, falta in (
                ("tasa mensual", not tarjeta["interes_mensual"]),
                ("fecha de corte", not tarjeta.get("fecha_corte")),
                ("fecha de pago", not tarjeta.get("fecha_pago")),
                ("origen de la deuda histórica", bool(tarjeta.get("saldo_historico_pendiente"))),
            ) if falta],
        })
    deuda = sum(item["saldo_deuda"] for item in tarjetas)
    cupo = sum(item["cupo_total"] for item in tarjetas)
    return {
        "tarjetas": detalle, "cantidad": len(detalle),
        "deuda_total": deuda, "cupo_total": cupo,
        "cupo_disponible": cupo - deuda,
        "utilizacion_global": _dividir(deuda, cupo) or 0.0,
        "pago_minimo_total": sum(item["pago_minimo_efectivo"] for item in detalle),
        "interes_estimado_mensual": sum(item["interes_estimado"] for item in tarjetas),
        "deuda_samuel": sum(item["deuda_persona1"] for item in tarjetas),
        "deuda_sara": sum(item["deuda_persona2"] for item in tarjetas),
        "creciendo": [item["nombre"] for item in detalle if item["crecimiento_neto_mes"] > 0],
    }


def _bloque_deuda(mes: str, tarjetas: dict[str, Any], ingresos: dict[str, Any]) -> dict[str, Any]:
    """Deuda total y estrategias comparadas con el excedente registrado."""
    terceros = calc.resumen_deudas_terceros()
    flujo = calc.flujo_caja_mes(mes)
    presupuesto = max(int(flujo["ahorro"]), 0) + tarjetas["pago_minimo_total"]
    try:
        avalancha = engine.unified_debt_payoff(presupuesto, "avalancha", start_month=mes)
        snowball = engine.unified_debt_payoff(presupuesto, "snowball", start_month=mes)
    except ValueError:
        avalancha = snowball = {"viable": False, "note": "No se pudo proyectar con el presupuesto calculado."}
    total = tarjetas["deuda_total"] + terceros["por_pagar"]
    orden_avalancha = [t for t in sorted(tarjetas["tarjetas"], key=lambda item: (-item["interes_mensual"], -item["saldo_deuda"]))
                       if t["saldo_deuda"]]
    orden_snowball = [t for t in sorted(tarjetas["tarjetas"], key=lambda item: (item["saldo_deuda"], -item["interes_mensual"]))
                      if t["saldo_deuda"]]
    return {
        "total": total,
        "tarjetas": tarjetas["deuda_total"],
        "externa_por_pagar": terceros["por_pagar"],
        "externa_por_cobrar": terceros["por_cobrar"],
        "detalle_terceros": terceros,
        "samuel": tarjetas["deuda_samuel"],
        "sara": tarjetas["deuda_sara"],
        "interes_mensual_estimado": tarjetas["interes_estimado_mensual"],
        "pagos_minimos": tarjetas["pago_minimo_total"],
        "carga_sobre_ingreso": _dividir(total, ingresos["total"]),
        "minimos_sobre_ingreso": _dividir(tarjetas["pago_minimo_total"], ingresos["total"]),
        "presupuesto_proyectado": presupuesto,
        "avalancha": avalancha, "snowball": snowball,
        "orden_avalancha": [{"id": t["id"], "nombre": t["nombre"], "tasa": t["interes_mensual"],
                             "saldo": t["saldo_deuda"]} for t in orden_avalancha],
        "orden_snowball": [{"id": t["id"], "nombre": t["nombre"], "tasa": t["interes_mensual"],
                            "saldo": t["saldo_deuda"]} for t in orden_snowball],
    }


def _bloque_ahorro(mes: str) -> dict[str, Any]:
    """Cajitas, metas, aportes del mes y fondo de emergencia."""
    resumen = calc.resumen_ahorros()
    metas = calc.progreso_metas()
    movimientos = db.get_movimientos_ahorro(mes=mes)
    depositos = sum(item["monto"] for item in movimientos if item["tipo"] == "DEPOSITO")
    retiros = sum(item["monto"] for item in movimientos if item["tipo"] == "RETIRO")
    emergencia = engine.emergency_fund(mes)
    fatiga = engine.savings_fatigue(mes)
    ingresos = sum(item["valor"] for item in db.get_ingresos(mes))
    proximas = sorted([meta for meta in metas if not meta["cumplida"] and meta.get("fecha_objetivo")],
                      key=lambda meta: meta["fecha_objetivo"])[:3]
    return {
        "total": resumen["total"], "fondos": resumen["fondos"], "metas": metas,
        "depositos_mes": depositos, "retiros_mes": retiros, "aporte_neto_mes": depositos - retiros,
        "tasa_ahorro_mes": _dividir(depositos - retiros, ingresos),
        "emergencia": emergencia, "fatiga": fatiga,
        "metas_atrasadas": [meta["nombre"] for meta in metas if meta.get("atrasada")],
        "aporte_requerido_metas": sum(int(meta["ahorro_necesario_mensual"] or 0) for meta in metas if not meta["cumplida"]),
        "proximas_metas": [{"nombre": meta["nombre"], "fecha_objetivo": meta["fecha_objetivo"],
                            "faltante": meta["faltante"], "necesario_mensual": meta["ahorro_necesario_mensual"]}
                           for meta in proximas],
        "promedio_mensual": calc.promedio_ahorro(),
    }


def _bloque_pareja(mes: str) -> dict[str, Any]:
    """Quién aportó, quién consumió y quién le debe a quién."""
    mensual = calc.balance_historico_pareja(mes)
    historico = calc.balance_historico_pareja()
    detalle = historico["detalle"]
    saldo_samuel = int(historico[f"balance_neto_{SAMUEL}"])
    saldo_sara = int(historico[f"balance_neto_{SARA}"])
    if saldo_samuel > 0:
        acreedor, deudor, monto = "Samuel", "Sara", saldo_samuel
    elif saldo_sara > 0:
        acreedor, deudor, monto = "Sara", "Samuel", saldo_sara
    else:
        acreedor = deudor = None
        monto = 0
    aportado_total = sum(detalle[persona]["aportado"] for persona in PERSONAS_VALIDAS)
    consumido_total = sum(detalle[persona]["consumido"] for persona in PERSONAS_VALIDAS)
    liquidaciones = db.get_liquidaciones(mes)
    return {
        "mes": mes, "mensual": mensual, "historico": historico,
        "saldo_samuel": saldo_samuel, "saldo_sara": saldo_sara,
        "acreedor": acreedor, "deudor": deudor, "monto_pendiente": max(monto, 0),
        "aportado_samuel": detalle[SAMUEL]["aportado"], "aportado_sara": detalle[SARA]["aportado"],
        "consumido_samuel": detalle[SAMUEL]["consumido"], "consumido_sara": detalle[SARA]["consumido"],
        "participacion_aportes_samuel": _dividir(detalle[SAMUEL]["aportado"], aportado_total),
        "participacion_consumo_samuel": _dividir(detalle[SAMUEL]["consumido"], consumido_total),
        "liquidaciones_mes": liquidaciones,
        "cuadra": historico["cuadra"], "descuadre": historico["descuadre"],
        "explicacion_samuel": calc.explicar_balance(SAMUEL, mes),
        "explicacion_sara": calc.explicar_balance(SARA, mes),
    }


def _bloque_flujo(mes: str, ingresos: dict[str, Any], gastos: dict[str, Any],
                  tarjetas: dict[str, Any], ahorro: dict[str, Any]) -> dict[str, Any]:
    """Cuánto entra, cuánto está comprometido y cuánto queda realmente libre."""
    flujo = calc.flujo_caja_mes(mes)
    patrimonio = calc.patrimonio_liquido(mes)
    fijos = gastos["gastos_fijos_registrados"]
    comprometido = fijos["pendiente_este_mes"] + tarjetas["pago_minimo_total"] + ahorro["aporte_requerido_metas"]
    margen = round(ingresos["total"] * float(PARAMS["margen_seguridad_pct"]))
    disponible = patrimonio["disponible_gastos_recurrentes"]
    libre = disponible - tarjetas["pago_minimo_total"] - margen
    return {
        **flujo,
        "liquidez_operativa": patrimonio["liquidez_operativa"],
        "reservado": patrimonio["reservado_metas"],
        "disponible": disponible,
        "comprometido": comprometido,
        "gastos_fijos_pendientes": fijos["pendiente_este_mes"],
        "pagos_minimos": tarjetas["pago_minimo_total"],
        "aporte_metas_requerido": ahorro["aporte_requerido_metas"],
        "margen_seguridad": margen,
        "libre_estimado": libre,
        "cubre_compromisos": disponible >= comprometido,
        "por_persona": calc.liquidez_por_persona(mes),
        "caja_por_persona": calc.caja_real_por_persona(mes),
    }


def perfil_financiero(mes: str) -> dict[str, Any]:
    """Foto completa e interpretada del mes. Solo lectura."""
    mes = validar_mes(mes)
    ingresos = _bloque_ingresos(mes)
    gastos = _bloque_gastos(mes)
    tarjetas = _bloque_tarjetas(mes)
    deuda = _bloque_deuda(mes, tarjetas, ingresos)
    ahorro = _bloque_ahorro(mes)
    pareja = _bloque_pareja(mes)
    flujo = _bloque_flujo(mes, ingresos, gastos, tarjetas, ahorro)
    calidad = engine.data_quality(mes)
    return {"mes": mes, "ingresos": ingresos, "gastos": gastos, "tarjetas": tarjetas,
            "deuda": deuda, "ahorro": ahorro, "pareja": pareja, "flujo": flujo,
            "calidad_datos": calidad,
            "generado_en": dt.datetime.now().isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# Dimensiones de salud financiera (internas, nunca un puntaje único)
# ---------------------------------------------------------------------------

def _dimension(nombre: str, valor: float | None, nivel: str, lectura: str,
               evidencia: dict[str, Any], explicacion: str) -> dict[str, Any]:
    return {"dimension": nombre, "valor": valor, "nivel": nivel, "lectura": lectura,
            "evidencia": evidencia, "explicacion": explicacion}


def _nivel_por_umbral(valor: float, bueno: float, atencion: float, invertido: bool = False) -> str:
    if invertido:
        if valor <= bueno:
            return "bueno"
        return "atencion" if valor <= atencion else "critico"
    if valor >= bueno:
        return "bueno"
    return "atencion" if valor >= atencion else "critico"


def dimensiones_salud(mes: str, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Siete dimensiones explicables. No se reducen a un número único."""
    perfil = perfil or perfil_financiero(mes)
    ingresos, gastos = perfil["ingresos"], perfil["gastos"]
    tarjetas, deuda = perfil["tarjetas"], perfil["deuda"]
    ahorro, flujo = perfil["ahorro"], perfil["flujo"]
    dimensiones: list[dict[str, Any]] = []

    # 1. Liquidez
    cobertura = _dividir(flujo["disponible"], max(gastos["obligatorios"], 1)) or 0.0
    dimensiones.append(_dimension(
        "liquidez", round(cobertura, 2), _nivel_por_umbral(cobertura, 1.0, 0.5),
        f"El disponible cubre {cobertura:.1f} meses de gasto obligatorio.",
        {"disponible": flujo["disponible"], "gasto_obligatorio": gastos["obligatorios"],
         "reservado": flujo["reservado"]},
        "Mide cuánto aguanta la caja libre si los ingresos se detuvieran, usando el gasto obligatorio registrado."))

    # 2. Presión de deuda
    carga = deuda["carga_sobre_ingreso"] or 0.0
    dimensiones.append(_dimension(
        "presion_deuda", round(carga, 3),
        _nivel_por_umbral(carga, PARAMS["deuda_ingreso_alta"], PARAMS["deuda_ingreso_alta"] * 2, invertido=True),
        f"La deuda total equivale a {_pct(carga, 0)} del ingreso del mes.",
        {"deuda_total": deuda["total"], "ingreso": ingresos["total"],
         "minimos": deuda["pagos_minimos"], "interes_mensual": deuda["interes_mensual_estimado"]},
        "Compara la deuda viva con el ingreso registrado. No es una regla bancaria, es una referencia de presión."))

    # 3. Utilización de tarjetas
    uso = tarjetas["utilizacion_global"]
    dimensiones.append(_dimension(
        "utilizacion_tarjetas", round(uso, 3),
        _nivel_por_umbral(uso, PARAMS["utilizacion_atencion"], PARAMS["utilizacion_alta"], invertido=True),
        f"Están usando {_pct(uso, 0)} del cupo total registrado.",
        {"deuda": tarjetas["deuda_total"], "cupo": tarjetas["cupo_total"],
         "tarjetas_creciendo": tarjetas["creciendo"]},
        "Una utilización alta reduce el margen ante imprevistos y encarece cualquier decisión futura."))

    # 4. Progreso de ahorro
    emergencia = ahorro["emergencia"]
    progreso = emergencia["progress"]
    dimensiones.append(_dimension(
        "progreso_ahorro", round(progreso, 3), _nivel_por_umbral(progreso, 0.6, 0.25),
        f"El fondo de emergencia va en {_pct(progreso, 0)} de su referencia.",
        {"actual": emergencia["current"], "objetivo": emergencia["target"],
         "meses_cubiertos": round(emergencia["coverage_months"], 2), "aporte_mes": ahorro["aporte_neto_mes"]},
        "Usa la meta de la cajita de emergencia o tres meses de gasto obligatorio cuando no hay meta definida."))

    # 5. Carga de gastos fijos
    carga_fija = gastos["carga_fija"]
    if carga_fija is None:
        dimensiones.append(_dimension(
            "carga_gastos_fijos", None, "sin_datos", "No hay ingreso registrado este mes para calcular la carga fija.",
            {"total_fijos": gastos["gastos_fijos_registrados"]["total_mensual_equivalente"]},
            "Sin ingreso del mes no puedo expresar los gastos fijos como porcentaje."))
    else:
        dimensiones.append(_dimension(
            "carga_gastos_fijos", round(carga_fija, 3),
            _nivel_por_umbral(carga_fija, PARAMS["carga_fija_atencion"], PARAMS["carga_fija_alta"], invertido=True),
            f"Los gastos fijos registrados equivalen a {_pct(carga_fija, 0)} del ingreso.",
            {"total_fijos": gastos["gastos_fijos_registrados"]["total_mensual_equivalente"],
             "ingreso": ingresos["total"],
             "detectados_sin_registrar": gastos["gastos_fijos_detectados"]["total_mensual_estimado"]},
            "Entre más alta, menos margen queda para decidir. Incluye solo los gastos fijos ya registrados."))

    # 6. Estabilidad del flujo
    historial = [calc.flujo_caja_mes(periodo)["ahorro"] for periodo in ventana_meses(mes, 4)]
    positivos = sum(1 for valor in historial if valor > 0)
    estabilidad = positivos / len(historial) if historial else 0.0
    dimensiones.append(_dimension(
        "estabilidad_flujo", round(estabilidad, 2), _nivel_por_umbral(estabilidad, 0.75, 0.5),
        f"{positivos} de los últimos {len(historial)} meses cerraron con flujo positivo.",
        {"historial": historial, "ingreso_estabilidad": ingresos["estabilidad"],
         "flujo_actual": flujo["ahorro"]},
        "Mide consistencia, no monto: un mes bueno aislado no sostiene una decisión grande."))

    # 7. Progreso de metas
    metas = [meta for meta in ahorro["metas"] if not meta["cumplida"]]
    if metas:
        avance = mean([meta["progreso"] for meta in metas])
        atrasadas = len(ahorro["metas_atrasadas"])
        dimensiones.append(_dimension(
            "progreso_metas", round(avance, 3), _nivel_por_umbral(avance, 0.5, 0.2),
            f"El avance promedio de las metas activas es {_pct(avance, 0)}" +
            (f" y {atrasadas} van atrasadas." if atrasadas else "."),
            {"metas_activas": len(metas), "atrasadas": ahorro["metas_atrasadas"],
             "aporte_requerido": ahorro["aporte_requerido_metas"], "flujo": flujo["ahorro"]},
            "Compara el avance real con lo que cada meta necesita al mes según su fecha objetivo."))
    else:
        dimensiones.append(_dimension(
            "progreso_metas", None, "sin_datos", "No hay metas activas registradas.", {},
            "Sin metas registradas no hay progreso que evaluar."))

    niveles = Counter(item["nivel"] for item in dimensiones)
    return {"mes": mes, "dimensiones": dimensiones,
            "resumen_niveles": dict(niveles),
            "puntos_criticos": [item["dimension"] for item in dimensiones if item["nivel"] == "critico"],
            "puntos_fuertes": [item["dimension"] for item in dimensiones if item["nivel"] == "bueno"],
            "nota": "Son siete lecturas separadas a propósito: reducirlas a un solo puntaje escondería el problema real."}


# ---------------------------------------------------------------------------
# Anomalías
# ---------------------------------------------------------------------------

def _anomalia(tipo: str, titulo: str, detalle: str, evidencia: dict[str, Any],
              severidad: str = "media", confianza: str = "Media") -> dict[str, Any]:
    return {"tipo": tipo, "titulo": titulo, "detalle": detalle, "evidencia": evidencia,
            "severidad": severidad, "confianza": confianza,
            "nota": "Posible anomalía: es una señal para revisar, no una afirmación de error."}


def detectar_anomalias(mes: str, perfil: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Señales que merecen revisión humana. Nunca afirma que algo es un error."""
    perfil = perfil or perfil_financiero(mes)
    gastos_mes = db.get_gastos(mes)
    hallazgos: list[dict[str, Any]] = []

    # Gasto inusualmente grande. Se compara contra el historial del mismo
    # concepto y, si no hay, contra su categoría: comparar el arriendo con la
    # mediana global marcaría como anomalía algo perfectamente normal.
    historial_concepto: dict[str, list[int]] = defaultdict(list)
    historial_categoria: dict[str, list[int]] = defaultdict(list)
    for item in db.get_gastos():
        if item["mes"] >= mes:
            continue
        historial_concepto[_clave_comercio(item["nombre"])].append(item["valor"])
        historial_categoria[normalizar_texto(item["categoria"])].append(item["valor"])
    conocidos = {item["clave"] for item in
                 detectar_gastos_fijos(mes, incluir_registrados=True)["todos"]}
    for gasto in gastos_mes:
        clave = _clave_comercio(gasto["nombre"])
        propio = historial_concepto.get(clave, [])
        if len(propio) >= 2:
            referencia, base = median(propio), f"lo que suele costar «{gasto['nombre']}»"
        elif clave in conocidos:
            continue  # concepto recurrente conocido sin variación relevante
        else:
            propio = historial_categoria.get(normalizar_texto(gasto["categoria"]), [])
            if len(propio) < 3:
                continue
            referencia, base = median(propio), f"el gasto típico de {gasto['categoria']}"
        if referencia and gasto["valor"] >= referencia * float(PARAMS["gasto_grande_vs_mediana"]):
            hallazgos.append(_anomalia(
                "gasto_grande", f"Gasto grande: {gasto['nombre']}",
                f"{_cop(gasto['valor'])} es {gasto['valor'] / referencia:.1f} veces {base} "
                f"({_cop(referencia)}).",
                {"gasto_id": gasto["id"], "valor": gasto["valor"], "referencia": round(referencia),
                 "categoria": gasto["categoria"]},
                severidad="media", confianza="Alta"))

    # Categorías por encima de su patrón (reutiliza el motor existente)
    for item in engine.anomalies(mes):
        hallazgos.append(_anomalia(
            "categoria_elevada", f"{item['category']} está por encima de su patrón",
            f"{_cop(item['current'])} este mes contra un promedio de {_cop(item['average'])} "
            f"({item['multiplier']:.1f}x).",
            item, severidad="media", confianza="Media"))

    # Posibles duplicados: mismo nombre, mismo monto, fechas cercanas
    por_firma: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for gasto in gastos_mes:
        por_firma[(_clave_comercio(gasto["nombre"]), gasto["valor"])].append(gasto)
    for (clave, valor), items in por_firma.items():
        if len(items) < 2:
            continue
        fechas = sorted(dt.date.fromisoformat(item["fecha"]) for item in items if item.get("fecha"))
        cercanos = any((posterior - anterior).days <= int(PARAMS["ventana_duplicado_dias"])
                       for anterior, posterior in zip(fechas, fechas[1:])) if len(fechas) >= 2 else True
        if cercanos:
            hallazgos.append(_anomalia(
                "posible_duplicado", f"Posible duplicado: {items[0]['nombre']}",
                f"Hay {len(items)} registros por {_cop(valor)} muy cercanos entre sí. Puede ser real "
                "(dos compras iguales) o un registro repetido.",
                {"ids": [item["id"] for item in items], "valor": valor, "clave": clave},
                severidad="media", confianza="Media"))

    # Comercio repetido muchas veces en el mes
    conteo = Counter(_clave_comercio(item["nombre"]) for item in gastos_mes)
    for clave, veces in conteo.most_common(3):
        if veces >= 5 and clave:
            total = sum(item["valor"] for item in gastos_mes if _clave_comercio(item["nombre"]) == clave)
            hallazgos.append(_anomalia(
                "comercio_repetido", f"Compras frecuentes en «{clave}»",
                f"{veces} compras este mes por {_cop(total)} en total. Vale la pena mirar si es un hábito o un caso puntual.",
                {"clave": clave, "veces": veces, "total": total},
                severidad="baja", confianza="Alta"))

    # Tarjetas que crecen y dependencia del pago mínimo
    for tarjeta in perfil["tarjetas"]["tarjetas"]:
        if tarjeta["crecimiento_neto_mes"] > 0 and tarjeta["saldo_deuda"]:
            hallazgos.append(_anomalia(
                "tarjeta_creciendo", f"La deuda de {tarjeta['nombre']} creció este mes",
                f"Compras, intereses y cargos superaron los pagos en {_cop(tarjeta['crecimiento_neto_mes'])}.",
                {"tarjeta_id": tarjeta["id"], "crecimiento": tarjeta["crecimiento_neto_mes"],
                 "actividad": tarjeta["actividad_mes"]},
                severidad="alta", confianza="Alta"))
        pagos = tarjeta["actividad_mes"]["pagos"]
        minimo = tarjeta["pago_minimo_efectivo"]
        if minimo and pagos and abs(pagos - minimo) <= max(round(minimo * 0.05), 1000) and tarjeta["saldo_deuda"]:
            hallazgos.append(_anomalia(
                "dependencia_minimo", f"{tarjeta['nombre']} se está pagando solo con el mínimo",
                f"El pago registrado ({_cop(pagos)}) coincide con el mínimo. Pagar solo el mínimo alarga la deuda "
                "y concentra el costo en intereses.",
                {"tarjeta_id": tarjeta["id"], "pago": pagos, "minimo": minimo,
                 "interes_estimado": tarjeta["interes_estimado"]},
                severidad="alta", confianza="Media"))

    # Retiros de ahorro
    retiros = [item for item in db.get_movimientos_ahorro(mes=mes) if item["tipo"] == "RETIRO"]
    if retiros:
        total_retiros = sum(item["monto"] for item in retiros)
        hallazgos.append(_anomalia(
            "retiro_ahorro", "Hubo retiros de las cajitas este mes",
            f"{len(retiros)} retiros por {_cop(total_retiros)}. Si fueron para cubrir gastos corrientes, "
            "la reserva está funcionando como caja y conviene revisarlo.",
            {"movimientos": [item["id"] for item in retiros], "total": total_retiros},
            severidad="media", confianza="Alta"))

    # Caída de ingresos
    ingresos = perfil["ingresos"]
    if ingresos["variacion_vs_anterior"] is not None and ingresos["variacion_vs_anterior"] <= -float(PARAMS["caida_ingreso"]):
        hallazgos.append(_anomalia(
            "ingreso_menor", "El ingreso del mes bajó frente al anterior",
            f"{_cop(ingresos['total'])} contra {_cop(ingresos['total_mes_anterior'])} "
            f"({_pct(ingresos['variacion_vs_anterior'], 0)}). Puede ser un mes incompleto o un cambio real.",
            {"actual": ingresos["total"], "anterior": ingresos["total_mes_anterior"]},
            severidad="alta", confianza="Media"))

    # Carga de gastos fijos alta
    carga = perfil["gastos"]["carga_fija"]
    if carga is not None and carga >= float(PARAMS["carga_fija_alta"]):
        hallazgos.append(_anomalia(
            "carga_fija_alta", "Los gastos fijos ocupan buena parte del ingreso",
            f"Equivalen a {_pct(carga, 0)} del ingreso registrado.",
            {"carga": carga, "total": perfil["gastos"]["gastos_fijos_registrados"]["total_mensual_equivalente"]},
            severidad="alta", confianza="Alta"))

    # Descuadre de pareja
    pareja = perfil["pareja"]
    if not pareja["cuadra"]:
        hallazgos.append(_anomalia(
            "descuadre_pareja", "El libro de pareja no cuadra",
            f"Queda una diferencia de {_cop(abs(pareja['descuadre']))} entre aportes, consumos y deuda pendiente.",
            {"descuadre": pareja["descuadre"]},
            severidad="alta", confianza="Alta"))

    orden = {"alta": 0, "media": 1, "baja": 2}
    return sorted(hallazgos, key=lambda item: orden.get(item["severidad"], 3))


# ---------------------------------------------------------------------------
# Inteligencia mensual: comparaciones y tendencias
# ---------------------------------------------------------------------------

def _totales_por_categoria(mes: str) -> dict[str, int]:
    totales: dict[str, int] = defaultdict(int)
    for gasto in db.get_gastos(mes):
        totales[gasto["categoria"]] += gasto["valor"]
    return dict(totales)


def comparar_meses(mes: str, referencia: str | None = None) -> dict[str, Any]:
    """Compara dos meses en ingresos, gastos, categorías, deuda y ahorro."""
    mes = validar_mes(mes)
    anterior = validar_mes(referencia) if referencia else mes_anterior(mes)
    hay_datos = bool(db.get_ingresos(anterior) or db.get_gastos(anterior))
    flujo_actual, flujo_previo = calc.flujo_caja_mes(mes), calc.flujo_caja_mes(anterior)
    cat_actual, cat_previo = _totales_por_categoria(mes), _totales_por_categoria(anterior)
    categorias: list[dict[str, Any]] = []
    for categoria in sorted(set(cat_actual) | set(cat_previo)):
        ahora, antes = cat_actual.get(categoria, 0), cat_previo.get(categoria, 0)
        categorias.append({"categoria": categoria, "actual": ahora, "anterior": antes,
                           "diferencia": ahora - antes, "variacion": _dividir(ahora - antes, antes)})
    categorias.sort(key=lambda item: abs(item["diferencia"]), reverse=True)
    deuda_actual = calc.deuda_total_tarjetas()
    ahorro_actual = calc.ahorro_mensual(mes)
    ahorro_previo = calc.ahorro_mensual(anterior)
    fijos_actual = calc.resumen_gastos_fijos(mes)
    variacion_gasto = int(flujo_actual["salidas"]) - int(flujo_previo["salidas"])
    subidas = [item for item in categorias if item["diferencia"] > 0]
    explicacion_variacion = []
    for item in subidas[:3]:
        parte = _dividir(item["diferencia"], variacion_gasto) if variacion_gasto > 0 else None
        explicacion_variacion.append({**item, "participacion_en_el_cambio": parte})
    return {
        "mes": mes, "mes_comparado": anterior, "suficiente_historial": hay_datos,
        "ingresos": {"actual": int(flujo_actual["ingresos"]), "anterior": int(flujo_previo["ingresos"]),
                     "diferencia": int(flujo_actual["ingresos"]) - int(flujo_previo["ingresos"]),
                     "variacion": _dividir(int(flujo_actual["ingresos"]) - int(flujo_previo["ingresos"]),
                                           int(flujo_previo["ingresos"]))},
        "gastos": {"actual": int(flujo_actual["salidas"]), "anterior": int(flujo_previo["salidas"]),
                   "diferencia": variacion_gasto,
                   "variacion": _dividir(variacion_gasto, int(flujo_previo["salidas"]))},
        "flujo": {"actual": int(flujo_actual["ahorro"]), "anterior": int(flujo_previo["ahorro"]),
                  "diferencia": int(flujo_actual["ahorro"]) - int(flujo_previo["ahorro"])},
        "ahorro": {"actual": ahorro_actual, "anterior": ahorro_previo,
                   "diferencia": ahorro_actual - ahorro_previo},
        "categorias": categorias,
        "principales_subidas": explicacion_variacion,
        "principales_bajadas": [item for item in categorias if item["diferencia"] < 0][:3],
        "deuda_tarjetas_actual": deuda_actual,
        "carga_fija_sobre_ingreso": fijos_actual["porcentaje_ingreso"],
        "nota": "Comparación directa entre dos meses registrados. Un mes en curso puede estar incompleto.",
    }


def tendencias(mes: str, meses: int = 6) -> dict[str, Any]:
    """Series de los últimos meses para distinguir tendencia de mes puntual."""
    mes = validar_mes(mes)
    periodos = ventana_meses(mes, meses)
    filas: list[dict[str, Any]] = []
    for periodo in periodos:
        flujo = calc.flujo_caja_mes(periodo)
        filas.append({
            "mes": periodo,
            "ingresos": int(flujo["ingresos"]),
            "gastos": int(flujo["salidas"]),
            "flujo": int(flujo["ahorro"]),
            "ahorro_neto": calc.ahorro_mensual(periodo),
            "gastos_registrados": len(db.get_gastos(periodo)),
        })
    con_datos = [fila for fila in filas if fila["ingresos"] or fila["gastos"]]
    def _tendencia(clave: str) -> dict[str, Any]:
        serie = [fila[clave] for fila in con_datos]
        if len(serie) < 3:
            return {"direccion": "sin_datos", "serie": serie,
                    "nota": "Se necesitan al menos tres meses registrados para hablar de tendencia."}
        recientes = serie[-3:]
        if recientes[0] < recientes[1] < recientes[2]:
            direccion = "subiendo"
        elif recientes[0] > recientes[1] > recientes[2]:
            direccion = "bajando"
        else:
            direccion = "estable"
        promedio = mean(serie)
        return {"direccion": direccion, "serie": serie, "promedio": round(promedio),
                "ultimo_vs_promedio": _dividir(serie[-1] - promedio, abs(promedio) or 1)}
    categorias: dict[str, list[int]] = defaultdict(list)
    for periodo in periodos:
        totales = _totales_por_categoria(periodo)
        for categoria in set(totales) | set(categorias):
            categorias[categoria].append(totales.get(categoria, 0))
    crecientes = []
    for categoria, serie in categorias.items():
        utiles = serie[-3:]
        if len(utiles) == 3 and utiles[0] and utiles[0] < utiles[1] < utiles[2]:
            crecientes.append({"categoria": categoria, "serie": utiles,
                               "crecimiento": _dividir(utiles[-1] - utiles[0], utiles[0])})
    return {"mes": mes, "meses": periodos, "filas": filas,
            "ingresos": _tendencia("ingresos"), "gastos": _tendencia("gastos"),
            "flujo": _tendencia("flujo"), "ahorro": _tendencia("ahorro_neto"),
            "categorias_en_alza": sorted(crecientes, key=lambda item: item["crecimiento"] or 0, reverse=True)[:5],
            "meses_con_datos": len(con_datos)}


# ---------------------------------------------------------------------------
# Análisis de pareja
# ---------------------------------------------------------------------------

def analisis_pareja(mes: str, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aportes, consumo, desbalance y liquidaciones, sin repartir nada solo."""
    perfil = perfil or perfil_financiero(mes)
    pareja = perfil["pareja"]
    ingresos = perfil["ingresos"]
    gastos = db.get_gastos(mes)
    ambiguos = [gasto for gasto in gastos
                if gasto["responsabilidad"] == RESP_COMPARTIDO and gasto["monto_p1"] == 0 and gasto["monto_p2"] == 0]
    tarjeta_de_otro = []
    for gasto in gastos:
        if not gasto.get("tarjeta_id"):
            continue
        tarjeta = next((item for item in db.get_tarjetas() if item["id"] == gasto["tarjeta_id"]), None)
        if tarjeta and gasto["responsabilidad"] in PERSONAS_VALIDAS and tarjeta["propietario"] != gasto["responsabilidad"]:
            tarjeta_de_otro.append({"gasto_id": gasto["id"], "nombre": gasto["nombre"], "valor": gasto["valor"],
                                    "titular": _nombre(tarjeta["propietario"]),
                                    "responsable": _nombre(gasto["responsabilidad"])})
    proporcion_ingreso = _dividir(ingresos["samuel"], ingresos["total"])
    proporcion_consumo = _dividir(perfil["gastos"]["consumo_samuel"],
                                  perfil["gastos"]["consumo_samuel"] + perfil["gastos"]["consumo_sara"])
    desbalance = None
    if proporcion_ingreso is not None and proporcion_consumo is not None:
        desbalance = proporcion_consumo - proporcion_ingreso
    lectura = "equilibrado"
    if desbalance is not None and abs(desbalance) >= 0.15:
        lectura = "Samuel consume más de lo que aporta al ingreso" if desbalance > 0 else \
                  "Sara consume más de lo que aporta al ingreso"
    return {
        "mes": mes,
        "saldo_pendiente": pareja["monto_pendiente"],
        "deudor": pareja["deudor"], "acreedor": pareja["acreedor"],
        "aportes": {"Samuel": pareja["aportado_samuel"], "Sara": pareja["aportado_sara"]},
        "consumo": {"Samuel": pareja["consumido_samuel"], "Sara": pareja["consumido_sara"]},
        "consumo_del_mes": {"Samuel": perfil["gastos"]["consumo_samuel"], "Sara": perfil["gastos"]["consumo_sara"]},
        "ingresos_del_mes": {"Samuel": ingresos["samuel"], "Sara": ingresos["sara"]},
        "participacion_ingreso_samuel": proporcion_ingreso,
        "participacion_consumo_samuel": proporcion_consumo,
        "desbalance": desbalance, "lectura_desbalance": lectura,
        "liquidaciones_mes": pareja["liquidaciones_mes"],
        "gastos_sin_distribucion": ambiguos,
        "compras_en_tarjeta_del_otro": tarjeta_de_otro,
        "cuadra": pareja["cuadra"], "descuadre": pareja["descuadre"],
        "regla": "La titularidad de la tarjeta no define quién asume el gasto: la responsabilidad se lee del registro, "
                 "no del plástico usado.",
        "requiere_confirmacion": [item["nombre"] for item in ambiguos][:5],
    }


# ---------------------------------------------------------------------------
# Asequibilidad: ¿podemos gastar esto?
# ---------------------------------------------------------------------------

def capacidad_discrecional(mes: str, perfil: dict[str, Any] | None = None,
                           margen_porcentaje: int | None = None) -> dict[str, Any]:
    """Cuánto se puede gastar hoy sin tocar compromisos ya registrados."""
    perfil = perfil or perfil_financiero(mes)
    flujo, tarjetas, ahorro, gastos = perfil["flujo"], perfil["tarjetas"], perfil["ahorro"], perfil["gastos"]
    porcentaje = float(margen_porcentaje) / 100 if margen_porcentaje is not None else float(PARAMS["margen_seguridad_pct"])
    margen = round(perfil["ingresos"]["total"] * porcentaje)
    base = flujo["disponible"]
    compromisos = {
        "pagos_minimos_tarjetas": tarjetas["pago_minimo_total"],
        "gastos_fijos_pendientes": gastos["gastos_fijos_registrados"]["pendiente_este_mes"],
        "metas_obligatorias": sum(int(meta["ahorro_necesario_mensual"] or 0) for meta in ahorro["metas"]
                                  if meta["prioridad"] != PRIORIDAD_DISCRECIONAL and not meta["cumplida"]),
        "margen_seguridad": margen,
    }
    # Los gastos fijos pendientes ya están descontados dentro de "disponible".
    descuento = compromisos["pagos_minimos_tarjetas"] + compromisos["metas_obligatorias"] + margen
    maximo = max(base - descuento, 0)
    return {"mes": mes, "base_disponible": base, "compromisos": compromisos,
            "descuento_total": descuento, "maximo_discrecional": maximo,
            "margen_porcentaje": round(porcentaje * 100),
            "flujo_del_mes": int(flujo["ahorro"]),
            "confianza": perfil["calidad_datos"]["level"],
            "explicacion": "Parte de la liquidez operativa registrada, descuenta gastos fijos pendientes, "
                           "pagos mínimos de tarjeta, aportes obligatorios a metas y un margen de seguridad."}


def evaluar_asequibilidad(mes: str, monto: int | None = None, *, concepto: str = "esta salida",
                          margen_porcentaje: int | None = None,
                          perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Responde «¿podemos gastar X?» o «¿cuánto podemos gastar?»."""
    perfil = perfil or perfil_financiero(mes)
    capacidad = capacidad_discrecional(mes, perfil, margen_porcentaje)
    maximo = capacidad["maximo_discrecional"]
    proximos = calendario_proximos_pagos(mes, perfil)
    if monto is None:
        veredicto = "informativo"
        mensaje = (f"Con lo registrado hoy podrían destinar hasta {_cop(maximo)} a gasto discrecional "
                   f"sin tocar los compromisos del mes.")
        if not maximo:
            mensaje = ("Hoy no queda margen discrecional: la liquidez registrada ya está comprometida con "
                       "gastos fijos, mínimos de tarjeta y el margen de seguridad.")
        return {**capacidad, "monto": None, "veredicto": veredicto, "mensaje": mensaje,
                "proximos_pagos": proximos, "que_cambiaria": _que_cambiaria(perfil, capacidad, maximo)}
    monto = int(monto)
    if monto <= 0:
        raise ValueError("El monto debe ser mayor que cero.")
    restante = maximo - monto
    # Dos restricciones distintas: el acumulado disponible (stock) y lo que el
    # propio mes genera (flujo). Gastar por encima del flujo no es imposible,
    # pero significa comerse ahorro acumulado, y eso hay que decirlo.
    flujo_mes = max(int(perfil["flujo"]["ahorro"]), 0)
    excede_flujo = bool(flujo_mes) and monto > flujo_mes
    if monto > maximo:
        veredicto = "no_asequible"
    elif excede_flujo or monto > maximo * float(PARAMS["borde_asequible_pct"]):
        veredicto = "al_limite"
    else:
        veredicto = "asequible"
    if veredicto == "asequible":
        mensaje = (f"Sí. Después de {concepto} por {_cop(monto)} les quedarían {_cop(restante)} de margen "
                   "discrecional y los compromisos del mes siguen cubiertos.")
    elif veredicto == "al_limite" and excede_flujo:
        mensaje = (f"Alcanza, pero no sale del mes: {_cop(monto)} supera lo que este mes genera "
                   f"({_cop(flujo_mes)}), así que {_cop(monto - flujo_mes)} saldrían del acumulado. "
                   f"Después quedarían {_cop(max(restante, 0))} de margen.")
    elif veredicto == "al_limite":
        mensaje = (f"Sí, pero quedan al filo: {_cop(monto)} consume casi todo el margen "
                   f"({_cop(maximo)}) y solo sobrarían {_cop(max(restante, 0))}.")
    else:
        mensaje = (f"Hoy no. {_cop(monto)} supera en {_cop(monto - maximo)} el margen disponible "
                   f"({_cop(maximo)}) una vez cubiertos gastos fijos, mínimos de tarjeta y el colchón de seguridad.")
    return {**capacidad, "monto": monto, "veredicto": veredicto, "restante_despues": restante,
            "faltante": max(monto - maximo, 0), "excede_flujo_del_mes": excede_flujo,
            "flujo_del_mes": flujo_mes, "mensaje": mensaje, "proximos_pagos": proximos,
            "que_cambiaria": _que_cambiaria(perfil, capacidad, maximo, monto)}


def _que_cambiaria(perfil: dict[str, Any], capacidad: dict[str, Any], maximo: int,
                   monto: int | None = None) -> list[str]:
    """Qué tendría que pasar para que la respuesta cambiara."""
    opciones: list[str] = []
    faltante = max((monto or 0) - maximo, 0)
    if faltante:
        discrecional = perfil["gastos"]["discrecionales"]
        if discrecional:
            opciones.append(f"Recortar {_cop(min(faltante, discrecional))} de gasto discrecional ya registrado este mes.")
        referencia = perfil["ingresos"]["referencia"].get("referencia_conservadora")
        if referencia:
            opciones.append(f"Esperar al próximo ingreso (referencia conservadora: {_cop(referencia)}).")
        minimos = capacidad["compromisos"]["pagos_minimos_tarjetas"]
        if minimos:
            opciones.append(f"Aplazar la compra hasta después de cubrir los mínimos de tarjeta ({_cop(minimos)}).")
    else:
        if perfil["tarjetas"]["deuda_total"]:
            opciones.append("Ese margen también podría ir a un abono extra de tarjeta en lugar de gasto.")
        if perfil["ahorro"]["emergencia"]["shortfall"]:
            opciones.append(f"El fondo de emergencia todavía necesita {_cop(perfil['ahorro']['emergencia']['shortfall'])}.")
    return opciones


def calendario_proximos_pagos(mes: str, perfil: dict[str, Any] | None = None,
                              dias: int = 30) -> list[dict[str, Any]]:
    """Compromisos con fecha conocida dentro del horizonte indicado."""
    perfil = perfil or perfil_financiero(mes)
    hoy = dt.date.today()
    limite = hoy + dt.timedelta(days=dias)
    eventos: list[dict[str, Any]] = []
    for tarjeta in perfil["tarjetas"]["tarjetas"]:
        if tarjeta.get("fecha_pago") and tarjeta["saldo_deuda"]:
            try:
                fecha = dt.date.fromisoformat(tarjeta["fecha_pago"])
            except (TypeError, ValueError):
                continue
            if hoy <= fecha <= limite:
                eventos.append({"fecha": fecha.isoformat(), "tipo": "Pago de tarjeta",
                                "titulo": tarjeta["nombre"], "monto": tarjeta["pago_minimo_efectivo"]})
    for fijo in db.get_gastos_fijos(solo_activos=True):
        if fijo.get("dia_pago"):
            dia = min(int(fijo["dia_pago"]), 28)
            candidatos = [dt.date(hoy.year, hoy.month, dia)]
            siguiente = mes_siguiente(f"{hoy.year:04d}-{hoy.month:02d}")
            ano, numero = (int(parte) for parte in siguiente.split("-"))
            candidatos.append(dt.date(ano, numero, dia))
            for fecha in candidatos:
                if hoy <= fecha <= limite:
                    eventos.append({"fecha": fecha.isoformat(), "tipo": "Gasto fijo",
                                    "titulo": fijo["nombre"], "monto": fijo["valor"]})
                    break
    for meta in perfil["ahorro"]["proximas_metas"]:
        if meta["fecha_objetivo"]:
            try:
                fecha = dt.date.fromisoformat(meta["fecha_objetivo"])
            except (TypeError, ValueError):
                continue
            if hoy <= fecha <= limite:
                eventos.append({"fecha": fecha.isoformat(), "tipo": "Meta",
                                "titulo": meta["nombre"], "monto": meta["faltante"]})
    return sorted(eventos, key=lambda item: item["fecha"])


# ---------------------------------------------------------------------------
# Recomendación de método de pago
# ---------------------------------------------------------------------------

def opciones_de_pago(mes: str, monto: int, *, concepto: str = "esta compra",
                     perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compara pagar en efectivo/débito, con cada tarjeta viable, o esperar."""
    if not isinstance(monto, int) or monto <= 0:
        raise ValueError("El monto debe ser mayor que cero.")
    perfil = perfil or perfil_financiero(mes)
    capacidad = capacidad_discrecional(mes, perfil)
    maximo = capacidad["maximo_discrecional"]
    opciones: list[dict[str, Any]] = []

    cubre_caja = monto <= maximo
    opciones.append({
        "opcion": "Efectivo o débito",
        "tipo": "caja",
        "viable": cubre_caja,
        "impacto": (f"Sale hoy de la caja: el margen discrecional pasaría de {_cop(maximo)} a "
                    f"{_cop(max(maximo - monto, 0))}."),
        "costo_financiero": 0,
        "riesgo": ("Ninguno adicional: no crea deuda." if cubre_caja else
                   f"Dejaría el margen en déficit de {_cop(monto - maximo)} y presionaría compromisos ya registrados."),
        "puntaje": 100 if cubre_caja else 20,
    })

    for tarjeta in perfil["tarjetas"]["tarjetas"]:
        if not tarjeta.get("activa", True):
            continue
        disponible = tarjeta["cupo_disponible"]
        viable = disponible >= monto
        uso_nuevo = _dividir(tarjeta["saldo_deuda"] + monto, tarjeta["cupo_total"]) or 1.0
        interes = round(monto * tarjeta["interes_mensual"] / 100)
        penalizacion = 0
        if uso_nuevo >= float(PARAMS["utilizacion_critica"]):
            penalizacion = 45
        elif uso_nuevo >= float(PARAMS["utilizacion_alta"]):
            penalizacion = 30
        elif uso_nuevo >= float(PARAMS["utilizacion_atencion"]):
            penalizacion = 12
        opciones.append({
            "opcion": f"Tarjeta {tarjeta['nombre']}",
            "tipo": "tarjeta",
            "tarjeta_id": tarjeta["id"],
            "titular": tarjeta["titular_nombre"],
            "viable": viable,
            "impacto": (f"No toca la caja hoy, pero la deuda subiría de {_cop(tarjeta['saldo_deuda'])} a "
                        f"{_cop(tarjeta['saldo_deuda'] + monto)} y la utilización a {_pct(uso_nuevo, 0)}."),
            "costo_financiero": interes if tarjeta["interes_mensual"] else None,
            "riesgo": ("Cupo insuficiente." if not viable else
                       f"Si no lo pagan antes del corte, cuesta cerca de {_cop(interes)} al mes en intereses."
                       if tarjeta["interes_mensual"] else
                       "No hay tasa registrada para esta tarjeta, así que no puedo cuantificar el costo de arrastrarla."),
            "utilizacion_despues": uso_nuevo,
            "puntaje": (0 if not viable else 85 - penalizacion - (10 if tarjeta["interes_mensual"] > 2 else 0)),
        })

    opciones.append({
        "opcion": "Esperar",
        "tipo": "esperar",
        "viable": True,
        "impacto": "Mantiene el margen intacto y conserva cupo para un imprevisto.",
        "costo_financiero": 0,
        "riesgo": "El costo es la espera, no el dinero.",
        "puntaje": 60 if cubre_caja else 90,
    })

    viables = [item for item in opciones if item["viable"]]
    mejor = max(viables, key=lambda item: item["puntaje"]) if viables else opciones[0]
    return {
        "mes": mes, "monto": monto, "concepto": concepto,
        "opciones": sorted(opciones, key=lambda item: -item["puntaje"]),
        "recomendada": mejor["opcion"],
        "razon": _razon_metodo(mejor, monto, maximo, perfil),
        "margen_discrecional": maximo,
        "confianza": perfil["calidad_datos"]["level"],
        "nota": "La comparación usa cupo, tasa y liquidez registrados. No conozco promociones, diferidos ni beneficios del banco.",
    }


def _razon_metodo(mejor: dict[str, Any], monto: int, maximo: int, perfil: dict[str, Any]) -> str:
    if mejor["tipo"] == "caja":
        return (f"Hay margen registrado ({_cop(maximo)}) para cubrir {_cop(monto)} sin crear deuda nueva, "
                "y así no dependen de pagar a tiempo para evitar intereses.")
    if mejor["tipo"] == "tarjeta":
        return (f"La caja no alcanza sin comprometer obligaciones, y esta es la tarjeta con menor costo y menor "
                f"impacto en utilización de las registradas. Solo tiene sentido si separan el monto para pagarla "
                "antes del corte.")
    return ("Ninguna fuente de pago queda cómoda hoy: la caja no alcanza y las tarjetas subirían la utilización "
            "a un nivel que después cuesta caro desarmar.")


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------

ESCENARIOS: dict[str, str] = {
    "gasto": "Un gasto adicional pagado con caja",
    "gasto_tarjeta": "Una compra cargada a tarjeta",
    "ingreso_extra": "Un ingreso adicional confirmado",
    "perdida_ingresos": "Un ingreso menor al habitual",
    "pago_tarjeta": "Un abono extra a tarjeta",
    "ahorro_adicional": "Un aporte extra a las cajitas",
    "retiro_cajita": "Un retiro de las cajitas",
    "reduccion_gastos": "Un recorte sostenido de gasto",
    "aumento_gastos": "Un aumento sostenido de gasto",
    "nueva_deuda": "Tomar una deuda nueva",
    "viaje": "El costo total de un viaje pagado con caja",
    "compra_grande": "Una compra grande pagada con caja",
    "reduccion_gasto_fijo": "Bajar o cancelar un gasto fijo",
    "acelerar_meta": "Aportar de más a una meta para adelantarla",
    "aplazar_meta": "Dejar de aportar a una meta este mes",
    "no_pagar_tarjeta": "No pagar la cuota de una tarjeta",
}


def _progreso_metas_promedio(metas: Sequence[dict[str, Any]], aporte_extra: int = 0) -> float | None:
    """Avance promedio de las metas activas, opcionalmente con un aporte extra.

    El aporte se reparte entre las metas no cumplidas en proporción a lo que
    les falta: es la distribución más neutral posible sin inventar prioridades
    que la pareja no declaró.
    """
    activas = [meta for meta in metas if not meta["cumplida"] and meta["monto_objetivo"]]
    if not activas:
        return None
    faltante_total = sum(meta["faltante"] for meta in activas) or 1
    progresos = []
    for meta in activas:
        parte = aporte_extra * (meta["faltante"] / faltante_total) if aporte_extra else 0
        actual = max(meta["actual"] + parte, 0)
        progresos.append(min(actual / meta["monto_objetivo"], 1.0))
    return round(mean(progresos), 4)


def simular_escenario_detallado(mes: str, tipo: str, monto: int, *, tarjeta_id: int | None = None,
                                perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Antes/después en seis métricas, con explicación. No escribe nada."""
    if tipo not in ESCENARIOS:
        raise ValueError(f"Escenario no válido. Opciones: {', '.join(sorted(ESCENARIOS))}.")
    if not isinstance(monto, int) or monto <= 0:
        raise ValueError("El monto debe ser mayor que cero.")
    perfil = perfil or perfil_financiero(mes)
    flujo, tarjetas, ahorro, deuda = perfil["flujo"], perfil["tarjetas"], perfil["ahorro"], perfil["deuda"]
    antes = {
        "liquidez": flujo["disponible"],
        "deuda_tarjetas": tarjetas["deuda_total"],
        "deuda_total": deuda["total"],
        "ahorro": ahorro["total"],
        "utilizacion": tarjetas["utilizacion_global"],
        "flujo_mensual": int(flujo["ahorro"]),
        "progreso_metas": _progreso_metas_promedio(ahorro["metas"]),
    }
    delta_liquidez = delta_deuda = delta_ahorro = delta_flujo = 0
    tarjeta = None
    if tipo in ("gasto", "compra_grande", "viaje"):
        delta_liquidez, delta_flujo = -monto, -monto
    elif tipo == "gasto_tarjeta":
        tarjeta = next((item for item in tarjetas["tarjetas"] if item["id"] == tarjeta_id), None)
        if tarjeta is None:
            raise ValueError("Selecciona una tarjeta válida para este escenario.")
        delta_deuda = monto
    elif tipo == "ingreso_extra":
        delta_liquidez, delta_flujo = monto, monto
    elif tipo == "perdida_ingresos":
        delta_liquidez, delta_flujo = -monto, -monto
    elif tipo == "pago_tarjeta":
        delta_liquidez, delta_deuda, delta_flujo = -monto, -monto, -monto
    elif tipo == "ahorro_adicional":
        delta_liquidez, delta_ahorro, delta_flujo = -monto, monto, -monto
    elif tipo == "retiro_cajita":
        delta_liquidez, delta_ahorro = monto, -monto
    elif tipo == "reduccion_gastos":
        delta_liquidez, delta_flujo = monto, monto
    elif tipo == "aumento_gastos":
        delta_liquidez, delta_flujo = -monto, -monto
    elif tipo == "nueva_deuda":
        delta_liquidez, delta_deuda = monto, monto
    elif tipo == "reduccion_gasto_fijo":
        delta_liquidez, delta_flujo = monto, monto
    elif tipo == "acelerar_meta":
        delta_liquidez, delta_ahorro, delta_flujo = -monto, monto, -monto
    elif tipo == "aplazar_meta":
        delta_liquidez, delta_flujo = monto, monto
    elif tipo == "no_pagar_tarjeta":
        # No pagar libera caja hoy, pero el saldo sigue generando intereses.
        tasa_media = _dividir(deuda["interes_mensual_estimado"], max(tarjetas["deuda_total"], 1)) or 0.0
        delta_liquidez = monto
        delta_deuda = round(monto + monto * tasa_media)

    deuda_tarjetas_despues = max(antes["deuda_tarjetas"] + (delta_deuda if tipo != "nueva_deuda" else 0), 0)
    despues = {
        "liquidez": antes["liquidez"] + delta_liquidez,
        "deuda_tarjetas": deuda_tarjetas_despues,
        "deuda_total": max(antes["deuda_total"] + delta_deuda, 0),
        "ahorro": max(antes["ahorro"] + delta_ahorro, 0),
        "utilizacion": _dividir(deuda_tarjetas_despues, tarjetas["cupo_total"]) or 0.0,
        "flujo_mensual": antes["flujo_mensual"] + delta_flujo,
        "progreso_metas": _progreso_metas_promedio(ahorro["metas"], delta_ahorro),
    }
    capacidad = capacidad_discrecional(mes, perfil)
    margen_despues = capacidad["maximo_discrecional"] + delta_liquidez
    metas_afectadas = []
    if tipo in ("retiro_cajita", "ahorro_adicional"):
        metas_afectadas = [meta["nombre"] for meta in ahorro["metas"] if not meta["cumplida"]]
    # Se separan los riesgos (que sí cambian el veredicto) de las notas
    # informativas: adelantar una meta no puede quedar marcado como "riesgoso"
    # solo porque el asesor añadió una observación útil.
    riesgos: list[str] = []
    notas: list[str] = []
    if despues["liquidez"] < 0:
        riesgos.append("La liquidez registrada quedaría en negativo: el escenario no se sostiene con la caja actual.")
    if margen_despues < 0:
        riesgos.append(f"El margen discrecional quedaría corto en {_cop(abs(margen_despues))}.")
    if despues["flujo_mensual"] < 0:
        riesgos.append("El mes cerraría en déficit de flujo.")
    if despues["utilizacion"] >= float(PARAMS["utilizacion_alta"]) > antes["utilizacion"]:
        riesgos.append(f"La utilización global subiría a {_pct(despues['utilizacion'], 0)}.")
    if tipo == "pago_tarjeta" and tarjetas["deuda_total"]:
        interes_evitado = round(monto * (deuda["interes_mensual_estimado"] / max(tarjetas["deuda_total"], 1)))
        notas.append(f"Evitaría alrededor de {_cop(interes_evitado)} de interés mensual estimado.")
    if tipo == "no_pagar_tarjeta":
        riesgos.append("No conozco las condiciones de mora de cada banco, así que el costo real de no "
                       "pagar puede ser mayor que el interés corriente estimado aquí.")
    if tipo in ("reduccion_gasto_fijo", "aplazar_meta"):
        notas.append(f"Sostenido durante un año, equivale a {_cop(monto * 12)}.")
    if tipo == "aplazar_meta":
        notas.append("Aplazar el aporte no cancela la meta, pero corre su fecha de llegada.")
    if tipo == "acelerar_meta" and antes["progreso_metas"] is not None:
        notas.append(f"El avance promedio de las metas pasaría de {_pct(antes['progreso_metas'])} a "
                     f"{_pct(despues['progreso_metas'])}.")
    if tipo == "perdida_ingresos":
        cobertura = _dividir(despues["liquidez"], max(perfil["gastos"]["obligatorios"], 1)) or 0
        notas.append(f"La caja cubriría cerca de {cobertura:.1f} meses de gasto obligatorio.")
    consecuencias = riesgos + notas
    veredicto = "sostenible" if not riesgos else "riesgoso" if despues["liquidez"] >= 0 else "no_sostenible"
    return {
        "mes": mes, "escenario": tipo, "descripcion": ESCENARIOS[tipo], "monto": monto,
        "modifica_base": False, "antes": antes, "despues": despues,
        "cambios": {"liquidez": delta_liquidez, "deuda": delta_deuda, "ahorro": delta_ahorro,
                    "flujo": delta_flujo},
        "margen_discrecional_antes": capacidad["maximo_discrecional"],
        "margen_discrecional_despues": margen_despues,
        "metas_afectadas": metas_afectadas,
        "tarjeta": tarjeta["nombre"] if tarjeta else None,
        "consecuencias": consecuencias, "riesgos": riesgos, "notas": notas, "veredicto": veredicto,
        "explicacion": _explicar_escenario(tipo, monto, antes, despues, veredicto, consecuencias),
        "confianza": perfil["calidad_datos"]["level"],
        "nota": "Escenario calculado sobre los registros actuales; no se guardó ningún movimiento.",
    }


def _explicar_escenario(tipo: str, monto: int, antes: dict[str, Any], despues: dict[str, Any],
                        veredicto: str, consecuencias: Sequence[str]) -> str:
    encabezado = {
        "sostenible": "Se puede sostener con los números actuales.",
        "riesgoso": "Es posible, pero aprieta.",
        "no_sostenible": "Con los datos de hoy no se sostiene.",
    }[veredicto]
    detalle = (f"La liquidez pasaría de {_cop(antes['liquidez'])} a {_cop(despues['liquidez'])}, "
               f"la deuda de tarjetas de {_cop(antes['deuda_tarjetas'])} a {_cop(despues['deuda_tarjetas'])} "
               f"y el flujo del mes de {_cop(antes['flujo_mensual'])} a {_cop(despues['flujo_mensual'])}.")
    cierre = " ".join(consecuencias[:2])
    return f"{encabezado} {detalle} {cierre}".strip()


# ---------------------------------------------------------------------------
# Viajes: marco de comparación de destinos
# ---------------------------------------------------------------------------

CAMPOS_VIAJE: tuple[tuple[str, str], ...] = (
    ("transporte_ida_vuelta", "Tiquetes o transporte principal ida y vuelta, por persona"),
    ("alojamiento_noche", "Alojamiento por noche (total del grupo)"),
    ("comida_dia", "Comida por día y por persona"),
    ("transporte_local_dia", "Transporte local por día (total del grupo)"),
    ("actividades", "Actividades y entradas para todo el viaje (total del grupo)"),
    ("seguro_visa_otros", "Seguro, visa, trámites y otros costos fijos del viaje"),
    ("noches", "Noches de estadía"),
    ("viajeros", "Cuántas personas viajan"),
)


def plantilla_viaje() -> dict[str, Any]:
    """Qué datos hacen falta para comparar destinos. No inventa precios."""
    return {
        "campos": [{"campo": campo, "descripcion": descripcion} for campo, descripcion in CAMPOS_VIAJE],
        "nota": "Los precios de tiquetes y alojamiento cambian todo el tiempo y dependen de fechas concretas. "
                "No los estimo de memoria: pásamelos (o consúltalos) y yo hago el análisis financiero.",
    }


def costear_destino(destino: dict[str, Any]) -> dict[str, Any]:
    """Costo total de un destino a partir de datos entregados por el usuario."""
    nombre = str(destino.get("nombre") or "destino").strip()
    faltantes = [campo for campo, _ in CAMPOS_VIAJE
                 if destino.get(campo) in (None, "") and campo not in ("seguro_visa_otros", "actividades")]
    noches = int(destino.get("noches") or 0)
    viajeros = int(destino.get("viajeros") or 2)
    def valor(campo: str) -> int:
        try:
            return int(round(float(destino.get(campo) or 0)))
        except (TypeError, ValueError):
            return 0
    transporte = valor("transporte_ida_vuelta") * viajeros
    alojamiento = valor("alojamiento_noche") * noches
    comida = valor("comida_dia") * noches * viajeros
    transporte_local = valor("transporte_local_dia") * noches
    actividades = valor("actividades")
    otros = valor("seguro_visa_otros")
    total = transporte + alojamiento + comida + transporte_local + actividades + otros
    return {
        "nombre": nombre, "noches": noches, "viajeros": viajeros,
        "desglose": {"transporte": transporte, "alojamiento": alojamiento, "comida": comida,
                     "transporte_local": transporte_local, "actividades": actividades, "otros": otros},
        "total": total, "costo_por_dia": round(total / noches) if noches else None,
        "costo_por_persona": round(total / viajeros) if viajeros else None,
        "datos_faltantes": faltantes,
        "completo": not faltantes and total > 0,
    }


def comparar_destinos(mes: str, destinos: Sequence[dict[str, Any]], *,
                      meses_para_viajar: int | None = None,
                      perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compara destinos con la restricción financiera real, sin elegir por gusto."""
    perfil = perfil or perfil_financiero(mes)
    capacidad = capacidad_discrecional(mes, perfil)
    ahorro_disponible = perfil["ahorro"]["total"]
    emergencia = perfil["ahorro"]["emergencia"]
    ahorro_libre = max(ahorro_disponible - emergencia["current"], 0)
    flujo_mensual = max(int(perfil["flujo"]["ahorro"]), 0)
    contexto_base = {
        "ahorro_total": ahorro_disponible, "fondo_emergencia": emergencia["current"],
        "ahorro_libre_para_viaje": ahorro_libre, "flujo_mensual": flujo_mensual,
        "margen_discrecional": capacidad["maximo_discrecional"],
        "deuda_tarjetas": perfil["tarjetas"]["deuda_total"],
    }
    if not destinos:
        return {"mes": mes, "destinos": [], "listo": False, "plantilla": plantilla_viaje(),
                "contexto_financiero": contexto_base, "comparacion": None, "datos_faltantes": [],
                "mensaje": "Puedo comparar los destinos que quieran, pero necesito los costos estimados de cada uno. "
                           "Los precios de tiquetes y alojamiento son información externa y cambian por fechas; "
                           "no los voy a inventar."}
    horizonte = int(meses_para_viajar) if meses_para_viajar else None
    resultados: list[dict[str, Any]] = []
    for destino in destinos:
        costeo = costear_destino(destino)
        total = costeo["total"]
        falta = max(total - ahorro_libre, 0)
        meses_necesarios = ceil(falta / flujo_mensual) if falta and flujo_mensual else (0 if not falta else None)
        requerido_mensual = ceil(falta / horizonte) if (falta and horizonte) else (0 if not falta else None)
        if falta == 0:
            asequible = "si"
        elif horizonte and requerido_mensual is not None and requerido_mensual <= flujo_mensual:
            asequible = "si_con_ahorro"
        elif meses_necesarios is not None:
            asequible = "requiere_tiempo"
        else:
            asequible = "no_calculable"
        impacto_metas = []
        for meta in perfil["ahorro"]["metas"]:
            if meta["cumplida"]:
                continue
            necesario = int(meta["ahorro_necesario_mensual"] or 0)
            if necesario and requerido_mensual and necesario + requerido_mensual > flujo_mensual:
                impacto_metas.append({
                    "meta": meta["nombre"], "necesario_mensual": necesario,
                    "detalle": f"Con {_cop(requerido_mensual)} al mes hacia el viaje, «{meta['nombre']}» "
                               f"se quedaría sin sus {_cop(necesario)} mensuales."})
        resultados.append({
            **costeo,
            "faltante_sobre_ahorro_libre": falta,
            "meses_para_ahorrarlo": meses_necesarios,
            "ahorro_mensual_requerido": requerido_mensual,
            "asequible": asequible,
            "porcentaje_de_ahorro_libre": _dividir(total, ahorro_libre),
            "impacto_en_metas": impacto_metas,
            "impacto_deuda": ("Con deuda de tarjeta activa, cualquier viaje compite con el abono que la reduce."
                              if perfil["tarjetas"]["deuda_total"] else "No hay deuda de tarjeta compitiendo por ese dinero."),
        })
    completos = [item for item in resultados if item["completo"]]
    comparacion = None
    if len(completos) >= 2:
        barato = min(completos, key=lambda item: item["total"])
        caro = max(completos, key=lambda item: item["total"])
        diferencia = caro["total"] - barato["total"]
        comparacion = {
            "mas_economico": barato["nombre"], "mas_costoso": caro["nombre"],
            "diferencia": diferencia,
            "diferencia_en_meses_de_ahorro": ceil(diferencia / flujo_mensual) if flujo_mensual else None,
            "lectura": (f"{caro['nombre']} cuesta {_cop(diferencia)} más que {barato['nombre']}. "
                        + (f"Con el flujo actual ({_cop(flujo_mensual)} al mes) esa diferencia son "
                           f"{_meses(ceil(diferencia / flujo_mensual))} extra de ahorro."
                           if flujo_mensual else
                           "Sin flujo mensual positivo registrado no puedo traducir esa diferencia en tiempo.")),
        }
    return {
        "mes": mes, "destinos": resultados, "comparacion": comparacion,
        "listo": bool(completos),
        "contexto_financiero": contexto_base,
        "datos_faltantes": sorted({campo for item in resultados for campo in item["datos_faltantes"]}),
        "plantilla": plantilla_viaje(),
        "nota": "La comparación es financiera, no turística: mide costo total, tiempo de ahorro e impacto en metas. "
                "No decide cuál destino vale más la pena vivir.",
    }


# ---------------------------------------------------------------------------
# Integridad de datos
# ---------------------------------------------------------------------------

def informe_integridad(mes: str | None = None, *, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reporta problemas de datos sin esconderlos y sin inventar la causa."""
    referencia = validar_mes(mes) if mes else (meses_con_actividad() or [dt.date.today().strftime("%Y-%m")])[-1]
    auditoria = engine.audit_integrity(referencia)
    # El balance puede lanzar si alguna invariante está rota. Esa excepción es
    # justamente un hallazgo de integridad: se reporta, no se propaga.
    error_balance: str | None = None
    try:
        balance = calc.balance_historico_pareja()
    except Exception as fallo:  # noqa: BLE001 - cualquier fallo aquí es un hallazgo
        balance = {"cuadra": False, "descuadre": 0}
        error_balance = str(fallo)
    descuadre = int(balance["descuadre"])
    candidatos: list[dict[str, Any]] = []
    # Distribuciones que no suman: es la causa más verificable de un descuadre,
    # porque rompe directamente la igualdad consumo = valor del movimiento.
    for gasto in db.get_gastos():
        if gasto["monto_p1"] + gasto["monto_p2"] != gasto["valor"]:
            candidatos.append({"tipo": "gasto", "id": gasto["id"], "nombre": gasto["nombre"],
                               "mes": gasto["mes"], "valor": gasto["valor"],
                               "motivo": f"La distribución no suma el valor: "
                                         f"{_cop(gasto['monto_p1'])} + {_cop(gasto['monto_p2'])} != "
                                         f"{_cop(gasto['valor'])}."})
    for compra in db.get_compras_tarjeta():
        if compra["monto_p1"] + compra["monto_p2"] != compra["valor_original"]:
            candidatos.append({"tipo": "compra_tarjeta", "id": compra["id"], "nombre": compra["descripcion"],
                               "mes": compra.get("mes"), "valor": compra["valor_original"],
                               "motivo": "La distribución del movimiento de tarjeta no suma su valor original."})
    for pago in db.get_pagos_deuda():
        if pago["monto_aportado_p1"] + pago["monto_aportado_p2"] != pago["monto"]:
            candidatos.append({"tipo": "pago_tarjeta", "id": pago["id"], "nombre": pago.get("concepto") or "Pago",
                               "mes": pago["mes"], "valor": pago["monto"],
                               "motivo": "Los aportes del pago no suman el monto pagado."})
    if descuadre:
        objetivo = abs(descuadre)
        # Movimientos cuyo monto coincide con el descuadre: son pistas, no culpables.
        for gasto in db.get_gastos():
            if abs(gasto["valor"] - objetivo) <= max(round(objetivo * 0.01), 1):
                candidatos.append({"tipo": "gasto", "id": gasto["id"], "nombre": gasto["nombre"],
                                   "mes": gasto["mes"], "valor": gasto["valor"],
                                   "motivo": "El monto coincide con la diferencia."})
        for compra in db.get_compras_tarjeta():
            if abs(compra["valor_original"] - objetivo) <= max(round(objetivo * 0.01), 1):
                candidatos.append({"tipo": "compra_tarjeta", "id": compra["id"], "nombre": compra["descripcion"],
                                   "mes": compra.get("mes"), "valor": compra["valor_original"],
                                   "motivo": "El monto coincide con la diferencia."})
        for ajuste in db.get_ajustes_tarjeta():
            candidatos.append({"tipo": "ajuste_tarjeta", "id": ajuste["id"], "nombre": ajuste["motivo"],
                               "mes": ajuste["mes"], "valor": ajuste["variacion"],
                               "motivo": "Los ajustes manuales de saldo son el origen más común de diferencias."})
        for tarjeta in calc.resumen_tarjetas():
            if tarjeta.get("saldo_historico_pendiente"):
                candidatos.append({"tipo": "deuda_historica", "id": tarjeta["id"], "nombre": tarjeta["nombre"],
                                   "mes": None, "valor": tarjeta["saldo_historico_pendiente"],
                                   "motivo": "Deuda histórica sin desglose de compras que la respalden."})
    criticos = [item for item in auditoria["issues"] if item["severity"] == "critical"]
    atencion = [item for item in auditoria["issues"] if item["severity"] != "critical"]
    faltantes = engine.data_quality(referencia)["missing"]
    if error_balance:
        mensaje = ("El balance histórico entre ustedes no se puede reconstruir en este momento porque un registro "
                   f"rompe una invariante del libro: {error_balance} Los cálculos del mes siguen disponibles.")
    elif descuadre:
        mensaje = (f"Los cálculos financieros del mes están disponibles y son utilizables, pero el libro histórico "
                   f"tiene una diferencia de {_cop(abs(descuadre))} que hay que revisar. No voy a atribuirle una causa "
                   "sin evidencia: abajo dejo los movimientos que valdría la pena mirar primero.")
    elif criticos:
        mensaje = "Hay hallazgos críticos de integridad que conviene resolver antes de tomar decisiones grandes."
    elif atencion:
        mensaje = "Los números cuadran; quedan detalles menores por ordenar."
    else:
        mensaje = "El libro cuadra y no encontré inconsistencias en los controles automáticos."
    return {
        "mes": referencia, "cuadra": bool(balance["cuadra"]) and not error_balance,
        "error_balance": error_balance, "descuadre": descuadre,
        "descuadre_absoluto": abs(descuadre),
        "hallazgos_criticos": criticos, "hallazgos_atencion": atencion,
        "total_hallazgos": len(auditoria["issues"]),
        "candidatos_descuadre": candidatos[:15],
        "datos_faltantes": faltantes,
        "controles": auditoria["checked"],
        "mensaje": mensaje,
        "advertencia": "Un descuadre no invalida los cálculos del mes: afecta la reconstrucción histórica del "
                       "balance entre ustedes dos.",
    }


# ---------------------------------------------------------------------------
# Hallazgos explicables (qué → por qué → acción)
# ---------------------------------------------------------------------------

def _hallazgo(titulo: str, prioridad: str, categoria: str, que: str, por_que: str, accion: str,
              impacto: str, evidencia: dict[str, Any], *, confianza: str = "Media",
              monto: int = 0) -> dict[str, Any]:
    return {"titulo": titulo, "prioridad": prioridad, "categoria": categoria,
            "que": que, "por_que": por_que, "accion": accion, "impacto": impacto,
            "evidencia": evidencia, "confianza": confianza, "monto_involucrado": monto}


def _h(identificador: str, categoria: str, titulo: str, que: str, por_que: str, accion: str,
       impacto: str, evidencia: dict[str, Any], *, monto: int = 0, confianza: str = "Media",
       ingreso: int = 0, urgencia: float = 0.5, reversibilidad: float = 0.5,
       control: float = 0.7, entidades: list[dict[str, Any]] | None = None,
       montos: dict[str, int] | None = None, severidad: str | None = None,
       razon_severidad: str = "", **condiciones: Any) -> af.Hallazgo:
    """Construye un hallazgo derivando la severidad de condiciones observables."""
    if severidad is None:
        severidad, razon = af._severidad_por_reglas(
            impacto_relativo=af._impacto_relativo(monto, ingreso), **condiciones)
        razon_severidad = razon_severidad or razon
    return af.Hallazgo(
        id=identificador, categoria=categoria, severidad=severidad, titulo=titulo, que=que,
        por_que=por_que, accion=accion, impacto=impacto, evidencia=evidencia, confianza=confianza,
        razon_severidad=razon_severidad, entidades_relacionadas=entidades or [],
        montos_relacionados=montos or {}, monto_involucrado=abs(int(monto)),
        urgencia=urgencia, reversibilidad=reversibilidad, control_usuario=control)


def construir_hallazgos(perfil: dict[str, Any], *, detectados: dict[str, Any], cotejo: dict[str, Any],
                        tendencia: dict[str, Any], tarjetas_iq: dict[str, Any],
                        ahorro_iq: dict[str, Any]) -> list[af.Hallazgo]:
    """Diagnóstico completo como objetos ``Hallazgo``, ya priorizados."""
    ingresos, gastos = perfil["ingresos"], perfil["gastos"]
    tarjetas, deuda = perfil["tarjetas"], perfil["deuda"]
    ahorro, flujo, pareja = perfil["ahorro"], perfil["flujo"], perfil["pareja"]
    ingreso = ingresos["total"]
    hallazgos: list[af.Hallazgo] = []

    # --- Flujo ---
    if int(flujo["ahorro"]) < 0:
        deficit = -int(flujo["ahorro"])
        hallazgos.append(_h(
            "flujo_deficit", "flujo", "El mes cierra en déficit",
            f"Las salidas ({_cop(flujo['salidas'])}) superan los ingresos ({_cop(flujo['ingresos'])}) "
            f"en {_cop(deficit)}.",
            f"El gasto registrado y los pagos de tarjeta consumieron más caja de la que entró. "
            f"El gasto discrecional del mes suma {_cop(gastos['discrecionales'])}.",
            "Cubran obligaciones y mínimos primero y congelen gasto discrecional hasta cerrar la brecha.",
            f"Cerrar el déficit libera {_cop(deficit)} de presión sobre caja o crédito.",
            {"ingresos": flujo["ingresos"], "salidas": flujo["salidas"], "deficit": deficit,
             "discrecional": gastos["discrecionales"]},
            monto=deficit, ingreso=ingreso, confianza="Alta", urgencia=1.0, reversibilidad=0.2,
            montos={"deficit": deficit, "discrecional_disponible": gastos["discrecionales"]},
            compromete_obligaciones=True))
    elif flujo["disponible"] < flujo["comprometido"]:
        faltante = flujo["comprometido"] - flujo["disponible"]
        hallazgos.append(_h(
            "liquidez_comprometida", "flujo", "La liquidez no cubre todo lo comprometido",
            f"Los compromisos del mes suman {_cop(flujo['comprometido'])} y el disponible es "
            f"{_cop(flujo['disponible'])}.",
            "Gastos fijos pendientes, mínimos de tarjeta y aportes obligatorios a metas ocupan más caja "
            "de la que hay registrada.",
            f"Prioricen cuál de esos compromisos se cubre primero; faltan {_cop(faltante)}.",
            "Evita que un pago obligatorio termine financiado con tarjeta.",
            {"comprometido": flujo["comprometido"], "disponible": flujo["disponible"]},
            monto=faltante, ingreso=ingreso, confianza="Alta", urgencia=0.9, reversibilidad=0.3,
            compromete_obligaciones=True))

    # --- Tarjetas (se agrupan después en un solo bloque de decisión) ---
    for tarjeta in tarjetas_iq["tarjetas"]:
        señales = {item["señal"] for item in tarjeta["señales"]}
        if {"utilizacion_alta", "utilizacion_critica"} & señales:
            actividad = tarjeta["actividad_mes"]
            hallazgos.append(_h(
                f"tarjeta_utilizacion_{tarjeta['id']}", "tarjeta",
                f"Bajar la utilización de {tarjeta['nombre']}",
                f"Está en {_pct(tarjeta['utilizacion'])}: {_cop(tarjeta['saldo'])} de {_cop(tarjeta['cupo'])}.",
                (f"Durante el período hubo compras por {_cop(actividad['compras'])} e intereses y cargos por "
                 f"{_cop(actividad['intereses'] + actividad['cargos'])} frente a pagos por "
                 f"{_cop(actividad['pagos'])}."
                 if actividad["compras"] or actividad["pagos"] else
                 "El saldo viene arrastrado de períodos anteriores; este mes no hay movimientos que lo expliquen."),
                f"Pausen compras nuevas ahí y definan un abono por encima del mínimo ({_cop(tarjeta['pago_minimo'])}) "
                "antes del próximo corte.",
                (f"Cada {_cop(100000)} abonados evitan cerca de "
                 f"{_cop(round(tarjeta['interes_estimado']))} de interés mensual."
                 if tarjeta["interes_mensual"] else
                 "Sin tasa registrada no puedo cuantificar el ahorro en intereses."),
                {"tarjeta_id": tarjeta["id"], "utilizacion": tarjeta["utilizacion"], "saldo": tarjeta["saldo"],
                 "cupo": tarjeta["cupo"], "minimo": tarjeta["pago_minimo"],
                 "señales": [item["detalle"] for item in tarjeta["señales"]]},
                monto=tarjeta["saldo"], ingreso=ingreso, urgencia=0.8, reversibilidad=0.3,
                confianza="Alta" if not tarjeta["datos_faltantes"] else "Media",
                entidades=[{"tipo": "tarjeta", "id": tarjeta["id"], "nombre": tarjeta["nombre"],
                            "titular": tarjeta["titular"]}],
                montos={"saldo": tarjeta["saldo"], "minimo": tarjeta["pago_minimo"]},
                tendencia_negativa="saldo_creciendo" in señales))
        if "saldo_creciendo" in señales:
            hallazgos.append(_h(
                f"tarjeta_creciendo_{tarjeta['id']}", "tarjeta",
                f"La deuda de {tarjeta['nombre']} está creciendo",
                f"El saldo subió {_cop(tarjeta['crecimiento_neto'])} neto este mes.",
                "Las compras, intereses y cargos del período superaron los pagos realizados.",
                "Comparen el abono extra contra las compras discrecionales cargadas a esa tarjeta.",
                f"Frenarlo evita que {_cop(tarjeta['crecimiento_neto'])} se conviertan en deuda arrastrada.",
                {"tarjeta_id": tarjeta["id"], "actividad": tarjeta["actividad_mes"],
                 "crecimiento": tarjeta["crecimiento_neto"]},
                monto=tarjeta["crecimiento_neto"], ingreso=ingreso, confianza="Alta",
                urgencia=0.8, reversibilidad=0.4, tendencia_negativa=True,
                entidades=[{"tipo": "tarjeta", "id": tarjeta["id"], "nombre": tarjeta["nombre"]}]))
        if "dependencia_minimo" in señales:
            hallazgos.append(_h(
                f"tarjeta_minimo_{tarjeta['id']}", "tarjeta",
                f"{tarjeta['nombre']} se está pagando solo con el mínimo",
                f"El pago del mes ({_cop(tarjeta['actividad_mes']['pagos'])}) coincide con el mínimo.",
                "Pagar el mínimo cubre sobre todo intereses, así que el capital casi no baja.",
                "Suban el abono aunque sea parcialmente por encima del mínimo este mes.",
                (f"El interés estimado de esta tarjeta es {_cop(tarjeta['interes_estimado'])} al mes."
                 if tarjeta["interes_estimado"] else "Falta la tasa para cuantificar el costo."),
                {"tarjeta_id": tarjeta["id"], "pago": tarjeta["actividad_mes"]["pagos"],
                 "minimo": tarjeta["pago_minimo"]},
                monto=tarjeta["interes_estimado"] * 12, ingreso=ingreso, confianza="Media",
                urgencia=0.7, reversibilidad=0.5, tendencia_negativa=True,
                entidades=[{"tipo": "tarjeta", "id": tarjeta["id"], "nombre": tarjeta["nombre"]}]))
        if "datos_incompletos" in señales and tarjeta["saldo"]:
            hallazgos.append(_h(
                f"tarjeta_datos_{tarjeta['id']}", "datos",
                f"Faltan datos de {tarjeta['nombre']}",
                f"Tiene {_cop(tarjeta['saldo'])} de saldo y faltan " + ", ".join(tarjeta["datos_faltantes"]) + ".",
                "Sin esos datos no puedo cuantificar el costo real de arrastrar el saldo ni ordenar las deudas.",
                "Tomen los datos del extracto y complétenlos en la tarjeta.",
                "Permite calcular intereses reales y comparar estrategias de pago.",
                {"tarjeta_id": tarjeta["id"], "faltantes": tarjeta["datos_faltantes"]},
                monto=0, ingreso=ingreso, confianza="Alta", urgencia=0.4, reversibilidad=0.9,
                solo_confirmacion=True))

    # --- Gastos fijos: registrados vs realidad ---
    if cotejo["sin_registrar"]:
        primeros = ", ".join(item["nombre"] for item in cotejo["sin_registrar"][:3])
        total = cotejo["impacto_no_registrado"]
        hallazgos.append(_h(
            "fijos_sin_registrar", "gastos_fijos",
            "Hay cobros recurrentes que no están registrados como gasto fijo",
            f"Detecté {len(cotejo['sin_registrar'])} conceptos ({primeros}) por unos {_cop(total)} al mes.",
            "Se repiten en varios meses con montos parecidos, pero no existen en la lista de gastos fijos.",
            "Revísenlos y confirmen cuáles registrar; así el margen disponible deja de estar sobreestimado.",
            f"Registrarlos ajusta el disponible mensual en {_cop(total)}.",
            {"candidatos": cotejo["sin_registrar"][:6]},
            monto=total, ingreso=ingreso, confianza="Media", urgencia=0.4, reversibilidad=0.9, control=0.9))
    for item in cotejo["monto_desactualizado"][:3]:
        diferencia = item["diferencia"]
        hallazgos.append(_h(
            f"fijo_monto_{item['gasto_fijo_id']}", "gastos_fijos",
            f"«{item['nombre']}» ya no cuesta lo registrado",
            item["detalle"],
            ("El valor registrado quedó viejo frente a los cobros reales; suele pasar tras un ajuste de tarifa."
             if diferencia > 0 else
             "El valor registrado está por encima de lo que se está cobrando, así que el margen se ve más bajo de lo real."),
            f"Actualicen el gasto fijo a {_cop(item['promedio_real'])}.",
            f"Corrige el compromiso mensual en {_cop(abs(diferencia))}.",
            item, monto=abs(diferencia) * 12, ingreso=ingreso, confianza="Alta",
            urgencia=0.35, reversibilidad=0.95, control=1.0, solo_confirmacion=abs(diferencia) * 12 < ingreso * 0.05))
    for item in cotejo["sin_cobros_recientes"][:3]:
        hallazgos.append(_h(
            f"fijo_inactivo_{item['gasto_fijo_id']}", "gastos_fijos",
            f"«{item['nombre']}» sigue activo pero no se ve en los cobros",
            item["detalle"],
            "O se canceló, o se está registrando con otro nombre. No puedo distinguirlo desde los datos.",
            "Confirmen si sigue vigente; si no, desactívenlo para que deje de ocupar margen.",
            f"Libera {_cop(item['valor_registrado'])} de compromiso mensual si ya no existe.",
            item, monto=item["valor_registrado"], ingreso=ingreso, confianza="Media",
            urgencia=0.4, reversibilidad=0.95, control=1.0))
    for item in cotejo["responsabilidad_distinta"][:2]:
        hallazgos.append(_h(
            f"fijo_responsabilidad_{item['gasto_fijo_id']}", "gastos_fijos",
            f"La responsabilidad de «{item['nombre']}» no coincide con lo registrado",
            item["detalle"],
            "La responsabilidad económica se declara, no se deduce de quién paga; por eso no la cambio solo.",
            "Confirmen quién asume ese gasto y ajusten el registro si hace falta.",
            "Evita que el balance entre ustedes arrastre un sesgo mes a mes.",
            item, monto=0, ingreso=ingreso, confianza="Media", urgencia=0.3,
            reversibilidad=0.9, solo_confirmacion=True))

    carga = gastos["carga_fija"]
    if carga is not None and carga >= float(PARAMS["carga_fija_atencion"]):
        total_fijo = gastos["gastos_fijos_registrados"]["total_mensual_equivalente"]
        hallazgos.append(_h(
            "carga_fija", "gastos_fijos", "Los gastos fijos pesan sobre el ingreso",
            f"Equivalen a {_cop(total_fijo)} al mes: {_pct(carga)} del ingreso.",
            "Son compromisos que se renuevan solos, así que recortan el margen antes de cualquier decisión.",
            "Revisen cuáles se pueden renegociar, bajar de plan o cancelar antes de asumir otro compromiso.",
            "Cada peso liberado de un gasto fijo se libera todos los meses, no una sola vez.",
            {"total": total_fijo, "porcentaje": carga,
             "cantidad": gastos["gastos_fijos_registrados"]["cantidad_activos"]},
            monto=total_fijo, ingreso=ingreso, confianza="Alta", urgencia=0.5, reversibilidad=0.4,
            severidad=(af.ALTA if carga >= float(PARAMS["carga_fija_alta"]) else af.MEDIA),
            razon_severidad=(f"Los gastos fijos son {_pct(carga)} del ingreso, por encima del umbral de "
                             f"{_pct(PARAMS['carga_fija_alta'])} que se considera alto.")))

    # --- Ahorro y metas ---
    emergencia = ahorro["emergencia"]
    if emergencia["coverage_months"] < float(PARAMS["colchon_minimo_meses"]):
        faltante_mes = max(emergencia["monthly_base"] - emergencia["current"], 0)
        hallazgos.append(_h(
            "emergencia_baja", "ahorro", "El fondo de emergencia cubre menos de un mes",
            f"La reserva es {_cop(emergencia['current'])} frente a un gasto obligatorio mensual de "
            f"{_cop(emergencia['monthly_base'])}.",
            "Los aportes no han alcanzado el ritmo necesario o la reserva se usó para cubrir gastos corrientes.",
            "Definan un aporte fijo pequeño pero automático, en paralelo a los mínimos de tarjeta.",
            f"Llegar a un mes de cobertura requiere {_cop(faltante_mes)}.",
            {"actual": emergencia["current"], "objetivo": emergencia["target"],
             "cobertura_meses": round(emergencia["coverage_months"], 2)},
            monto=emergencia["shortfall"], ingreso=ingreso, confianza="Media",
            urgencia=0.6, reversibilidad=0.6,
            severidad=af.ALTA,
            razon_severidad="Sin colchón, cualquier imprevisto se financia con tarjeta o retirando de una meta."))
    if ahorro_iq["conflicto_de_metas"]:
        brecha = ahorro_iq["aporte_requerido_total"] - ahorro_iq["flujo_disponible"]
        hallazgos.append(_h(
            "metas_en_conflicto", "metas", "Las metas piden más de lo que deja el flujo",
            f"Necesitan {_cop(ahorro_iq['aporte_requerido_total'])} al mes y el flujo deja "
            f"{_cop(ahorro_iq['flujo_disponible'])}.",
            "Varias metas compiten por el mismo excedente y ninguna tiene prioridad declarada.",
            "Elijan una o dos metas prioritarias y muevan la fecha objetivo de las demás.",
            f"Concentrar el aporte reduce la brecha de {_cop(brecha)} sobre la meta principal.",
            {"requerido": ahorro_iq["aporte_requerido_total"], "disponible": ahorro_iq["flujo_disponible"],
             "atrasadas": ahorro_iq["metas_atrasadas"]},
            monto=brecha, ingreso=ingreso, confianza="Media", urgencia=0.4, reversibilidad=0.8, control=1.0))

    # --- Pareja ---
    if pareja["monto_pendiente"]:
        hallazgos.append(_h(
            "saldo_pareja", "pareja",
            f"{pareja['deudor']} tiene un saldo pendiente con {pareja['acreedor']}",
            f"El balance acumulado deja {_cop(pareja['monto_pendiente'])} a favor de {pareja['acreedor']}.",
            "Uno de los dos ha puesto más caja de la que le corresponde según las responsabilidades registradas.",
            "Registren una liquidación por ese monto o acuerden que el próximo gasto grande lo cubra quien debe.",
            "Cierra el saldo y evita que la diferencia siga creciendo mes a mes.",
            {"deudor": pareja["deudor"], "acreedor": pareja["acreedor"],
             "monto": pareja["monto_pendiente"]},
            monto=pareja["monto_pendiente"], ingreso=ingreso,
            confianza="Alta" if pareja["cuadra"] else "Media",
            urgencia=0.35, reversibilidad=0.9, control=1.0))

    # --- Integridad ---
    if not pareja["cuadra"]:
        hallazgos.append(_h(
            "libro_descuadrado", "integridad", "El libro histórico no cuadra",
            f"Hay una diferencia de {_cop(abs(pareja['descuadre']))} entre aportes, consumos y deuda pendiente.",
            "No tengo evidencia suficiente para atribuir la causa; suele venir de ajustes manuales de saldo, "
            "deuda histórica sin desglose o distribuciones que no suman.",
            "Revisen los movimientos candidatos antes de usar el balance de pareja para liquidar.",
            "Los cálculos del mes siguen siendo válidos; lo que queda en duda es la reconstrucción histórica.",
            {"descuadre": pareja["descuadre"]}, monto=abs(int(pareja["descuadre"])), ingreso=ingreso,
            confianza="Alta", urgencia=0.7, reversibilidad=0.5, control=0.8, es_integridad=True))

    # --- Tendencia ---
    if tendencia["gastos"]["direccion"] == "subiendo":
        serie = tendencia["gastos"]["serie"]
        subida = max(serie[-1] - serie[-3], 0)
        culpables = ", ".join(item["categoria"] for item in tendencia["categorias_en_alza"][:3])
        hallazgos.append(_h(
            "gasto_en_alza", "tendencia", "El gasto sube tres meses seguidos",
            f"Las salidas pasaron de {_cop(serie[-3])} a {_cop(serie[-1])} en tres meses.",
            (f"Las categorías que más crecieron son {culpables}." if culpables else
             "El aumento está repartido entre varias categorías, sin un responsable claro."),
            "Miren esas categorías antes de asumir nuevos compromisos fijos.",
            f"Volver al nivel de hace tres meses liberaría {_cop(subida)}.",
            {"serie": serie, "categorias": tendencia["categorias_en_alza"][:3]},
            monto=subida, ingreso=ingreso, confianza="Media", urgencia=0.4,
            reversibilidad=0.7, control=0.9, tendencia_negativa=True))

    # --- Datos faltantes ---
    if not ingresos["confirmado"]:
        hallazgos.append(_h(
            "sin_ingresos", "datos", "No hay ingresos registrados este mes",
            f"El mes {perfil['mes']} no tiene ingresos registrados.",
            "Puede ser un mes en curso o un registro pendiente; sin ingreso no puedo calcular márgenes reales.",
            "Registren los ingresos del mes aunque sean parciales.",
            "Todo el análisis de asequibilidad y carga fija depende de ese dato.",
            {"mes": perfil["mes"]}, monto=0, ingreso=ingreso, confianza="Alta",
            urgencia=0.8, reversibilidad=1.0, control=1.0,
            severidad=af.ALTA,
            razon_severidad="Sin ingreso registrado, varios cálculos del mes quedan sin base."))

    if int(flujo["ahorro"]) > 0 and not any(item.severidad in (af.CRITICA, af.ALTA) for item in hallazgos):
        hallazgos.append(_h(
            "mes_en_orden", "flujo", "El mes va bien",
            f"El mes deja {_cop(flujo['ahorro'])} de excedente con los movimientos registrados.",
            "Los ingresos superaron las salidas y no hay señales de presión en tarjetas ni metas.",
            "Definan a dónde va ese excedente antes de que se diluya en gasto no planeado.",
            f"Asignar {_cop(flujo['ahorro'])} cambia el punto de partida del próximo mes.",
            {"flujo": flujo["ahorro"]}, monto=int(flujo["ahorro"]), ingreso=ingreso, confianza="Alta",
            urgencia=0.3, reversibilidad=1.0, severidad=af.POSITIVA,
            razon_severidad="No es un problema: es una oportunidad de asignación."))

    return af.priorizar(hallazgos, ingreso)


def generar_hallazgos(mes: str, perfil: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Hallazgos priorizados en formato dict (contrato que consume la UI)."""
    perfil = perfil or perfil_financiero(mes)
    detectados = perfil["gastos"]["gastos_fijos_detectados"]
    objetos = construir_hallazgos(
        perfil,
        detectados=detectados,
        cotejo=cotejar_gastos_fijos(mes, detectados=detectar_gastos_fijos(mes, incluir_registrados=True)),
        tendencia=tendencias(mes, 4),
        tarjetas_iq=inteligencia_tarjetas(mes, perfil),
        ahorro_iq=inteligencia_ahorro(mes, perfil))
    return [hallazgo.a_dict() for hallazgo in objetos]


def explicar_finanzas_simple(mes: str, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explicación en lenguaje llano, sin tecnicismos ni motivación vacía."""
    perfil = perfil or perfil_financiero(mes)
    ingresos, gastos = perfil["ingresos"], perfil["gastos"]
    tarjetas, ahorro, flujo = perfil["tarjetas"], perfil["ahorro"], perfil["flujo"]
    partes: list[str] = []
    partes.append(
        f"Este mes entraron {_cop(ingresos['total'])} y salieron {_cop(flujo['salidas'])}. "
        + (f"Eso deja {_cop(flujo['ahorro'])} a favor." if int(flujo["ahorro"]) >= 0
           else f"Eso deja un hueco de {_cop(-int(flujo['ahorro']))}."))
    if gastos["total"]:
        principal = gastos["por_categoria"][0]
        partes.append(
            f"De lo que gastaron, lo más pesado fue {principal['categoria']} con {_cop(principal['monto'])} "
            f"({_pct(principal['participacion'], 0)} del gasto). "
            f"Lo obligatorio fue {_cop(gastos['obligatorios'])} y lo discrecional {_cop(gastos['discrecionales'])}.")
    if tarjetas["deuda_total"]:
        partes.append(
            f"Con las tarjetas deben {_cop(tarjetas['deuda_total'])}, que es {_pct(tarjetas['utilizacion_global'], 0)} "
            f"del cupo. El mínimo del mes suma {_cop(tarjetas['pago_minimo_total'])}"
            + (f" y arrastrar ese saldo cuesta cerca de {_cop(tarjetas['interes_estimado_mensual'])} al mes en intereses."
               if tarjetas["interes_estimado_mensual"] else " (falta registrar tasas para saber cuánto cuesta)."))
    else:
        partes.append("No tienen deuda de tarjeta registrada, que es la parte más cara de arreglar cuando se acumula.")
    if ahorro["total"]:
        partes.append(
            f"Guardado tienen {_cop(ahorro['total'])}, de los cuales {_cop(ahorro['emergencia']['current'])} "
            f"están en el fondo de emergencia. Eso alcanza para "
            f"{ahorro['emergencia']['coverage_months']:.1f} meses de gastos obligatorios.")
    else:
        partes.append("Todavía no hay dinero reservado en cajitas; cualquier imprevisto hoy se paga con la caja del mes o con tarjeta.")
    disponible = max(capacidad_discrecional(mes, perfil)["maximo_discrecional"], 0)
    partes.append(
        f"En términos prácticos: hoy podrían gastar hasta {_cop(disponible)} sin tocar compromisos ya registrados."
        if disponible else
        "En términos prácticos: hoy no queda margen libre; todo lo disponible ya está comprometido.")
    return {"mes": mes, "explicacion": " ".join(partes), "partes": partes,
            "confianza": perfil["calidad_datos"]["level"],
            "nota": "Resumen en lenguaje llano de los registros del mes."}


# ---------------------------------------------------------------------------
# Estado financiero: respuesta de cabecera
# ---------------------------------------------------------------------------

def construir_contexto(mes: str) -> ctx.ContextoFinanciero:
    """Arma el contexto canónico del mes con todas sus secciones perezosas.

    Nada se calcula aquí: se declara cómo se calcula cada sección. La primera
    consulta la materializa y el resto de análisis la reutiliza, de modo que un
    informe completo no vuelve a recorrer el historial una y otra vez.
    """
    mes = validar_mes(mes)
    contexto = ctx.ContextoFinanciero(mes=mes)
    contexto.registrar("perfil", lambda c: perfil_financiero(c.mes))
    contexto.registrar("gastos_fijos_detectados",
                       lambda c: detectar_gastos_fijos(c.mes, incluir_registrados=True))
    contexto.registrar("cotejo_gastos_fijos",
                       lambda c: cotejar_gastos_fijos(c.mes, detectados=c.gastos_fijos_detectados))
    contexto.registrar("tarjetas_iq", lambda c: inteligencia_tarjetas(c.mes, c.perfil))
    contexto.registrar("ahorro_iq", lambda c: inteligencia_ahorro(c.mes, c.perfil))
    contexto.registrar("salud", lambda c: dimensiones_salud(c.mes, c.perfil))
    contexto.registrar("capacidad", lambda c: capacidad_discrecional(c.mes, c.perfil))
    contexto.registrar("anomalias", lambda c: detectar_anomalias(c.mes, c.perfil))
    contexto.registrar("tendencias", lambda c: tendencias(c.mes, 6))
    contexto.registrar("comparacion", lambda c: comparar_meses(c.mes))
    contexto.registrar("pareja", lambda c: analisis_pareja(c.mes, c.perfil))
    contexto.registrar("integridad", lambda c: informe_integridad(c.mes, perfil=c.perfil))
    contexto.registrar("proximos", lambda c: calendario_proximos_pagos(c.mes, c.perfil))
    contexto.registrar("riesgo", lambda c: af.modelo_riesgo(c.perfil, c.tendencias))
    contexto.registrar("oportunidades", lambda c: af.detectar_oportunidades(
        c.perfil, detectados=c.gastos_fijos_detectados, cotejo=c.cotejo_gastos_fijos,
        tendencias=c.tendencias, comparacion=c.comparacion))
    contexto.registrar("hallazgos_objeto", lambda c: construir_hallazgos(
        c.perfil, detectados=c.gastos_fijos_detectados, cotejo=c.cotejo_gastos_fijos,
        tendencia=c.tendencias, tarjetas_iq=c.seccion("tarjetas_iq"),
        ahorro_iq=c.seccion("ahorro_iq")))
    contexto.registrar("hallazgos",
                       lambda c: [item.a_dict() for item in c.seccion("hallazgos_objeto")])
    contexto.registrar("grupos", lambda c: af.agrupar(c.seccion("hallazgos_objeto")))
    contexto.registrar("plan", lambda c: af.plan_de_accion(
        c.seccion("hallazgos_objeto"), c.oportunidades, c.perfil))
    return contexto


def contexto_de(mes: str, *, refrescar: bool = False) -> ctx.ContextoFinanciero:
    """Contexto del mes, reutilizando el cacheado si sigue vigente."""
    return ctx.obtener_contexto(validar_mes(mes), constructor=construir_contexto, refrescar=refrescar)


def invalidar_contexto(mes: str | None = None) -> None:
    """Descarta la foto cacheada. La capa de servicio la llama tras escribir."""
    ctx.invalidar_contextos(mes)


def estado_actual(mes: str, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lectura general del momento financiero: hallazgos, riesgo y oportunidades."""
    contexto = contexto_de(mes)
    if perfil is not None:
        contexto._secciones.setdefault("perfil", perfil)
    contexto.precargar("perfil", "hallazgos", "riesgo", "oportunidades", "capacidad", "integridad")
    perfil = contexto.perfil
    hallazgos = contexto.hallazgos
    capacidad = contexto.capacidad
    criticos = [item for item in hallazgos if item["prioridad"] in ("critica", "alta")]
    if not perfil["ingresos"]["confirmado"] and not perfil["gastos"]["total"]:
        titular = (f"No hay movimientos registrados en {mes}, así que sobre ese mes no puedo concluir nada. "
                   "Lo que sigue viene del acumulado, no del mes.")
    elif criticos:
        titular = ("Revisando sus números: hay algo importante que atender antes que cualquier otra cosa."
                   if len(criticos) == 1 else
                   f"Revisando sus números: hay {len(criticos)} cosas que pediría atender antes que cualquier otra.")
    elif int(perfil["flujo"]["ahorro"]) > 0:
        titular = (f"El mes va en orden: {_cop(perfil['flujo']['ahorro'])} de excedente y "
                   f"{_cop(capacidad['maximo_discrecional'])} de margen discrecional disponible.")
    else:
        titular = "El mes está ajustado: no hay excedente, aunque tampoco señales críticas."
    return {
        "mes": mes, "titular": titular, "perfil": perfil, "hallazgos": hallazgos,
        "grupos": contexto.seccion("grupos"), "plan": contexto.seccion("plan"),
        "riesgo": contexto.riesgo, "oportunidades": contexto.oportunidades,
        "salud": contexto.salud, "capacidad": capacidad, "integridad": contexto.integridad,
        "anomalias": contexto.anomalias, "comparacion": contexto.comparacion,
        "pareja": contexto.pareja, "tendencias": contexto.tendencias,
        "cotejo_gastos_fijos": contexto.cotejo_gastos_fijos,
        "proximos_pagos": contexto.proximos,
        "confianza": perfil["calidad_datos"]["level"],
    }


def plan_de_accion(mes: str) -> dict[str, Any]:
    """Responde «¿qué deberíamos hacer?» con horizontes y justificación."""
    return contexto_de(mes).seccion("plan")


def riesgo_financiero(mes: str) -> dict[str, Any]:
    """Modelo de riesgo multifactor, con las dimensiones separadas."""
    return contexto_de(mes).riesgo


def oportunidades(mes: str) -> list[dict[str, Any]]:
    """Oportunidades derivadas de los datos registrados."""
    return contexto_de(mes).oportunidades


def resumen_ejecutivo(mes: str, perfil: dict[str, Any] | None = None, limite: int = 3) -> str:
    """Párrafo corto y directo con lo que importa del mes."""
    estado = estado_actual(mes, perfil)
    lineas = [estado["titular"]]
    for hallazgo in estado["hallazgos"][:limite]:
        lineas.append(f"{hallazgo['titulo']}: {hallazgo['que']} {hallazgo['accion']}")
    if estado["integridad"]["descuadre"]:
        lineas.append(estado["integridad"]["mensaje"])
    return " ".join(lineas)


def explicacion_estructurada(mes: str) -> dict[str, Any]:
    """Narrativa completa en nueve bloques: de dónde viene la plata hasta qué hacer."""
    contexto = contexto_de(mes)
    perfil = contexto.perfil
    ingresos, gastos = perfil["ingresos"], perfil["gastos"]
    tarjetas, ahorro, flujo = perfil["tarjetas"], perfil["ahorro"], perfil["flujo"]
    fijos = gastos["gastos_fijos_registrados"]
    riesgo = contexto.riesgo
    bloques: list[dict[str, Any]] = []

    bloques.append({"titulo": "De dónde viene la plata", "orden": 1,
                    "texto": (f"Este mes entraron {_cop(ingresos['total'])}: {_cop(ingresos['samuel'])} de Samuel "
                              f"y {_cop(ingresos['sara'])} de Sara."
                              if ingresos["total"] else
                              "No hay ingresos registrados este mes, así que varios cálculos quedan sin base."),
                    "datos": {"total": ingresos["total"], "samuel": ingresos["samuel"],
                              "sara": ingresos["sara"], "estabilidad": ingresos["estabilidad"]}})

    principales = gastos["por_categoria"][:3]
    bloques.append({"titulo": "A dónde se va", "orden": 2,
                    "texto": (f"Salieron {_cop(flujo['salidas'])} en total. Lo más pesado: "
                              + ", ".join(f"{item['categoria']} ({_cop(item['monto'])})" for item in principales)
                              + f". Lo obligatorio fue {_cop(gastos['obligatorios'])} y lo discrecional "
                                f"{_cop(gastos['discrecionales'])}."
                              if gastos["total"] else "No hay gastos registrados este mes."),
                    "datos": {"salidas": flujo["salidas"], "categorias": gastos["por_categoria"][:6]}})

    bloques.append({"titulo": "Compromisos fijos", "orden": 3,
                    "texto": (f"Tienen {fijos['cantidad_activos']} gastos fijos registrados que equivalen a "
                              f"{_cop(fijos['total_mensual_equivalente'])} al mes"
                              + (f", o sea {_pct(fijos['porcentaje_ingreso'])} del ingreso."
                                 if fijos["porcentaje_ingreso"] else ".")
                              + (f" Además detecté {contexto.cotejo_gastos_fijos['total_desajustes']} desajustes "
                                 "entre lo registrado y lo que se está cobrando."
                                 if contexto.cotejo_gastos_fijos["total_desajustes"] else "")),
                    "datos": {"fijos": fijos, "cotejo": contexto.cotejo_gastos_fijos}})

    bloques.append({"titulo": "Deuda y tarjetas", "orden": 4,
                    "texto": (f"Con las tarjetas deben {_cop(tarjetas['deuda_total'])} "
                              f"({_pct(tarjetas['utilizacion_global'])} del cupo). El mínimo del mes suma "
                              f"{_cop(tarjetas['pago_minimo_total'])}"
                              + (f" y arrastrar ese saldo cuesta cerca de "
                                 f"{_cop(tarjetas['interes_estimado_mensual'])} al mes."
                                 if tarjetas["interes_estimado_mensual"] else
                                 " (faltan tasas para saber cuánto cuesta).")
                              if tarjetas["deuda_total"] else
                              "No hay deuda de tarjeta registrada."),
                    "datos": contexto.seccion("tarjetas_iq")})

    bloques.append({"titulo": "Ahorro y metas", "orden": 5,
                    "texto": (f"Hay {_cop(ahorro['total'])} reservados, con {_cop(ahorro['emergencia']['current'])} "
                              f"en el fondo de emergencia: {ahorro['emergencia']['coverage_months']:.1f} meses de "
                              "gasto obligatorio cubiertos."
                              if ahorro["total"] else
                              "Todavía no hay dinero reservado en cajitas."),
                    "datos": contexto.seccion("ahorro_iq")})

    bloques.append({"titulo": "Liquidez hoy", "orden": 6,
                    "texto": (f"La liquidez operativa acumulada es {_cop(flujo['liquidez_operativa'])}. Descontando "
                              f"reservas y compromisos, el margen discrecional es "
                              f"{_cop(contexto.capacidad['maximo_discrecional'])}."),
                    "datos": contexto.capacidad})

    bloques.append({"titulo": "Riesgos principales", "orden": 7, "texto": riesgo["lectura"],
                    "datos": {"nivel": riesgo["nivel"], "factores": riesgo["factores"]}})

    oportunidades_lista = contexto.oportunidades
    bloques.append({"titulo": "Oportunidades", "orden": 8,
                    "texto": (" ".join(f"{item['titulo']}: {item['accion']}" for item in oportunidades_lista[:3])
                              or "No encuentro oportunidades claras con los datos registrados."),
                    "datos": oportunidades_lista})

    plan = contexto.seccion("plan")
    bloques.append({"titulo": "Qué haría ahora", "orden": 9, "texto": plan["resumen"], "datos": plan})

    return {"mes": mes, "bloques": bloques,
            "narrativa": " ".join(bloque["texto"] for bloque in bloques),
            "confianza": perfil["calidad_datos"]["level"]}


# ---------------------------------------------------------------------------
# Motor de respuesta conversacional
# ---------------------------------------------------------------------------

_MULTIPLICADORES = {
    "k": 1_000, "mil": 1_000, "miles": 1_000,
    "m": 1_000_000, "millon": 1_000_000, "millones": 1_000_000, "milloncito": 1_000_000,
}

_NOMBRE_DESTINO: dict[str, str] = {
    "mexico": "México", "espana": "España", "colombia": "Colombia", "peru": "Perú",
    "chile": "Chile", "argentina": "Argentina", "brasil": "Brasil", "portugal": "Portugal",
    "italia": "Italia", "francia": "Francia", "japon": "Japón", "estados unidos": "Estados Unidos",
    "canada": "Canadá", "cartagena": "Cartagena", "medellin": "Medellín",
    "santa marta": "Santa Marta", "san andres": "San Andrés", "cancun": "Cancún",
    "madrid": "Madrid", "barcelona": "Barcelona", "bogota": "Bogotá",
    "eje cafetero": "Eje Cafetero",
}

_DESTINOS_CONOCIDOS = (
    "mexico", "espana", "colombia", "peru", "chile", "argentina", "brasil", "portugal",
    "italia", "francia", "japon", "estados unidos", "canada", "cartagena", "medellin",
    "santa marta", "san andres", "cancun", "madrid", "barcelona", "bogota", "eje cafetero",
)


_PATRON_ESCALA = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d{1,2})?)\s*"
    r"(millones|millon|mill|mil|miles|k|m)\b")
_PATRON_NUMERO = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{3,})(?![\d.,])")


def _interpretar_numero(bruto: str) -> float:
    """Lee un número en formato colombiano sin confundir miles con decimales.

    ``1.500`` son mil quinientos (separador de miles), mientras que ``1.5`` es
    uno con cinco. La diferencia está en cuántos dígitos siguen al separador:
    tres exactos y repetidos indican miles; uno o dos indican decimales.
    """
    texto = bruto.strip()
    if re.fullmatch(r"\d+", texto):
        return float(texto)
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", texto):
        return float(re.sub(r"[.,]", "", texto))
    decimal = re.fullmatch(r"(\d+)[.,](\d{1,2})", texto)
    if decimal:
        return float(f"{decimal.group(1)}.{decimal.group(2)}")
    return float(re.sub(r"[.,]", "", texto))


def extraer_monto(texto: str) -> int | None:
    """Extrae un monto en pesos de lenguaje natural colombiano.

    Soporta 150.000, 150,000, 150000, «150 mil», 150k, «1.5 millones»,
    1.500.000, $150.000 y «COP 150.000». Ignora fechas AAAA-MM y números
    sueltos de una o dos cifras, que casi nunca son montos.
    """
    if not isinstance(texto, str) or not texto.strip():
        return None
    # Se normaliza sin destruir los separadores numéricos: «150.000» tiene que
    # seguir siendo un solo número al llegar a las expresiones regulares.
    plano = unicodedata.normalize("NFKD", texto.replace("'", ".").replace("\u00a0", " "))
    plano = plano.encode("ascii", "ignore").decode("ascii").lower()
    plano = re.sub(r"[^0-9a-z.,\s-]", " ", plano)
    plano = re.sub(r"\b20\d{2}\s*-\s*(0[1-9]|1[0-2])\b", " ", plano)   # fechas AAAA-MM
    plano = re.sub(r"\bcop\b|\bcops\b|\bpesos\b", " ", plano)
    plano = re.sub(r"\s+", " ", plano)
    con_escala = _PATRON_ESCALA.search(plano)
    if con_escala:
        numero = _interpretar_numero(con_escala.group(1))
        factor = _MULTIPLICADORES.get(con_escala.group(2), 1)
        # «1.500 mil» no existe en el habla real: si el número ya venía con
        # separador de miles, la palabra es redundante y no vuelve a escalar.
        if factor == 1_000 and numero >= 1_000:
            return int(round(numero))
        return int(round(numero * factor))
    valores = [_interpretar_numero(coincidencia.group(1)) for coincidencia in _PATRON_NUMERO.finditer(plano)]
    valores = [valor for valor in valores if valor >= 100]
    return int(round(valores[0])) if valores else None


def extraer_persona(texto: str) -> str | None:
    plano = normalizar_texto(texto)
    if "samuel" in plano:
        return SAMUEL
    if "sara" in plano:
        return SARA
    return None


def extraer_destinos(texto: str) -> list[str]:
    plano = normalizar_texto(texto)
    return [destino for destino in _DESTINOS_CONOCIDOS if destino in plano]


def extraer_mes(texto: str) -> str | None:
    encontrado = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", texto)
    return encontrado.group(0) if encontrado else None


# Cada intención declara frases fuertes (peso 3) y palabras de apoyo (peso 1).
_REGLAS_INTENCION: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("data_integrity", ("descuadre", "no cuadra", "cuadra el libro", "integridad", "diferencia historica",
                        "error en los datos", "inconsistencia"),
     ("cuadrar", "auditoria", "revisar datos", "faltante contable")),
    ("fixed_expense", ("gasto fijo", "gastos fijos", "podria ser un gasto fijo", "es un gasto fijo",
                       "esto es fijo", "seria fijo"),
     ("fijo", "fijos", "suscripcion", "suscripciones", "mensualidad")),
    ("recurring_expense", ("gastos recurrentes", "se repiten", "parecen ser fijos", "parecen fijos",
                           "cobros repetidos", "que se repite cada mes", "pagos automaticos",
                           "no estan registrados", "sin registrar", "todavia no estan"),
     ("recurrente", "recurrentes", "repite", "repiten", "todos los meses", "cada mes")),
    ("travel_comparison", ("nos conviene mas ir", "podemos viajar", "viajar a", "que destino",
                           "mexico o espana", "comparar destinos", "alcanza para un viaje"),
     ("viaje", "viajar", "destino", "destinos", "vacaciones", "tiquetes", "pasajes")),
    ("card_payment", ("cuanto deberiamos pagarle a la tarjeta", "cuanto le pago a la tarjeta",
                      "cuanto abonar", "pagar la tarjeta", "abono a la tarjeta", "con que tarjeta",
                      "debito o credito", "como pago esto", "como deberiamos pagar", "como pagamos"),
     ("tarjeta", "tarjetas", "credito", "debito", "abonar", "abono", "pagar", "pago minimo")),
    ("card_deferral", ("diferir la tarjeta", "diferir alguna tarjeta", "diferir tarjetas",
                          "diferir una compra", "diferir compras", "diferir una tarjeta y pagar",
                          "diferir alguna y pagar"),
     ("diferir", "diferida", "diferidas", "cuotas")),
    ("debt_strategy", ("que deuda", "cual deuda", "que pagar primero", "atacar primero", "avalancha",
                       "bola de nieve", "snowball", "cuando salimos de las tarjetas", "salir de deudas"),
     ("deuda", "deudas", "intereses", "estrategia", "prioridad de pago")),
    ("scenario", ("que pasa si", "que pasaria si", "simula", "simular", "escenario", "si gastamos",
                  "si pagamos", "si gano menos", "si dejamos de"),
     ("simulacion", "hipotetico", "supongamos")),
    ("affordability", ("podemos salir", "podemos gastar", "puedo gastar", "cuanto podemos gastar",
                       "alcanza para", "nos alcanza", "podemos permitirnos", "podemos darnos",
                       "cuanto nos queda", "cuanto dinero nos queda", "cuanto podemos"),
     ("salida", "salir", "rumba", "cine", "plan", "gastar", "presupuesto", "disponible")),
    ("purchase_evaluation", ("podemos comprar", "puedo comprar", "deberiamos comprar", "vale la pena comprar",
                             "comprar una moto", "comprar un carro", "conviene comprar"),
     ("comprar", "compra", "moto", "carro", "celular", "computador", "televisor")),
    ("savings", ("podemos ahorrar", "capacidad de ahorro", "cuanto deberiamos ahorrar", "fondo de emergencia",
                 "nuestras metas", "meta de ahorro", "estamos ahorrando"),
     ("ahorro", "ahorrar", "cajita", "cajitas", "meta", "metas", "emergencia")),
    ("couple_analysis", ("quien esta gastando mas", "quien gasta mas", "cuanto le debo a sara",
                         "cuanto me debe samuel", "quien debe", "como vamos sara y yo", "balance de pareja",
                         "quien aporto mas"),
     ("sara", "samuel", "pareja", "debemos", "aporto", "aportes", "equilibrio")),
    ("monthly_comparison", ("mes pasado", "mes anterior", "comparado con el mes", "comparados con el mes",
                            "versus el mes", "estamos gastando mas que", "comparacion mensual",
                            "que el mes", "frente al mes"),
     ("comparar", "comparados", "comparado", "comparacion", "anterior", "tendencia", "tendencias",
      "evolucion")),
    ("spending_analysis", ("por que estamos gastando tanto", "donde se nos esta yendo la plata",
                           "en que gastamos", "que gastos podriamos reducir", "donde gastamos mas",
                           "analiza nuestros gastos"),
     ("gastos", "gasto", "categorias", "reducir", "recortar", "plata")),
    ("anomaly", ("algo raro", "algo extrano", "anomalia", "anomalias", "cobro duplicado", "cobro raro",
                 "algo fuera de lo normal"),
     ("raro", "duplicado", "inusual", "extrano")),
    ("financial_explanation", ("explicame nuestras finanzas", "explicame como si", "no entiendo nada",
                               "explicamelo facil", "en palabras simples", "explicame la situacion"),
     ("explicame", "explicar", "entender", "simple", "facil")),
    ("current_state", ("cual es nuestra situacion financiera", "como estamos", "como vamos",
                       "situacion actual", "estado financiero", "resumen financiero", "como esta todo"),
     ("situacion", "estado", "resumen", "panorama", "general")),
)


def clasificar_intencion(pregunta: str) -> dict[str, Any]:
    """Clasifica la pregunta y extrae entidades. Sin coincidencia clara: unknown."""
    texto = normalizar_texto(pregunta)
    if not texto:
        raise ValueError("Escribe una pregunta para el asesor.")
    puntajes: dict[str, int] = defaultdict(int)
    coincidencias: dict[str, list[str]] = defaultdict(list)
    for intencion, fuertes, apoyos in _REGLAS_INTENCION:
        for frase in fuertes:
            if frase in texto:
                puntajes[intencion] += 3
                coincidencias[intencion].append(frase)
        for palabra in apoyos:
            if re.search(rf"\b{re.escape(palabra)}\b", texto):
                puntajes[intencion] += 1
                coincidencias[intencion].append(palabra)
    monto = extraer_monto(pregunta)
    palabras = texto.split()
    if texto.startswith(("por que", "porque")) and len(palabras) <= 4 and monto is None:
        # «¿por qué?» a secas pregunta por la respuesta anterior, no por un tema.
        puntajes["why"] += 5
    # Desambiguaciones finas: el monto y el verbo cambian el destino de la pregunta.
    if monto and "comprar" in texto:
        puntajes["purchase_evaluation"] += 2
    if monto and any(frase in texto for frase in ("que pasa si", "que pasaria si", "si gastamos", "si pagamos",
                                                   "y si", "si gasto", "si ahorramos", "si dejamos de",
                                                   "si compro", "si compramos")):
        puntajes["scenario"] += 4
    if any(frase in texto for frase in ("como deberia pagar", "como lo pago", "como lo pagamos", "como pagarlo",
                                        "con que lo pago", "con que pago", "pagarlo")):
        puntajes["card_payment"] += 4
    if any(palabra in texto for palabra in ("viaje", "viajar", "vacaciones")) and \
            any(palabra in texto for palabra in ("ahorrar", "ahorro", "cuanto", "alcanza")):
        # Preguntar cuánto ahorrar «para viajar» es una pregunta de viaje.
        puntajes["travel_comparison"] += 4
    if "tarjeta" in texto and any(frase in texto for frase in ("cuanto", "cuanto le", "abonar", "abono")):
        puntajes["card_payment"] += 2
    if not puntajes:
        return {"intencion": "unknown", "puntaje": 0, "coincidencias": [], "monto": monto,
                "persona": extraer_persona(pregunta), "destinos": extraer_destinos(pregunta),
                "mes": extraer_mes(pregunta), "texto": texto}
    intencion = max(puntajes, key=lambda clave: (puntajes[clave], clave))
    return {"intencion": intencion, "puntaje": puntajes[intencion],
            "coincidencias": coincidencias[intencion],
            "alternativas": sorted(((clave, valor) for clave, valor in puntajes.items() if clave != intencion),
                                   key=lambda par: -par[1])[:2],
            "monto": monto, "persona": extraer_persona(pregunta),
            "destinos": extraer_destinos(pregunta), "mes": extraer_mes(pregunta), "texto": texto}


def _respuesta(intencion: str, texto: str, datos: dict[str, Any], confianza: str,
               seguimiento: Sequence[str] = ()) -> dict[str, Any]:
    return {"intencion": intencion, "respuesta": texto.strip(), "confianza": confianza,
            "datos": datos, "preguntas_seguimiento": list(seguimiento)}


def _r_estado_actual(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    estado = estado_actual(mes, perfil)
    partes = [estado["titular"]]
    for hallazgo in estado["hallazgos"][:3]:
        partes.append(f"• {hallazgo['titulo']}. {hallazgo['que']} {hallazgo['accion']}")
    if estado["integridad"]["descuadre"]:
        partes.append(estado["integridad"]["mensaje"])
    return _respuesta("current_state", "\n".join(partes), estado, estado["confianza"],
                      ["¿Quieres que profundice en tarjetas, gastos o ahorro?",
                       "¿Reviso qué podrían gastar este mes sin comprometer nada?"])


def _r_asequibilidad(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    monto = ctx_dict.get("monto")
    evaluacion = evaluar_asequibilidad(mes, monto, perfil=perfil)
    texto = evaluacion["mensaje"]
    if evaluacion["proximos_pagos"]:
        proximo = evaluacion["proximos_pagos"][0]
        texto += f" Ojo con lo que viene: {proximo['titulo']} el {proximo['fecha']} por {_cop(proximo['monto'])}."
    if evaluacion["que_cambiaria"]:
        texto += " " + evaluacion["que_cambiaria"][0]
    return _respuesta("affordability", texto, evaluacion, evaluacion["confianza"],
                      ["¿Quieres que compare cómo pagarlo (efectivo, débito o tarjeta)?"] if monto else
                      ["¿Tienes un monto en mente? Con eso te digo si cabe o no."])


def _r_compra(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    monto = ctx_dict.get("monto")
    if monto is None:
        return _respuesta("purchase_evaluation",
                          "Dime el monto aproximado de la compra y te digo si cabe, cómo pagarla y qué deja atrás.",
                          {"requiere": ["monto"]}, "Baja",
                          ["¿Cuánto cuesta?", "¿Es algo que puede esperar un mes?"])
    evaluacion = evaluar_asequibilidad(mes, monto, concepto="esta compra", perfil=perfil)
    pago = opciones_de_pago(mes, monto, perfil=perfil)
    escenario = simular_escenario_detallado(mes, "compra_grande", monto, perfil=perfil)
    texto = (f"{evaluacion['mensaje']} Si la hacen, la forma que menos daño hace hoy es {pago['recomendada'].lower()}: "
             f"{pago['razon']} {escenario['explicacion']}")
    return _respuesta("purchase_evaluation", texto,
                      {"asequibilidad": evaluacion, "opciones_pago": pago, "escenario": escenario},
                      evaluacion["confianza"],
                      ["¿La compra es de los dos o de uno solo? Eso cambia cómo se reparte."])


def _r_diferir_tarjeta(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    """Aclara un diferimiento sin inventar compra, plazo, tasa ni costo."""
    texto = (
        "Sí puedo analizar un diferimiento, pero no voy a asumir que se puede diferir una tarjeta completa. "
        "Normalmente el diferimiento se aplica a una compra o saldo concreto y depende de las condiciones "
        "de la entidad. Para decirles qué conviene necesito saber: qué tarjeta(s) quieres diferir, qué compra o saldo quieres "
        "diferir y a cuántas cuotas. Con esos datos puedo comparar el costo y el efecto sobre los pagos."
    )
    return _respuesta(
        "card_deferral",
        texto,
        {
            "requiere": ["diferir", "priorizar", "presupuesto_disponible"],
            "nota": "No se simuló ningún diferimiento porque faltan datos concretos y las condiciones del emisor."
        },
        "Baja",
        ["¿Qué tarjeta y qué compra/saldo quieren diferir?", "¿A cuántas cuotas les ofrecen hacerlo?"],
    )


def _r_pago_tarjeta(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    tarjetas = perfil["tarjetas"]
    monto = ctx_dict.get("monto")
    _FRASES_METODO = ("como pago", "con que tarjeta", "debito o credito", "credito o debito",
                      "como pagamos", "como deberiamos pagar", "como deberia pagar", "como lo pago",
                      "como lo pagamos", "pagarlo", "con que lo pago", "con que pago", "que metodo")
    if monto and any(frase in ctx_dict["texto"] for frase in _FRASES_METODO):
        pago = opciones_de_pago(mes, monto, perfil=perfil)
        detalle = "\n".join(f"• {opcion['opcion']}: {opcion['impacto']} {opcion['riesgo']}"
                            for opcion in pago["opciones"][:3])
        texto = f"Para {_cop(monto)} yo iría por {pago['recomendada'].lower()}. {pago['razon']}\n{detalle}"
        return _respuesta("card_payment", texto, pago, pago["confianza"],
                          ["¿Quieres que simule cómo queda el mes después de pagarlo?"])
    quiere_metodo = any(frase in ctx_dict["texto"] for frase in _FRASES_METODO)
    if quiere_metodo and not monto:
        # Sin monto no simulo una compra concreta, pero sí puedo dar el criterio
        # con los números reales de hoy.
        capacidad = capacidad_discrecional(mes, perfil)
        comparacion = comparar_tarjetas(mes, perfil)
        texto = (f"Depende del monto, pero el criterio es este: hoy tienen {_cop(capacidad['maximo_discrecional'])} "
                 "de margen discrecional, así que todo lo que quepa ahí conviene pagarlo con débito o efectivo: "
                 "no crea deuda y no depende de que paguen a tiempo. "
                 + comparacion.get("lectura", "")
                 + " El cupo disponible no es plata suya: es deuda que todavía no han tomado.")
        return _respuesta("card_payment", texto,
                          {"capacidad": capacidad, "comparacion": comparacion}, perfil["calidad_datos"]["level"],
                          ["¿De cuánto es la compra? Con el monto te comparo las opciones una por una."])
    if not tarjetas["deuda_total"]:
        return _respuesta("card_payment", "No hay deuda de tarjeta registrada, así que no hay nada que abonar ahora.",
                          tarjetas, "Alta", [])
    capacidad = capacidad_discrecional(mes, perfil)
    minimos = tarjetas["pago_minimo_total"]
    margen = max(capacidad["maximo_discrecional"], 0)
    foco = max((item for item in tarjetas["tarjetas"] if item["saldo_deuda"]),
               key=lambda item: (item["interes_mensual"], item["utilizacion"]))
    # Abonar más que la deuda no tiene sentido, y vaciar el margen tampoco:
    # el tope es lo que falta de esa tarjeta después de su mínimo.
    techo_tarjeta = max(foco["saldo_deuda"] - foco["pago_minimo_efectivo"], 0)
    extra = min(margen, techo_tarjeta)
    proyeccion = engine.debt_projection(foco["id"], extra) if extra else None
    texto = (f"Lo mínimo del mes son {_cop(minimos)}. Con el margen disponible después de compromisos "
             f"({_cop(margen)}), el abono extra más útil va a {foco['nombre']}: tiene la tasa más alta registrada "
             f"({foco['interes_mensual']:.2f}% mensual) y {_pct(foco['utilizacion'], 0)} de utilización. "
             f"Con {_cop(extra)} adicionales esa tarjeta queda en cero, así que no tiene sentido mandarle más."
             if extra and extra >= techo_tarjeta else
             f"Lo mínimo del mes son {_cop(minimos)}. Con el margen disponible después de compromisos "
             f"({_cop(margen)}), el abono extra más útil va a {foco['nombre']}: tiene la tasa más alta registrada "
             f"({foco['interes_mensual']:.2f}% mensual) y {_pct(foco['utilizacion'], 0)} de utilización.")
    if proyeccion and proyeccion["months_saved"]:
        texto += (f" Abonando {_cop(extra)} extra, la proyección pasa de {_meses(proyeccion['months_current'])} "
                  f"a {_meses(proyeccion['months_improved'])} y ahorra cerca de {_cop(proyeccion['interest_saved'])} "
                  "en intereses.")
    elif not foco["interes_mensual"]:
        texto += " Falta registrar la tasa de esa tarjeta: sin ella no puedo cuantificar cuánto ahorran abonando."
    return _respuesta("card_payment", texto,
                      {"tarjetas": tarjetas, "capacidad": capacidad, "foco": foco, "proyeccion": proyeccion,
                       "abono_extra_sugerido": extra, "minimos": minimos},
                      perfil["calidad_datos"]["level"],
                      ["¿Quieres ver el plan completo para salir de todas las tarjetas?"])


def _r_estrategia_deuda(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    deuda = perfil["deuda"]
    if not deuda["tarjetas"] and not deuda["externa_por_pagar"]:
        return _respuesta("debt_strategy", "No hay deudas activas registradas para priorizar.", deuda, "Alta", [])
    avalancha, snowball = deuda["avalancha"], deuda["snowball"]
    orden = deuda["orden_avalancha"]
    texto_orden = ", ".join(f"{item['nombre']} ({item['tasa']:.2f}%)" for item in orden[:3]) or "sin tarjetas con saldo"
    texto = (f"Deben {_cop(deuda['total'])} en total y los mínimos del mes son {_cop(deuda['pagos_minimos'])}. "
             f"Por costo, el orden de ataque es: {texto_orden}. Esa es la estrategia avalancha: primero la tasa más alta, "
             "que es la que hace crecer la deuda más rápido.")
    if avalancha.get("viable") and snowball.get("viable"):
        diferencia = snowball["total_interest"] - avalancha["total_interest"]
        texto += (f" Con un presupuesto de {_cop(deuda['presupuesto_proyectado'])} al mes, avalancha termina en "
                  f"{_meses(avalancha['months'])} ({avalancha['debt_free_date']}) y snowball en "
                  f"{_meses(snowball['months'])}. "
                  + (f"La diferencia en intereses es {_cop(abs(diferencia))} a favor de avalancha."
                     if diferencia > 0 else
                     "En su caso las dos cuestan prácticamente lo mismo, así que pueden elegir la que les motive más."))
    elif not avalancha.get("viable"):
        texto += (" Con el excedente registrado no alcanza para proyectar una fecha de salida responsable: "
                  "el presupuesto mensual no cubre los mínimos.")
    return _respuesta("debt_strategy", texto, deuda, perfil["calidad_datos"]["level"],
                      ["¿Quieres que simule cuánto cambia si abonan un monto extra?"])


def _r_ahorro(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    ahorro = perfil["ahorro"]
    monto = ctx_dict.get("monto")
    capacidad = capacidad_discrecional(mes, perfil)
    if monto:
        escenario = simular_escenario_detallado(mes, "ahorro_adicional", monto, perfil=perfil)
        posible = escenario["margen_discrecional_despues"] >= 0 and escenario["despues"]["liquidez"] >= 0
        texto = (f"{'Sí, cabe.' if posible else 'Hoy no cabe sin apretar otra cosa.'} "
                 f"Apartar {_cop(monto)} dejaría el margen discrecional en "
                 f"{_cop(escenario['margen_discrecional_despues'])} y la liquidez en "
                 f"{_cop(escenario['despues']['liquidez'])}.")
        if not posible and perfil["gastos"]["discrecionales"]:
            texto += (f" Para lograrlo tendrían que recortar gasto discrecional, que este mes va en "
                      f"{_cop(perfil['gastos']['discrecionales'])}.")
        return _respuesta("savings", texto, {"escenario": escenario, "ahorro": ahorro}, escenario["confianza"],
                          ["¿A qué cajita o meta iría ese aporte?"])
    emergencia = ahorro["emergencia"]
    texto = (f"Tienen {_cop(ahorro['total'])} reservados, con {_cop(emergencia['current'])} en el fondo de emergencia "
             f"({emergencia['coverage_months']:.1f} meses de gasto obligatorio cubiertos). "
             f"Este mes el aporte neto fue {_cop(ahorro['aporte_neto_mes'])} y el margen disponible es "
             f"{_cop(capacidad['maximo_discrecional'])}.")
    if ahorro["metas_atrasadas"]:
        texto += f" Van atrasadas: {', '.join(ahorro['metas_atrasadas'])}."
    if emergencia["shortfall"]:
        texto += (f" Para llegar a la referencia de {emergencia['target_months']} meses faltan "
                  f"{_cop(emergencia['shortfall'])}.")
    if ahorro["fatiga"]["activa"]:
        texto += (" Una cosa: el ritmo de ahorro está dejando poco margen operativo. Ahorrar está bien, "
                  "pero no a costa de terminar usando la tarjeta para lo del día a día.")
    return _respuesta("savings", texto, ahorro, perfil["calidad_datos"]["level"],
                      ["¿Quieres que priorice las metas según lo que deja el flujo?"])


def _r_gasto_fijo(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    texto_pregunta = ctx_dict["texto"]
    concepto = _concepto_consultado(texto_pregunta) or _comercio_mencionado(texto_pregunta, mes)
    if concepto:
        analisis = analizar_posible_gasto_fijo(concepto, monto=ctx_dict.get("monto"), mes=mes)
        cuerpo = analisis["respuesta"]
        if analisis["evidencia"]:
            cuerpo += "\n" + "\n".join(f"• {linea}" for linea in analisis["evidencia"][:4])
        return _respuesta("fixed_expense", cuerpo, analisis, analisis["confianza_etiqueta"],
                          ["¿Lo registro como gasto fijo? Dime sí y lo dejas listo desde la pestaña de gastos fijos."])
    registrados = perfil["gastos"]["gastos_fijos_registrados"]
    detectados = perfil["gastos"]["gastos_fijos_detectados"]
    lineas = [f"Gastos fijos registrados: {registrados['cantidad_activos']}, equivalentes a "
              f"{_cop(registrados['total_mensual_equivalente'])} al mes"
              + (f" ({_pct(registrados['porcentaje_ingreso'], 0)} del ingreso)."
                 if registrados["porcentaje_ingreso"] else ".")]
    for gasto in registrados["gastos"][:5]:
        lineas.append(f"• {gasto['nombre']}: {_cop(gasto['equivalente_mensual'])} ({gasto['frecuencia']})")
    if detectados["cantidad_nuevos"]:
        lineas.append(f"Además detecté {detectados['cantidad_nuevos']} que se comportan como fijos y no están "
                      f"registrados, por unos {_cop(detectados['total_mensual_estimado'])} al mes:")
        for item in detectados["candidatos"][:4]:
            lineas.append(f"• {item['nombre']}: {_cop(item['monto_promedio'])}, {item['ocurrencias']} meses, "
                          f"confianza {item['confianza'].lower()}.")
        lineas.append("No los registré: la confirmación es de ustedes.")
    return _respuesta("fixed_expense", "\n".join(lineas),
                      {"registrados": registrados, "detectados": detectados},
                      perfil["calidad_datos"]["level"],
                      ["¿Quieres que revise alguno en específico?"])


def _comercio_mencionado(texto: str, mes: str) -> str | None:
    """Busca en la pregunta el nombre de un comercio que ya exista en el historial."""
    palabras = set(texto.split())
    mejor: tuple[int, str] | None = None
    for serie in construir_series(hasta_mes=mes):
        tokens = set(serie.clave.split())
        if not tokens:
            continue
        comunes = tokens & palabras
        # Se exige coincidencia completa del nombre del comercio para no
        # confundir «gasto» o «pago» con un establecimiento real.
        if comunes == tokens and (mejor is None or len(tokens) > mejor[0]):
            mejor = (len(tokens), serie.nombre)
    return mejor[1] if mejor else None


def _concepto_consultado(texto: str) -> str | None:
    """Extrae el comercio del que se pregunta («¿Netflix es un gasto fijo?»)."""
    patrones = (
        r"(?:cobro|gasto|pago|cargo)\s+de\s+(.+?)\s+(?:parece|es|seria|sera|sigue)",
        r"(.+?)\s+parece\s+(?:ser\s+)?(?:un\s+)?gasto\s+fijo",
        r"(?:sera|seria|es|era)\s+(.+?)\s+un\s+gasto\s+fijo",
        r"(?:podria|puede)\s+ser\s+(.+?)\s+un\s+gasto\s+fijo",
        r"gasto\s+fijo\s*[:,]?\s*(.+)$",
        r"(.+?)\s+(?:es|seria|sera)\s+(?:un\s+)?(?:gasto\s+)?fijo",
        r"(?:revisa|analiza|mira)\s+(?:si\s+)?(.+?)\s+(?:es|seria)",
    )
    for patron in patrones:
        encontrado = re.search(patron, texto)
        if encontrado:
            candidato = encontrado.group(1).strip()
            candidato = re.sub(r"^(el|la|los|las|un|una|este|esta|ese|esa|mi|nuestro|nuestra)\s+", "", candidato)
            candidato = re.sub(r"^(cobro|gasto|pago|cargo)\s+de\s+", "", candidato)
            candidato = re.sub(r"\b(gasto|pago|cobro|de)\b\s*$", "", candidato).strip()
            if candidato and len(candidato) <= 60 and candidato not in ("esto", "eso", "nuestros", "los nuestros"):
                return candidato
    return None


def _r_recurrentes(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    detectados = detectar_gastos_fijos(mes, incluir_registrados=True)
    nuevos = [item for item in detectados["todos"] if not item["ya_registrado"]]
    if not detectados["todos"]:
        return _respuesta("recurring_expense",
                          "Todavía no encuentro patrones recurrentes claros. Necesito al menos tres meses del mismo "
                          "concepto con montos parecidos para llamarlo recurrente.",
                          detectados, "Baja", ["¿Quieres que revise un comercio en particular?"])
    lineas = [f"Encontré {len(detectados['todos'])} conceptos con patrón recurrente"
              + (f", de los cuales {len(nuevos)} no están registrados como gasto fijo." if nuevos else ".")]
    for item in detectados["todos"][:6]:
        estado = "ya registrado" if item["ya_registrado"] else "sin registrar"
        lineas.append(f"• {item['nombre']}: {_cop(item['monto_promedio'])} {item['frecuencia']}, "
                      f"{item['ocurrencias']} meses, variación {_pct(item['variacion_monto'], 1)}, "
                      f"confianza {item['confianza'].lower()} ({estado}).")
    lineas.append("Son patrones, no conclusiones: confirmen cuáles siguen vigentes antes de tratarlos como compromiso.")
    return _respuesta("recurring_expense", "\n".join(lineas), detectados,
                      "Media", ["¿Registro alguno como gasto fijo?"])


def _r_analisis_gastos(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    gastos = perfil["gastos"]
    comparacion = comparar_meses(mes)
    if not gastos["total"]:
        return _respuesta("spending_analysis", f"No hay gastos registrados en {mes}.", gastos, "Baja", [])
    top = gastos["por_categoria"][:4]
    lineas = [f"Este mes salieron {_cop(gastos['total'])} en {gastos['cantidad']} gastos. "
              f"Lo obligatorio fue {_cop(gastos['obligatorios'])} y lo discrecional {_cop(gastos['discrecionales'])}"
              + (f" ({_pct(gastos['porcentaje_discrecional'], 0)} del total)." if gastos["porcentaje_discrecional"] else ".")]
    lineas.append("Dónde se está yendo:")
    for item in top:
        lineas.append(f"• {item['categoria']}: {_cop(item['monto'])} ({_pct(item['participacion'], 0)})")
    if comparacion["suficiente_historial"] and comparacion["principales_subidas"]:
        subida = comparacion["principales_subidas"][0]
        lineas.append(f"Frente a {comparacion['mes_comparado']}, lo que más subió fue {subida['categoria']}: "
                      f"{_cop(subida['diferencia'])} más"
                      + (f" ({_pct(subida['variacion'], 0)})." if subida["variacion"] else "."))
    candidatos_recorte = [item for item in gastos["por_categoria"]
                          if item["monto"] and item["categoria"].lower() not in ("arriendo", "salud", "servicios")][:2]
    if gastos["discrecionales"]:
        lineas.append("El problema no es el gasto en sí, sino cuánto de eso se repite sin decidirlo: "
                      f"lo discrecional de este mes ({_cop(gastos['discrecionales'])}) es la parte sobre la que "
                      "sí tienen control inmediato"
                      + (f", empezando por {candidatos_recorte[0]['categoria']}." if candidatos_recorte else "."))
    return _respuesta("spending_analysis", "\n".join(lineas),
                      {"gastos": gastos, "comparacion": comparacion}, perfil["calidad_datos"]["level"],
                      ["¿Quieres ver qué gastos parecen fijos aunque no estén registrados?"])


def _r_pareja(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    analisis = analisis_pareja(mes, perfil)
    texto_pregunta = ctx_dict["texto"]
    if "debo" in texto_pregunta or "debe" in texto_pregunta or "quien debe" in texto_pregunta:
        if analisis["saldo_pendiente"]:
            texto = (f"{analisis['deudor']} le debe {_cop(analisis['saldo_pendiente'])} a {analisis['acreedor']} "
                     "según aportes, consumos y deuda pendiente registrados.")
        else:
            texto = "Con los registros activos no queda saldo pendiente entre ustedes dos."
        if not analisis["cuadra"]:
            texto += (f" Con una salvedad importante: el libro tiene un descuadre de "
                      f"{_cop(abs(analisis['descuadre']))}, así que ese saldo conviene revisarlo antes de liquidar.")
        return _respuesta("couple_analysis", texto, analisis, "Media" if not analisis["cuadra"] else "Alta",
                          ["¿Quieres ver el detalle de qué compone ese saldo?"])
    consumo = analisis["consumo_del_mes"]
    ingresos = analisis["ingresos_del_mes"]
    quien = "Samuel" if consumo["Samuel"] > consumo["Sara"] else "Sara" if consumo["Sara"] > consumo["Samuel"] else None
    lineas = [f"Este mes el consumo se repartió así: Samuel {_cop(consumo['Samuel'])}, Sara {_cop(consumo['Sara'])}. "
              f"Los ingresos fueron Samuel {_cop(ingresos['Samuel'])} y Sara {_cop(ingresos['Sara'])}."]
    if quien:
        lineas.append(f"{quien} consumió más este mes, pero eso solo importa si no coincide con lo que cada uno aporta.")
    if analisis["desbalance"] is not None and abs(analisis["desbalance"]) >= 0.15:
        lineas.append(f"Hay un desbalance de {_pct(abs(analisis['desbalance']), 0)}: {analisis['lectura_desbalance']}.")
    if analisis["saldo_pendiente"]:
        lineas.append(f"En el acumulado, {analisis['deudor']} le debe {_cop(analisis['saldo_pendiente'])} a "
                      f"{analisis['acreedor']}.")
    if analisis["gastos_sin_distribucion"]:
        lineas.append(f"Hay {len(analisis['gastos_sin_distribucion'])} gastos marcados como compartidos sin "
                      "distribución registrada; prefiero que los confirmen antes de repartirlos yo.")
    if analisis["compras_en_tarjeta_del_otro"]:
        lineas.append("Recuerden que hay compras hechas con la tarjeta de uno pero responsabilidad del otro: "
                      "las estoy leyendo por responsabilidad, no por quién puso el plástico.")
    return _respuesta("couple_analysis", "\n".join(lineas), analisis, perfil["calidad_datos"]["level"],
                      ["¿Quieres que calcule una liquidación para dejarlo en cero?"])


def _r_comparacion(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    comparacion = comparar_meses(mes, ctx_dict.get("mes"))
    if not comparacion["suficiente_historial"]:
        return _respuesta("monthly_comparison",
                          f"No tengo datos suficientes de {comparacion['mes_comparado']} para comparar.",
                          comparacion, "Baja", [])
    lineas = [f"Contra {comparacion['mes_comparado']}: ingresos {_cop(comparacion['ingresos']['diferencia'])} "
              f"de diferencia, gastos {_cop(comparacion['gastos']['diferencia'])} y flujo "
              f"{_cop(comparacion['flujo']['diferencia'])}."]
    for item in comparacion["principales_subidas"][:3]:
        variacion = f" ({_pct(item['variacion'], 0)})" if item["variacion"] else ""
        lineas.append(f"• {item['categoria']} subió {_cop(item['diferencia'])}{variacion}.")
    for item in comparacion["principales_bajadas"][:2]:
        lineas.append(f"• {item['categoria']} bajó {_cop(abs(item['diferencia']))}.")
    tendencia = tendencias(mes, 6)
    if tendencia["gastos"]["direccion"] != "sin_datos":
        lineas.append(f"Mirando más atrás, el gasto viene {tendencia['gastos']['direccion']} y el flujo "
                      f"{tendencia['flujo']['direccion']}.")
    return _respuesta("monthly_comparison", "\n".join(lineas),
                      {"comparacion": comparacion, "tendencias": tendencia},
                      perfil["calidad_datos"]["level"],
                      ["¿Quieres el detalle de alguna categoría?"])


def _r_anomalias(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    anomalias = detectar_anomalias(mes, perfil)
    if not anomalias:
        return _respuesta("anomaly", "No encuentro señales fuera de lo normal en los registros de este mes.",
                          {"anomalias": []}, perfil["calidad_datos"]["level"], [])
    lineas = [f"Encontré {len(anomalias)} cosas que revisaría:"]
    for item in anomalias[:5]:
        lineas.append(f"• {item['titulo']}: {item['detalle']}")
    lineas.append("Ninguna es necesariamente un error; son señales para confirmar.")
    return _respuesta("anomaly", "\n".join(lineas), {"anomalias": anomalias},
                      perfil["calidad_datos"]["level"], ["¿Reviso alguna en detalle?"])


_PALABRAS_ESCENARIO: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pago_tarjeta", ("pagamos la tarjeta", "pagamos de la tarjeta", "abonamos", "pago a la tarjeta",
                      "pagamos 1", "pagamos un millon")),
    ("perdida_ingresos", ("gano menos", "ganamos menos", "me pagan menos", "pierdo el trabajo",
                          "sin trabajo", "baja el ingreso", "ingreso menor")),
    ("ingreso_extra", ("gano mas", "ingreso extra", "nos entra", "recibimos", "bono", "prima")),
    ("reduccion_gastos", ("dejamos de gastar", "recortamos", "reducimos", "dejamos de pagar")),
    ("ahorro_adicional", ("ahorramos", "guardamos", "aportamos a")),
    ("retiro_cajita", ("sacamos de la cajita", "retiramos del ahorro", "sacamos del ahorro")),
    ("nueva_deuda", ("pedimos un prestamo", "nos prestan", "nueva deuda", "credito nuevo")),
    ("gasto", ("gastamos", "si gastamos", "gasto de")),
)


def _r_escenario(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    texto = ctx_dict["texto"]
    monto = ctx_dict.get("monto")
    tipo = next((clave for clave, frases in _PALABRAS_ESCENARIO if any(frase in texto for frase in frases)), None)
    if tipo is None:
        tipo = "gasto" if monto else None
    if monto is None:
        capacidad = capacidad_discrecional(mes, perfil)
        obligatorio = max(perfil["gastos"]["obligatorios"], 1)
        cobertura = perfil["flujo"]["disponible"] / obligatorio
        contexto = (f"Necesito el monto para simularlo; no lo voy a suponer. Lo que sí puedo darte como punto de "
                    f"partida: hoy el margen discrecional es {_cop(capacidad['maximo_discrecional'])}, el flujo del "
                    f"mes deja {_cop(int(perfil['flujo']['ahorro']))} y la caja disponible cubre "
                    f"{cobertura:.1f} meses de gasto obligatorio "
                    f"({_cop(perfil['gastos']['obligatorios'])} al mes).")
        if tipo == "perdida_ingresos":
            contexto += (" Dime cuánto menos entraría y calculo exactamente hasta dónde aguantan y qué "
                         "compromiso quedaría descubierto primero.")
        return _respuesta("scenario", contexto,
                          {"escenarios_disponibles": ESCENARIOS, "capacidad": capacidad,
                           "meses_de_cobertura": round(cobertura, 2)}, "Media",
                          ["¿De cuánto estamos hablando?"])
    if tipo is None:
        return _respuesta("scenario",
                          "Puedo simularlo sin registrar nada, pero dime de qué se trata: un gasto, un abono a "
                          "tarjeta, un ingreso menor, un aporte a ahorro o una deuda nueva.",
                          {"escenarios_disponibles": ESCENARIOS}, "Baja",
                          ["¿Es un gasto, un pago de tarjeta o un cambio de ingreso?"])
    escenario = simular_escenario_detallado(mes, tipo, monto, perfil=perfil)
    texto_salida = f"{escenario['descripcion']} de {_cop(monto)}. {escenario['explicacion']}"
    if escenario["consecuencias"]:
        texto_salida += "\n" + "\n".join(f"• {linea}" for linea in escenario["consecuencias"])
    texto_salida += f"\nMargen discrecional: {_cop(escenario['margen_discrecional_antes'])} → " \
                    f"{_cop(escenario['margen_discrecional_despues'])}."
    return _respuesta("scenario", texto_salida, escenario, escenario["confianza"],
                      ["¿Quieres comparar este escenario con otro?"])


def _r_viaje(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    destinos = ctx_dict.get("destinos") or []
    contexto = comparar_destinos(mes, [], perfil=perfil)["contexto_financiero"]
    if destinos:
        nombres = " y ".join(_NOMBRE_DESTINO.get(destino, destino.capitalize()) for destino in destinos[:3])
        texto = (f"Puedo comparar {nombres}, pero con criterio financiero, no turístico, y para eso necesito los "
                 "costos estimados de cada uno: tiquetes, alojamiento por noche, comida por día, transporte local, "
                 "actividades, noches y cuántos viajan. Los precios de vuelos y hoteles cambian por fechas y son "
                 "información externa: no los voy a inventar.\n"
                 f"Lo que sí puedo decirles hoy: tienen {_cop(contexto['ahorro_libre_para_viaje'])} de ahorro libre "
                 f"(fuera del fondo de emergencia), el flujo mensual deja {_cop(contexto['flujo_mensual'])} "
                 f"y hay {_cop(contexto['deuda_tarjetas'])} de deuda de tarjeta compitiendo por ese mismo dinero.")
    else:
        texto = ("Para hablar de viaje necesito dos cosas: a dónde y con qué costos estimados. "
                 f"Del lado de ustedes, hoy hay {_cop(contexto['ahorro_libre_para_viaje'])} de ahorro libre y "
                 f"{_cop(contexto['flujo_mensual'])} de flujo mensual.")
    return _respuesta("travel_comparison", texto,
                      {"contexto_financiero": contexto, "plantilla": plantilla_viaje(), "destinos": destinos},
                      perfil["calidad_datos"]["level"],
                      ["¿Me pasas los costos estimados de cada destino?",
                       "¿Para cuándo lo están pensando? Con eso calculo el ahorro mensual necesario."])


def _r_explicacion(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    explicacion = explicar_finanzas_simple(mes, perfil)
    return _respuesta("financial_explanation", explicacion["explicacion"], explicacion,
                      explicacion["confianza"], ["¿Quieres que profundice en alguna parte?"])


def _r_integridad(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    informe = informe_integridad(mes, perfil=perfil)
    lineas = [informe["mensaje"]]
    for candidato in informe["candidatos_descuadre"][:5]:
        lineas.append(f"• {candidato['tipo']} #{candidato['id']}: {candidato['nombre']} por "
                      f"{_cop(candidato['valor'])}. {candidato['motivo']}")
    for hallazgo in informe["hallazgos_criticos"][:3]:
        lineas.append(f"• {hallazgo['detail']}")
    if informe["datos_faltantes"]:
        lineas.append("Datos que faltan: " + ", ".join(informe["datos_faltantes"]) + ".")
    return _respuesta("data_integrity", "\n".join(lineas), informe, "Alta",
                      ["¿Quieres que revise uno de esos movimientos en detalle?"])


def _r_desconocida(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    texto = ("No estoy seguro de qué me estás preguntando, y prefiero decirlo a inventar una respuesta. "
             "Puedo ayudarte con: cómo van este mes, si alcanza para un gasto o una salida, cómo pagar algo, "
             "qué deuda atacar primero, ahorro y metas, gastos fijos y cobros recurrentes, en qué se está yendo "
             "la plata, cómo van ustedes dos, comparar meses, detectar cosas raras, simular un «qué pasa si», "
             "y revisar la integridad de los datos.")
    return _respuesta("unknown", texto, {"sugerencias": [regla[0] for regla in _REGLAS_INTENCION]}, "Baja",
                      ["¿Me lo puedes decir de otra forma o con un monto concreto?"])


# ---------------------------------------------------------------------------
# Cotejo entre gastos fijos registrados y lo que de verdad se está cobrando
# ---------------------------------------------------------------------------

def cotejar_gastos_fijos(mes: str | None = None, *, detectados: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compara la lista de gastos fijos registrada contra el historial real.

    Detecta seis desajustes distintos: detectado pero no registrado, registrado
    pero sin cobros recientes, monto desactualizado, frecuencia distinta,
    responsabilidad distinta y titular distinto. Ninguno se corrige solo: el
    resultado es material para que la pareja decida.
    """
    referencia = validar_mes(mes) if mes else (meses_con_actividad() or [dt.date.today().strftime("%Y-%m")])[-1]
    detectados = detectados or detectar_gastos_fijos(referencia, incluir_registrados=True)
    registrados = db.get_gastos_fijos(solo_activos=True)
    por_clave = {item["clave"]: item for item in detectados["todos"]}
    series = {serie.clave: serie for serie in construir_series(hasta_mes=referencia)}

    sin_registrar: list[dict[str, Any]] = []
    sin_cobros_recientes: list[dict[str, Any]] = []
    monto_desactualizado: list[dict[str, Any]] = []
    frecuencia_distinta: list[dict[str, Any]] = []
    responsabilidad_distinta: list[dict[str, Any]] = []
    titular_distinto: list[dict[str, Any]] = []

    for candidato in detectados["todos"]:
        if not candidato["ya_registrado"]:
            sin_registrar.append({
                "nombre": candidato["nombre"], "clave": candidato["clave"],
                "monto_promedio": candidato["monto_promedio"], "frecuencia": candidato["frecuencia"],
                "ocurrencias": candidato["ocurrencias"], "confianza": candidato["confianza"],
                "clase": candidato["clase"], "evidencia": candidato["evidencia"],
                "detalle": (f"«{candidato['nombre']}» se cobra {candidato['frecuencia']} por unos "
                            f"{_cop(candidato['monto_promedio'])} y no está en la lista de gastos fijos."),
            })

    for registrado in registrados:
        clave = _clave_comercio(registrado["nombre"])
        candidato = por_clave.get(clave)
        if candidato is None:
            candidato = next((item for item in detectados["todos"]
                              if set(item["clave"].split()) & set(clave.split())), None)
        serie = series.get(clave)
        if candidato is None and serie is None:
            sin_cobros_recientes.append({
                "gasto_fijo_id": registrado["id"], "nombre": registrado["nombre"],
                "valor_registrado": registrado["valor"], "meses_sin_verse": None,
                "detalle": (f"«{registrado['nombre']}» está registrado como gasto fijo por "
                            f"{_cop(registrado['valor'])}, pero no encuentro cobros con ese nombre en el "
                            "historial. Puede estar registrado con otro nombre o haber dejado de cobrarse."),
            })
            continue

        ultimo = candidato["ultima_aparicion"] if candidato else (serie.ultimo_mes if serie else "")
        inactividad = indice_mes(referencia) - indice_mes(ultimo) if ultimo else 99
        if inactividad >= int(PARAMS["meses_inactivo_para_dudar"]):
            sin_cobros_recientes.append({
                "gasto_fijo_id": registrado["id"], "nombre": registrado["nombre"],
                "valor_registrado": registrado["valor"], "meses_sin_verse": inactividad,
                "ultima_aparicion": ultimo,
                "detalle": (f"«{registrado['nombre']}» sigue activo como gasto fijo pero su último cobro "
                            f"registrado es de {ultimo}. Si ya lo cancelaron, el compromiso mensual está "
                            "sobrestimado en el tablero."),
            })

        promedio_real = candidato["monto_promedio"] if candidato else (serie.promedio if serie else 0)
        if promedio_real:
            # Se usa el promedio de los cobros recientes: un aumento de tarifa
            # se nota ahí antes que en el promedio histórico completo.
            recientes = [item["valor"] for item in (serie.movimientos if serie else [])][-4:]
            referencia_real = round(mean(recientes)) if recientes else promedio_real
            diferencia = referencia_real - registrado["valor"]
            umbral = max(round(registrado["valor"] * 0.07), 2000)
            if abs(diferencia) >= umbral:
                monto_desactualizado.append({
                    "gasto_fijo_id": registrado["id"], "nombre": registrado["nombre"],
                    "valor_registrado": registrado["valor"], "promedio_real": referencia_real,
                    "diferencia": diferencia, "cobros_considerados": len(recientes),
                    "detalle": (f"«{registrado['nombre']}» está registrado en {_cop(registrado['valor'])} al mes, "
                                f"pero los últimos {len(recientes)} cobros promedian {_cop(referencia_real)} "
                                f"({'+' if diferencia > 0 else ''}{_cop(diferencia)})."),
                })

        if candidato and candidato["frecuencia"] != "indeterminada" and registrado["frecuencia"] != candidato["frecuencia"]:
            frecuencia_distinta.append({
                "gasto_fijo_id": registrado["id"], "nombre": registrado["nombre"],
                "frecuencia_registrada": registrado["frecuencia"], "frecuencia_detectada": candidato["frecuencia"],
                "detalle": (f"«{registrado['nombre']}» figura como {registrado['frecuencia']} pero los cobros "
                            f"aparecen con patrón {candidato['frecuencia']}."),
            })

        if candidato and candidato["responsabilidad"] and registrado["responsabilidad"] != candidato["responsabilidad"]:
            responsabilidad_distinta.append({
                "gasto_fijo_id": registrado["id"], "nombre": registrado["nombre"],
                "responsabilidad_registrada": _etiqueta_responsabilidad(registrado["responsabilidad"]),
                "responsabilidad_observada": candidato["responsabilidad_nombre"],
                "detalle": (f"«{registrado['nombre']}» está registrado como responsabilidad de "
                            f"{_etiqueta_responsabilidad(registrado['responsabilidad'])}, pero los gastos reales "
                            f"se han cargado a {candidato['responsabilidad_nombre']}. Como la responsabilidad "
                            "no se deduce de quién paga, prefiero que lo confirmen."),
            })

        if candidato and candidato["pagador"] and registrado["propietario"] != candidato["pagador"]:
            titular_distinto.append({
                "gasto_fijo_id": registrado["id"], "nombre": registrado["nombre"],
                "titular_registrado": _nombre(registrado["propietario"]),
                "pagador_observado": candidato["pagador_nombre"],
                "detalle": (f"«{registrado['nombre']}» figura a nombre de {_nombre(registrado['propietario'])} "
                            f"pero quien suele desembolsarlo es {candidato['pagador_nombre']}. Eso no cambia "
                            "la responsabilidad económica, solo quién pone la caja."),
            })

    total_desajustes = (len(sin_registrar) + len(sin_cobros_recientes) + len(monto_desactualizado)
                        + len(frecuencia_distinta) + len(responsabilidad_distinta) + len(titular_distinto))
    ajuste_mensual = sum(item["diferencia"] for item in monto_desactualizado)
    return {
        "mes": referencia,
        "registrados": len(registrados),
        "detectados": len(detectados["todos"]),
        "sin_registrar": sin_registrar,
        "sin_cobros_recientes": sin_cobros_recientes,
        "monto_desactualizado": monto_desactualizado,
        "frecuencia_distinta": frecuencia_distinta,
        "responsabilidad_distinta": responsabilidad_distinta,
        "titular_distinto": titular_distinto,
        "total_desajustes": total_desajustes,
        "ajuste_mensual_estimado": ajuste_mensual,
        "impacto_no_registrado": sum(item["monto_promedio"] for item in sin_registrar),
        "nota": "Ningún desajuste se corrige automáticamente: son puntos para que ustedes confirmen.",
    }


# ---------------------------------------------------------------------------
# Inteligencia de tarjetas
# ---------------------------------------------------------------------------

def inteligencia_tarjetas(mes: str, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Señales de comportamiento por tarjeta, no solo saldos.

    Distingue titular de responsabilidad económica en todo momento: el saldo de
    una tarjeta pertenece a su titular frente al banco, pero el consumo puede
    ser del otro o compartido.
    """
    perfil = perfil or perfil_financiero(mes)
    bloque = perfil["tarjetas"]
    detalle: list[dict[str, Any]] = []
    for tarjeta in bloque["tarjetas"]:
        historial = calc.tendencia_tarjeta(tarjeta["id"], limite=6)
        saldos = [int(item.get("saldo_estimado") or item.get("deuda") or 0) for item in historial] \
            if historial and isinstance(historial[0], dict) else []
        actividad = tarjeta["actividad_mes"]
        señales: list[dict[str, Any]] = []

        if tarjeta["utilizacion"] >= float(PARAMS["utilizacion_critica"]):
            señales.append({"señal": "utilizacion_critica", "nivel": "alta",
                            "detalle": f"Utilización en {_pct(tarjeta['utilizacion'])}: casi sin cupo de maniobra."})
        elif tarjeta["utilizacion"] >= float(PARAMS["utilizacion_alta"]):
            señales.append({"señal": "utilizacion_alta", "nivel": "alta",
                            "detalle": f"Utilización en {_pct(tarjeta['utilizacion'])}."})
        elif tarjeta["utilizacion"] >= float(PARAMS["utilizacion_atencion"]):
            señales.append({"señal": "utilizacion_media", "nivel": "media",
                            "detalle": f"Utilización en {_pct(tarjeta['utilizacion'])}, todavía manejable."})

        if tarjeta["crecimiento_neto_mes"] > 0:
            señales.append({"señal": "saldo_creciendo", "nivel": "alta",
                            "detalle": (f"El saldo subió {_cop(tarjeta['crecimiento_neto_mes'])} neto: compras, "
                                        "intereses y cargos superaron los pagos.")})
        elif tarjeta["crecimiento_neto_mes"] < 0:
            señales.append({"señal": "saldo_bajando", "nivel": "positiva",
                            "detalle": f"El saldo bajó {_cop(abs(tarjeta['crecimiento_neto_mes']))} neto este mes."})

        minimo = tarjeta["pago_minimo_efectivo"]
        if minimo and actividad["pagos"] and abs(actividad["pagos"] - minimo) <= max(round(minimo * 0.05), 1000):
            señales.append({"señal": "dependencia_minimo", "nivel": "alta",
                            "detalle": ("El pago del mes coincide con el mínimo: la deuda se alarga y el costo "
                                        "se concentra en intereses.")})
        if tarjeta["interes_estimado"] and tarjeta["saldo_deuda"]:
            peso = _dividir(tarjeta["interes_estimado"], max(actividad["pagos"], 1))
            if peso and peso >= 0.30:
                señales.append({"señal": "interes_come_el_pago", "nivel": "alta",
                                "detalle": (f"De cada pago, cerca de {_pct(peso)} se va solo en intereses "
                                            f"({_cop(tarjeta['interes_estimado'])} al mes).")})
        if actividad["compras"]:
            mayor = max((compra for compra in db.get_compras_tarjeta(tarjeta["id"])
                         if compra.get("mes") == mes and compra.get("tipo", "COMPRA") == "COMPRA"),
                        key=lambda compra: compra["valor_original"], default=None)
            if mayor and mayor["valor_original"] >= actividad["compras"] * 0.6 and actividad["compras"] > 0:
                señales.append({"señal": "compra_concentrada", "nivel": "media",
                                "detalle": (f"Una sola compra («{mayor['descripcion']}», "
                                            f"{_cop(mayor['valor_original'])}) explica la mayor parte del consumo "
                                            "del mes en esta tarjeta.")})
        if tarjeta["cupo_disponible"] < max(tarjeta["cupo_total"] * 0.1, 1):
            señales.append({"señal": "cupo_bajo", "nivel": "alta",
                            "detalle": f"Queda {_cop(tarjeta['cupo_disponible'])} de cupo disponible."})
        if tarjeta["datos_faltantes"]:
            señales.append({"señal": "datos_incompletos", "nivel": "media",
                            "detalle": "Faltan " + ", ".join(tarjeta["datos_faltantes"]) + "."})

        deuda_p1, deuda_p2 = tarjeta["deuda_persona1"], tarjeta["deuda_persona2"]
        del_otro = (deuda_p2 if tarjeta["propietario"] == SAMUEL else deuda_p1)
        if del_otro:
            otro = NOMBRES[SARA] if tarjeta["propietario"] == SAMUEL else NOMBRES[SAMUEL]
            señales.append({"señal": "responsabilidad_cruzada", "nivel": "info",
                            "detalle": (f"{_cop(del_otro)} del saldo corresponde económicamente a {otro}, "
                                        f"aunque la tarjeta sea de {_nombre(tarjeta['propietario'])}.")})

        detalle.append({
            "id": tarjeta["id"], "nombre": tarjeta["nombre"],
            "titular": tarjeta["titular_nombre"], "saldo": tarjeta["saldo_deuda"],
            "cupo": tarjeta["cupo_total"], "disponible": tarjeta["cupo_disponible"],
            "utilizacion": tarjeta["utilizacion"], "interes_mensual": tarjeta["interes_mensual"],
            "interes_estimado": tarjeta["interes_estimado"], "pago_minimo": minimo,
            "fecha_corte": tarjeta.get("fecha_corte"), "fecha_pago": tarjeta.get("fecha_pago"),
            "actividad_mes": actividad, "crecimiento_neto": tarjeta["crecimiento_neto_mes"],
            "deuda_responsabilidad": {"Samuel": deuda_p1, "Sara": deuda_p2},
            "historial": historial, "saldos_historicos": saldos,
            "señales": señales,
            "datos_faltantes": tarjeta["datos_faltantes"],
        })

    concentracion = None
    if bloque["deuda_total"]:
        mayor = max(detalle, key=lambda item: item["saldo"])
        concentracion = {"tarjeta": mayor["nombre"],
                         "participacion": _dividir(mayor["saldo"], bloque["deuda_total"])}
    return {"mes": mes, "tarjetas": detalle, "deuda_total": bloque["deuda_total"],
            "utilizacion_global": bloque["utilizacion_global"],
            "pago_minimo_total": bloque["pago_minimo_total"],
            "interes_estimado_total": bloque["interes_estimado_mensual"],
            "concentracion": concentracion,
            "con_señales_altas": [item["nombre"] for item in detalle
                                  if any(s["nivel"] == "alta" for s in item["señales"])],
            "regla": "El titular responde ante el banco; la responsabilidad económica se lee del registro."}


def comparar_tarjetas(mes: str, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compara las tarjetas entre sí para decidir cuál usar y cuál atacar."""
    perfil = perfil or perfil_financiero(mes)
    tarjetas = [item for item in perfil["tarjetas"]["tarjetas"] if item.get("activa", True)]
    if not tarjetas:
        return {"mes": mes, "tarjetas": [], "mensaje": "No hay tarjetas registradas."}
    filas = [{
        "id": item["id"], "nombre": item["nombre"], "titular": item["titular_nombre"],
        "saldo": item["saldo_deuda"], "cupo": item["cupo_total"], "disponible": item["cupo_disponible"],
        "utilizacion": item["utilizacion"], "tasa": item["interes_mensual"],
        "interes_estimado": item["interes_estimado"], "minimo": item["pago_minimo_efectivo"],
    } for item in tarjetas]
    con_saldo = [fila for fila in filas if fila["saldo"]]
    mas_cara = max(con_saldo, key=lambda fila: fila["tasa"], default=None)
    mejor_para_usar = min((fila for fila in filas if fila["disponible"] > 0),
                          key=lambda fila: (fila["tasa"], fila["utilizacion"]), default=None)
    return {
        "mes": mes, "tarjetas": filas,
        "mas_cara": mas_cara, "mejor_para_usar": mejor_para_usar,
        "lectura": ((f"{mas_cara['nombre']} es la más cara de arrastrar ({mas_cara['tasa']:.2f}% mensual sobre "
                     f"{_cop(mas_cara['saldo'])}). " if mas_cara else "Ninguna tarjeta tiene saldo pendiente. ")
                    + (f"Si tuvieran que usar una hoy, {mejor_para_usar['nombre']} es la de menor costo y menor "
                       f"utilización." if mejor_para_usar else "Ninguna tiene cupo disponible.")),
        "nota": "Cupo disponible no es dinero propio: es deuda que todavía no han tomado.",
    }


# ---------------------------------------------------------------------------
# Inteligencia de ahorro y metas
# ---------------------------------------------------------------------------

def inteligencia_ahorro(mes: str, perfil: dict[str, Any] | None = None) -> dict[str, Any]:
    """Estado de cajitas y metas con sus señales: atrasos, conflictos, fatiga."""
    perfil = perfil or perfil_financiero(mes)
    ahorro = perfil["ahorro"]
    flujo_libre = max(int(perfil["flujo"]["ahorro"]), 0)
    metas: list[dict[str, Any]] = []
    for meta in ahorro["metas"]:
        necesario = int(meta["ahorro_necesario_mensual"] or 0)
        promedio = meta.get("ahorro_promedio_mensual") or 0
        if meta["cumplida"]:
            estado = "cumplida"
        elif necesario and promedio >= necesario:
            estado = "en_ritmo"
        elif necesario and promedio < necesario:
            estado = "atrasada"
        elif not promedio:
            estado = "inactiva"
        else:
            estado = "sin_fecha"
        metas.append({
            "id": meta["id"], "nombre": meta["nombre"], "objetivo": meta["monto_objetivo"],
            "actual": meta["actual"], "faltante": meta["faltante"], "progreso": meta["progreso"],
            "fecha_objetivo": meta.get("fecha_objetivo"), "meses_restantes": meta.get("meses_restantes"),
            "necesario_mensual": necesario, "aporte_promedio": round(promedio),
            "prioridad": meta["prioridad"], "estado": estado,
            "brecha_mensual": max(necesario - round(promedio), 0),
        })
    requerido = sum(meta["necesario_mensual"] for meta in metas if meta["estado"] != "cumplida")
    conflicto = requerido > flujo_libre and requerido > 0
    retiros = [item for item in db.get_movimientos_ahorro(mes=mes) if item["tipo"] == "RETIRO"]
    señales: list[str] = []
    if conflicto:
        señales.append(f"Las metas piden {_cop(requerido)} al mes y el flujo deja {_cop(flujo_libre)}: "
                       "no todas van a llegar a tiempo con el ritmo actual.")
    if ahorro["fatiga"]["activa"]:
        señales.append("El ritmo de ahorro está dejando poco margen operativo; ahorrar no debería empujarlos "
                       "a usar la tarjeta para el día a día.")
    if retiros:
        señales.append(f"Hubo {len(retiros)} retiros por {_cop(sum(item['monto'] for item in retiros))} este mes.")
    inactivas = [meta["nombre"] for meta in metas if meta["estado"] == "inactiva"]
    if inactivas:
        señales.append("Sin aportes registrados: " + ", ".join(inactivas) + ".")
    cumplidas = [meta for meta in metas if meta["estado"] == "cumplida"]
    if cumplidas:
        señales.append("Ya cumplidas: " + ", ".join(meta["nombre"] for meta in cumplidas)
                       + ". Ese aporte queda libre para redirigir.")
    return {
        "mes": mes, "total_reservado": ahorro["total"], "fondos": ahorro["fondos"], "metas": metas,
        "emergencia": ahorro["emergencia"], "fatiga": ahorro["fatiga"],
        "aporte_neto_mes": ahorro["aporte_neto_mes"], "retiros_mes": ahorro["retiros_mes"],
        "aporte_requerido_total": requerido, "flujo_disponible": flujo_libre,
        "conflicto_de_metas": conflicto, "señales": señales,
        "metas_atrasadas": [meta["nombre"] for meta in metas if meta["estado"] == "atrasada"],
        "metas_en_ritmo": [meta["nombre"] for meta in metas if meta["estado"] == "en_ritmo"],
    }


# ---------------------------------------------------------------------------
# Motor de respuesta ampliado: intenciones, seguimiento y trazabilidad
# ---------------------------------------------------------------------------

# Reglas adicionales. Se concatenan a las originales para no perder ninguna
# formulación que ya funcionaba.
_REGLAS_EXTRA: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("what_should_we_do", ("que deberiamos hacer", "que hacemos", "que nos recomiendas", "que harias tu",
                           "por donde empezamos", "dame un plan", "cual es el plan", "que sigue",
                           "que deberia hacer"),
     ("recomiendas", "plan", "prioridad", "prioridades", "empezar")),
    ("why", ("porque dices", "por que dices", "de donde sale ese numero", "de donde sacaste",
             "en que te basas", "como llegaste a eso", "explicame ese numero",
             "cual es la evidencia", "por que dices eso", "por que ese numero"),
     ("evidencia", "justifica", "justificacion")),
    ("financial_risk", ("que riesgos", "cual es nuestro riesgo", "estamos en riesgo", "que tan riesgoso",
                        "que puede salir mal"),
     ("riesgo", "riesgos", "peligro", "expuestos")),
    ("financial_opportunity", ("que oportunidades", "en que podemos mejorar", "que podemos optimizar",
                               "donde podemos ganar", "que estamos desaprovechando"),
     ("oportunidad", "oportunidades", "optimizar", "mejorar", "aprovechar")),
    ("card_status", ("como estan las tarjetas", "estado de las tarjetas", "como va la tarjeta",
                     "cuanto debemos en tarjetas", "saldo de las tarjetas"),
     ("tarjetas", "saldo", "cupo", "utilizacion")),
    ("card_comparison", ("que tarjeta conviene", "cual tarjeta es mejor", "comparar tarjetas",
                         "cual tarjeta uso", "diferencia entre las tarjetas"),
     ("comparar tarjetas", "cual tarjeta")),
    ("debt_projection", ("cuando salimos de las tarjetas", "cuando terminamos de pagar",
                         "cuanto nos falta para salir", "en cuanto tiempo pagamos", "proyeccion de deuda"),
     ("proyeccion", "cuando terminamos", "plazo")),
    ("goal_status", ("como van las metas", "estado de las metas", "vamos a alcanzar la meta",
                     "como va la meta", "metas atrasadas"),
     ("meta", "metas", "objetivo", "objetivos")),
    ("budget", ("cuanto deberiamos presupuestar", "hacer un presupuesto", "presupuesto del mes",
                "cuanto asignamos a"),
     ("presupuesto", "presupuestar", "asignar")),
    ("personal_summary", ("como voy yo", "mi situacion", "mis gastos", "cuanto gaste yo",
                          "como va samuel", "como va sara", "resumen de samuel", "resumen de sara"),
     ("mio", "mis", "personal")),
    ("monthly_summary", ("resumen del mes", "como cerro el mes", "resumen mensual",
                         "como nos fue este mes"),
     ("resumen", "cierre")),
    ("income_change", ("bajo el ingreso", "subio el ingreso", "gane menos", "ganamos menos este mes",
                       "cambio el ingreso", "me subieron el sueldo"),
     ("ingreso", "ingresos", "sueldo", "salario")),
    ("savings_capacity", ("cuanto podemos ahorrar", "capacidad de ahorro", "cuanto deberiamos ahorrar",
                          "cuanto alcanzamos a ahorrar"),
     ("ahorrar", "capacidad")),
    ("category_analysis", ("cuanto gastamos en", "cuanto llevamos en", "gasto en mercado",
                           "gasto en comida", "por categoria"),
     ("categoria", "categorias")),
    ("spending_trend", ("estamos gastando mas", "venimos gastando mas", "tendencia de gasto",
                        "el gasto viene subiendo", "gastamos mas cada mes"),
     ("tendencia", "evolucion", "viene subiendo")),
    ("unusual_spending", ("gasto inusual", "algo fuera de lo normal", "gasto extrano",
                          "un gasto muy grande"),
     ("inusual", "extrano", "atipico")),
)

_REGLAS_INTENCION = _REGLAS_INTENCION + _REGLAS_EXTRA


def _seleccionar_mes_personal(texto: str) -> str | None:
    if "sara" in texto:
        return SARA
    if "samuel" in texto:
        return SAMUEL
    return None


def _r_plan(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    contexto = contexto_de(mes)
    plan = contexto.seccion("plan")
    lineas = [plan["resumen"]]
    etiquetas = (("inmediato", "Ya"), ("este_mes", "Este mes"),
                 ("proximos_tres_meses", "Próximos tres meses"), ("optimizacion_opcional", "Opcional"))
    for clave, etiqueta in etiquetas:
        for paso in plan[clave][:3]:
            monto = f" ({_cop(paso['monto'])})" if paso["monto"] else ""
            lineas.append(f"• [{etiqueta}]{monto} {paso['accion']} — {paso['por_que']}")
    return _respuesta("what_should_we_do", "\n".join(lineas),
                      {"plan": plan, "riesgo": contexto.riesgo, "hallazgos": contexto.hallazgos},
                      perfil["calidad_datos"]["level"],
                      ["¿Quieres que profundice en alguno de esos puntos?"])


def _r_riesgo(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    riesgo = contexto_de(mes).riesgo
    lineas = [riesgo["lectura"]]
    for factor in sorted((f for f in riesgo["factores"] if f["valor"] is not None),
                         key=lambda f: -(f["valor"] * f["peso"]))[:5]:
        lineas.append(f"• {factor['factor'].replace('_', ' ')}: {factor['lectura']}")
    if riesgo["sin_datos"]:
        lineas.append("Sin datos para evaluar: " + ", ".join(riesgo["sin_datos"]).replace("_", " ") + ".")
    lineas.append("No lo resumo en un solo puntaje a propósito: cada factor se arregla de forma distinta.")
    return _respuesta("financial_risk", "\n".join(lineas), riesgo, perfil["calidad_datos"]["level"],
                      ["¿Quieres el plan de acción para bajar el factor que más pesa?"])


def _r_oportunidades(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    lista = contexto_de(mes).oportunidades
    if not lista:
        return _respuesta("financial_opportunity",
                          "Con los datos registrados no veo oportunidades claras que no haya mencionado ya.",
                          {"oportunidades": []}, perfil["calidad_datos"]["level"], [])
    lineas = ["Esto es lo que veo aprovechable hoy:"]
    for item in lista[:4]:
        lineas.append(f"• {item['titulo']}: {item['detalle']} {item['accion']} ({item['beneficio']})")
    return _respuesta("financial_opportunity", "\n".join(lineas), {"oportunidades": lista},
                      perfil["calidad_datos"]["level"], ["¿Quieres que simule alguna de esas decisiones?"])


def _r_estado_tarjetas(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    datos = contexto_de(mes).seccion("tarjetas_iq")
    if not datos["tarjetas"]:
        return _respuesta("card_status", "No hay tarjetas registradas.", datos, "Alta", [])
    lineas = [f"En total deben {_cop(datos['deuda_total'])} en tarjetas, {_pct(datos['utilizacion_global'])} "
              f"del cupo, con mínimos por {_cop(datos['pago_minimo_total'])} este mes."]
    for tarjeta in datos["tarjetas"]:
        lineas.append(f"• {tarjeta['nombre']} ({tarjeta['titular']}): {_cop(tarjeta['saldo'])} de "
                      f"{_cop(tarjeta['cupo'])}, {_pct(tarjeta['utilizacion'])} de uso"
                      + (f", tasa {tarjeta['interes_mensual']:.2f}%." if tarjeta["interes_mensual"] else
                         ", sin tasa registrada."))
        for señal in tarjeta["señales"][:2]:
            lineas.append(f"    - {señal['detalle']}")
    return _respuesta("card_status", "\n".join(lineas), datos, perfil["calidad_datos"]["level"],
                      ["¿Quieres saber cuánto abonarle a cuál?"])


def _r_comparar_tarjetas(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    datos = comparar_tarjetas(mes, perfil)
    lineas = [datos.get("lectura", datos.get("mensaje", ""))]
    for fila in datos.get("tarjetas", []):
        lineas.append(f"• {fila['nombre']} ({fila['titular']}): saldo {_cop(fila['saldo'])}, disponible "
                      f"{_cop(fila['disponible'])}, uso {_pct(fila['utilizacion'])}"
                      + (f", tasa {fila['tasa']:.2f}%." if fila["tasa"] else ", sin tasa registrada."))
    lineas.append(datos.get("nota", ""))
    return _respuesta("card_comparison", "\n".join(linea for linea in lineas if linea), datos,
                      perfil["calidad_datos"]["level"], [])


def _r_proyeccion_deuda(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    deuda = perfil["deuda"]
    if not deuda["tarjetas"]:
        return _respuesta("debt_projection", "No hay deuda de tarjeta activa para proyectar.", deuda, "Alta", [])
    presupuesto = ctx_dict.get("monto") or deuda["presupuesto_proyectado"]
    try:
        proyeccion = engine.unified_debt_payoff(int(presupuesto), "avalancha", start_month=mes)
    except ValueError as error:
        return _respuesta("debt_projection", f"No puedo proyectar eso: {error}", {}, "Baja", [])
    if not proyeccion.get("viable"):
        return _respuesta("debt_projection",
                          (f"Con {_cop(presupuesto)} al mes no alcanza para cubrir los mínimos "
                           f"({_cop(proyeccion['minimums'])}), así que no sería responsable darles una fecha."),
                          proyeccion, "Baja", ["¿Con cuánto mensual quieren que lo calcule?"])
    texto = (f"Con {_cop(presupuesto)} al mes y estrategia avalancha, la proyección termina en "
             f"{_meses(proyeccion['months'])} ({proyeccion['debt_free_date']}), pagando cerca de "
             f"{_cop(proyeccion['total_interest'])} en intereses sobre una deuda inicial de "
             f"{_cop(proyeccion['initial_debt'])}. No es una fecha garantizada: asume tasa constante y "
             "ninguna compra nueva.")
    return _respuesta("debt_projection", texto, proyeccion, proyeccion.get("confidence", "Media"),
                      ["¿Quieres ver cómo cambia si abonan más?"])


def _r_metas(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    datos = contexto_de(mes).seccion("ahorro_iq")
    if not datos["metas"]:
        return _respuesta("goal_status", "No hay metas registradas todavía.", datos, "Alta", [])
    lineas = ["Así van las metas:"]
    for meta in datos["metas"]:
        detalle = (f"• {meta['nombre']}: {_cop(meta['actual'])} de {_cop(meta['objetivo'])} "
                   f"({_pct(meta['progreso'])}), estado {meta['estado'].replace('_', ' ')}")
        if meta["necesario_mensual"]:
            detalle += (f". Necesita {_cop(meta['necesario_mensual'])} al mes y vienen aportando "
                        f"{_cop(meta['aporte_promedio'])}.")
        else:
            detalle += "."
        lineas.append(detalle)
    lineas.extend(datos["señales"])
    return _respuesta("goal_status", "\n".join(lineas), datos, perfil["calidad_datos"]["level"],
                      ["¿Quieres que priorice una meta sobre las otras?"])


def _r_presupuesto(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    capacidad = contexto_de(mes).capacidad
    gastos = perfil["gastos"]
    lineas = [f"Partiendo de {_cop(capacidad['base_disponible'])} disponibles, los compromisos ya reservados son:"]
    for clave, valor in capacidad["compromisos"].items():
        if valor:
            lineas.append(f"• {clave.replace('_', ' ')}: {_cop(valor)}")
    lineas.append(f"Queda un margen discrecional de {_cop(capacidad['maximo_discrecional'])}.")
    if gastos["por_categoria"]:
        referencia = gastos["por_categoria"][:3]
        lineas.append("Como referencia, este mes gastaron: "
                      + ", ".join(f"{item['categoria']} {_cop(item['monto'])}" for item in referencia) + ".")
    lineas.append("No fijo presupuestos por ustedes: con estos números pueden repartirlo como prefieran.")
    return _respuesta("budget", "\n".join(lineas), {"capacidad": capacidad, "gastos": gastos},
                      perfil["calidad_datos"]["level"], ["¿Quieren que revise una categoría en concreto?"])


def _r_resumen_personal(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    persona = ctx_dict.get("persona") or _seleccionar_mes_personal(ctx_dict["texto"])
    if persona is None:
        return _respuesta("personal_summary",
                          "¿De quién quieres el resumen, de Samuel o de Sara? Los leo por responsabilidad "
                          "económica, no por quién puso la tarjeta.",
                          {}, "Baja", ["¿Samuel o Sara?"])
    tablero = calc.dashboard_personal(persona, mes)
    nombre = _nombre(persona)
    texto = (f"{nombre} este mes: ingresos {_cop(tablero['ingresos'])}, consumo asumido "
             f"{_cop(tablero['gastos'])}, liquidez aportada {_cop(tablero['liquidez'])} y deuda a su "
             f"responsabilidad {_cop(tablero['deuda'])}. La capacidad de ahorro estimada es "
             f"{_cop(tablero['capacidad_ahorro'])}.")
    pareja = contexto_de(mes).pareja
    if pareja["saldo_pendiente"]:
        texto += (f" En el acumulado, {pareja['deudor']} le debe {_cop(pareja['saldo_pendiente'])} a "
                  f"{pareja['acreedor']}.")
    return _respuesta("personal_summary", texto, {"tablero": tablero, "pareja": pareja},
                      perfil["calidad_datos"]["level"], [])


def _r_resumen_mes(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    contexto = contexto_de(mes)
    comparacion = contexto.comparacion
    flujo = perfil["flujo"]
    lineas = [f"Cierre de {mes}: entraron {_cop(flujo['ingresos'])}, salieron {_cop(flujo['salidas'])} y "
              f"el mes deja {_cop(int(flujo['ahorro']))}."]
    if comparacion["suficiente_historial"]:
        lineas.append(f"Frente a {comparacion['mes_comparado']}: gastos "
                      f"{_cop(comparacion['gastos']['diferencia'])} y flujo "
                      f"{_cop(comparacion['flujo']['diferencia'])} de diferencia.")
    for hallazgo in contexto.hallazgos[:2]:
        lineas.append(f"• {hallazgo['titulo']}: {hallazgo['que']}")
    return _respuesta("monthly_summary", "\n".join(lineas),
                      {"flujo": flujo, "comparacion": comparacion, "hallazgos": contexto.hallazgos},
                      perfil["calidad_datos"]["level"], ["¿Quieres el detalle por categoría?"])


def _r_cambio_ingreso(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    ingresos = perfil["ingresos"]
    monto = ctx_dict.get("monto")
    if monto:
        return _r_escenario(mes, {**ctx_dict, "texto": ctx_dict["texto"] + " gano menos"}, perfil)
    if ingresos["variacion_vs_anterior"] is None:
        texto = (f"Este mes hay {_cop(ingresos['total'])} de ingreso registrado y no tengo mes anterior "
                 "completo para comparar.")
    else:
        direccion = "subió" if ingresos["variacion_vs_anterior"] > 0 else "bajó"
        texto = (f"El ingreso {direccion} {_pct(abs(ingresos['variacion_vs_anterior']))} frente al mes anterior: "
                 f"{_cop(ingresos['total'])} contra {_cop(ingresos['total_mes_anterior'])}. "
                 f"Samuel aportó {_cop(ingresos['samuel'])} y Sara {_cop(ingresos['sara'])}. "
                 f"El comportamiento reciente del ingreso se ve {ingresos['estabilidad']}.")
    referencia = ingresos["referencia"].get("referencia_conservadora")
    if referencia:
        texto += f" Para proyectar uso {_cop(referencia)}, que es el menor ingreso reciente confirmado."
    return _respuesta("income_change", texto, ingresos, perfil["calidad_datos"]["level"],
                      ["¿Quieres que simule un mes con menos ingreso? Dime cuánto menos."])


def _r_categoria(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    texto_pregunta = ctx_dict["texto"]
    categorias = perfil["gastos"]["por_categoria"]
    objetivo = next((item for item in categorias
                     if normalizar_texto(item["categoria"]) in texto_pregunta), None)
    comparacion = contexto_de(mes).comparacion
    if objetivo is None:
        if not categorias:
            return _respuesta("category_analysis", f"No hay gastos registrados en {mes}.", {}, "Baja", [])
        lineas = ["Así se reparte el gasto del mes:"]
        for item in categorias[:6]:
            lineas.append(f"• {item['categoria']}: {_cop(item['monto'])} ({_pct(item['participacion'])})")
        return _respuesta("category_analysis", "\n".join(lineas), {"categorias": categorias},
                          perfil["calidad_datos"]["level"], ["¿Quieres el detalle de alguna?"])
    delta = next((item for item in comparacion["categorias"]
                  if item["categoria"] == objetivo["categoria"]), None)
    texto = (f"En {objetivo['categoria']} llevan {_cop(objetivo['monto'])} este mes, "
             f"{_pct(objetivo['participacion'])} del gasto total.")
    if delta and delta["anterior"]:
        direccion = "más" if delta["diferencia"] > 0 else "menos"
        texto += (f" Es {_cop(abs(delta['diferencia']))} {direccion} que en {comparacion['mes_comparado']}"
                  + (f" ({_pct(delta['variacion'])})." if delta["variacion"] else "."))
    movimientos = [gasto for gasto in db.get_gastos(mes)
                   if gasto["categoria"] == objetivo["categoria"]]
    if movimientos:
        mayor = max(movimientos, key=lambda gasto: gasto["valor"])
        texto += f" El movimiento más grande fue «{mayor['nombre']}» por {_cop(mayor['valor'])}."
    return _respuesta("category_analysis", texto,
                      {"categoria": objetivo, "comparacion": delta, "movimientos": len(movimientos)},
                      perfil["calidad_datos"]["level"], [])


def _r_tendencia(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    datos = contexto_de(mes).tendencias
    if datos["meses_con_datos"] < 3:
        return _respuesta("spending_trend",
                          "Necesito al menos tres meses registrados para hablar de tendencia y todavía no los hay.",
                          datos, "Baja", [])
    lineas = [f"El gasto viene {datos['gastos']['direccion']}, el ingreso {datos['ingresos']['direccion']} "
              f"y el flujo {datos['flujo']['direccion']}."]
    serie = datos["gastos"]["serie"]
    if len(serie) >= 3:
        lineas.append("Últimos meses de gasto: " + " → ".join(_cop(valor) for valor in serie[-4:]) + ".")
    for item in datos["categorias_en_alza"][:3]:
        lineas.append(f"• {item['categoria']} lleva tres meses subiendo "
                      + (f"({_pct(item['crecimiento'])} en total)." if item["crecimiento"] else "."))
    if not datos["categorias_en_alza"]:
        lineas.append("Ninguna categoría concreta explica el movimiento: está repartido.")
    return _respuesta("spending_trend", "\n".join(lineas), datos, perfil["calidad_datos"]["level"],
                      ["¿Quieres comparar mes contra mes en detalle?"])


def _r_explicacion_estructurada(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    estructura = explicacion_estructurada(mes)
    texto = "\n".join(f"{bloque['orden']}. {bloque['titulo']}: {bloque['texto']}"
                      for bloque in estructura["bloques"])
    return _respuesta("financial_explanation", texto, estructura, estructura["confianza"],
                      ["¿Quieres que profundice en alguno de esos puntos?"])


def _r_por_que(mes: str, ctx_dict: dict[str, Any], perfil: dict[str, Any]) -> dict[str, Any]:
    """Explica de dónde salió la última respuesta usando su propia evidencia."""
    sesion: ctx.SesionAsesor | None = ctx_dict.get("sesion")
    # El turno actual todavía no se ha registrado, así que el último de la
    # sesión es exactamente la respuesta sobre la que se está preguntando.
    anterior = sesion.ultimo if sesion and sesion.turnos else None
    if anterior is not None and anterior.intencion == "why" and len(sesion.turnos) >= 2:
        anterior = list(sesion.turnos)[-2]
    if anterior is None:
        contexto = contexto_de(mes)
        lineas = ["Necesito saber a qué número te refieres. Mientras tanto, así se arma el margen que uso "
                  "para casi todo:"]
        capacidad = contexto.capacidad
        lineas.append(f"• Disponible registrado: {_cop(capacidad['base_disponible'])}")
        for clave, valor in capacidad["compromisos"].items():
            if valor:
                lineas.append(f"• Menos {clave.replace('_', ' ')}: {_cop(valor)}")
        lineas.append(f"• Margen discrecional: {_cop(capacidad['maximo_discrecional'])}")
        return _respuesta("why", "\n".join(lineas), capacidad, perfil["calidad_datos"]["level"],
                          ["¿De qué respuesta quieres el detalle?"])
    datos = anterior.datos or {}
    lineas = [f"Te respondí eso a partir de «{anterior.pregunta}». De dónde sale:"]
    evidencia = _extraer_evidencia(datos)
    if evidencia:
        lineas.extend(f"• {linea}" for linea in evidencia[:6])
    else:
        lineas.append("• No guardé evidencia estructurada de esa respuesta; vuelve a hacerme la pregunta "
                      "y te muestro los números uno por uno.")
    return _respuesta("why", "\n".join(lineas),
                      {"intencion_anterior": anterior.intencion, "datos": datos},
                      perfil["calidad_datos"]["level"], [])


def _extraer_evidencia(datos: dict[str, Any]) -> list[str]:
    """Convierte la estructura de una respuesta previa en líneas de evidencia."""
    lineas: list[str] = []
    if not isinstance(datos, dict):
        return lineas
    if "evidencia" in datos and isinstance(datos["evidencia"], list):
        lineas.extend(str(item) for item in datos["evidencia"][:5])
    for clave in ("base_disponible", "maximo_discrecional", "monto", "flujo_del_mes"):
        if isinstance(datos.get(clave), (int, float)):
            lineas.append(f"{clave.replace('_', ' ')}: {_cop(datos[clave])}")
    compromisos = datos.get("compromisos")
    if isinstance(compromisos, dict):
        for clave, valor in compromisos.items():
            if valor:
                lineas.append(f"compromiso {clave.replace('_', ' ')}: {_cop(valor)}")
    if isinstance(datos.get("antes"), dict) and isinstance(datos.get("despues"), dict):
        for clave in ("liquidez", "deuda_tarjetas", "flujo_mensual"):
            if clave in datos["antes"]:
                lineas.append(f"{clave.replace('_', ' ')}: {_cop(datos['antes'][clave])} → "
                              f"{_cop(datos['despues'][clave])}")
    hallazgos = datos.get("hallazgos")
    if isinstance(hallazgos, list):
        for hallazgo in hallazgos[:3]:
            if isinstance(hallazgo, dict) and hallazgo.get("razon_severidad"):
                lineas.append(f"{hallazgo['titulo']}: {hallazgo['razon_severidad']}")
    for opcion in (datos.get("opciones") or [])[:3]:
        if isinstance(opcion, dict):
            lineas.append(f"{opcion.get('opcion')}: {opcion.get('impacto')}")
    return lineas


# Tabla única de enrutamiento intención → manejador. Los nombres originales se
# conservan y se añaden los nuevos; varios son alias del mismo manejador porque
# la pregunta cambia de forma pero no de respuesta. ``financial_explanation``
# usa la versión estructurada en nueve bloques (``_r_explicacion`` quedó sin uso).
_RUTAS: dict[str, Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "current_state": _r_estado_actual,
    "affordability": _r_asequibilidad,
    "purchase_evaluation": _r_compra,
    "card_payment": _r_pago_tarjeta,
    "card_deferral": _r_diferir_tarjeta,
    "debt_strategy": _r_estrategia_deuda,
    "savings": _r_ahorro,
    "fixed_expense": _r_gasto_fijo,
    "recurring_expense": _r_recurrentes,
    "spending_analysis": _r_analisis_gastos,
    "couple_analysis": _r_pareja,
    "monthly_comparison": _r_comparacion,
    "anomaly": _r_anomalias,
    "scenario": _r_escenario,
    "travel_comparison": _r_viaje,
    "financial_explanation": _r_explicacion_estructurada,
    "data_integrity": _r_integridad,
    "unknown": _r_desconocida,
    "what_should_we_do": _r_plan,
    "recommendation": _r_plan,
    "why": _r_por_que,
    "financial_risk": _r_riesgo,
    "financial_opportunity": _r_oportunidades,
    "card_status": _r_estado_tarjetas,
    "card_comparison": _r_comparar_tarjetas,
    "payment_method": _r_pago_tarjeta,
    "debt_projection": _r_proyeccion_deuda,
    "debt_status": _r_estrategia_deuda,
    "goal_status": _r_metas,
    "savings_status": _r_ahorro,
    "savings_capacity": _r_ahorro,
    "budget": _r_presupuesto,
    "personal_summary": _r_resumen_personal,
    "couple_summary": _r_pareja,
    "monthly_summary": _r_resumen_mes,
    "income_change": _r_cambio_ingreso,
    "category_analysis": _r_categoria,
    "expense_analysis": _r_analisis_gastos,
    "spending_trend": _r_tendencia,
    "unusual_spending": _r_anomalias,
    "fixed_expense_detection": _r_recurrentes,
    "fixed_expense_candidate": _r_gasto_fijo,
    "current_financial_state": _r_estado_actual,
    "outing": _r_asequibilidad,
    "purchase": _r_compra,
    "travel": _r_viaje,
    "destination_comparison": _r_viaje,
    "explanation": _r_explicacion_estructurada,
    "integrity": _r_integridad,
}

INTENCIONES = tuple(sorted(_RUTAS))

# Preguntas de seguimiento que solo tienen sentido con el turno anterior.
_MARCAS_SEGUIMIENTO = ("y si", "y a ", "y en ", "y con ", "y para ", "y entonces", "y eso",
                       "que tal si", "y cuanto", "y que pasa")


def _es_seguimiento(texto: str) -> bool:
    return texto.startswith(("y ", "entonces", "ok y", "pero y")) or any(
        marca in texto[:14] for marca in _MARCAS_SEGUIMIENTO)


def _resolver_seguimiento(contexto_pregunta: dict[str, Any],
                          sesion: ctx.SesionAsesor | None) -> dict[str, Any]:
    """Completa una pregunta de seguimiento con lo dicho en turnos anteriores.

    Solo rellena lo que falta: si la pregunta nueva trae monto o destino
    propios, esos mandan. Nunca inventa una intención que no se pueda sostener
    con el turno previo.
    """
    if sesion is None or not sesion.turnos:
        return contexto_pregunta
    texto = contexto_pregunta["texto"]
    ultimo = sesion.ultimo
    seguimiento = (_es_seguimiento(texto) or contexto_pregunta["intencion"] == "unknown"
                   or bool(re.search(r"\b\w+(?:arlo|erlo|irlo|arla|erla)\b", texto)))
    if not seguimiento:
        return contexto_pregunta
    heredado = dict(contexto_pregunta)
    heredado["heredado_de"] = ultimo.intencion if ultimo else None
    if not heredado.get("destinos") and ultimo and ultimo.entidades.get("destinos"):
        heredado["destinos"] = ultimo.entidades["destinos"]
    if heredado.get("destinos") and heredado["intencion"] in ("unknown", "scenario"):
        heredado["intencion"] = "travel_comparison"
        return heredado
    if heredado.get("monto") and heredado["intencion"] == "unknown" and ultimo:
        # «¿y 300k?» hereda la pregunta anterior con el monto nuevo.
        heredado["intencion"] = ultimo.intencion
        return heredado
    if heredado["intencion"] == "unknown" and ultimo:
        heredado["intencion"] = ultimo.intencion
        heredado.setdefault("monto", ultimo.monto)
        if heredado.get("monto") is None:
            heredado["monto"] = ultimo.monto
    if heredado.get("monto") is None and ultimo and ultimo.monto and texto.startswith("y "):
        heredado["monto"] = ultimo.monto
    # «¿cómo lo pagamos?», «¿y eso?»: el pronombre se refiere al monto anterior.
    pronombres = ("lo", "eso", "esto", "la compra", "ese gasto", "esa compra")
    if (heredado.get("monto") is None and ultimo and ultimo.monto
            and heredado["intencion"] in ("card_payment", "payment_method", "purchase_evaluation",
                                          "affordability", "scenario")
            and any(re.search(rf"\b{re.escape(p)}\b", texto) or texto.endswith(p) for p in pronombres)):
        heredado["monto"] = ultimo.monto
        heredado["monto_heredado"] = True
    return heredado


def responder(mes: str, pregunta: str, *, monto: int | None = None,
              perfil: dict[str, Any] | None = None,
              sesion: ctx.SesionAsesor | None = None) -> dict[str, Any]:
    """Punto de entrada conversacional: clasifica, hereda contexto y responde.

    ``sesion`` es opcional. Cuando se entrega, la respuesta puede resolver
    referencias como «¿y a España?» o «¿y si son 300k?», y habilita la pregunta
    «¿por qué?» sobre la respuesta anterior.
    """
    mes = validar_mes(mes)
    contexto_pregunta = clasificar_intencion(pregunta)
    if monto is not None:
        contexto_pregunta["monto"] = int(monto)
    contexto_pregunta = _resolver_seguimiento(contexto_pregunta, sesion)
    contexto_pregunta["sesion"] = sesion
    objetivo = contexto_pregunta.get("mes") or mes
    if perfil is None:
        perfil = contexto_de(objetivo).perfil
    manejador = _RUTAS.get(contexto_pregunta["intencion"], _r_desconocida)
    try:
        resultado = manejador(objetivo, contexto_pregunta, perfil)
    except ValueError as error:
        resultado = _respuesta(contexto_pregunta["intencion"],
                               f"No puedo responder eso todavía: {error}", {}, "Baja", [])
    except KeyError as error:  # sección o dato ausente: se informa, no se inventa
        resultado = _respuesta(contexto_pregunta["intencion"],
                               f"Me falta un dato para responder eso ({error}). Revisa que el mes tenga "
                               "movimientos registrados.", {}, "Baja", [])
    resultado["pregunta"] = pregunta
    resultado["mes"] = objetivo
    resultado["contexto"] = {clave: contexto_pregunta[clave]
                             for clave in ("intencion", "puntaje", "monto", "persona", "destinos",
                                           "heredado_de")
                             if clave in contexto_pregunta}
    if sesion is not None:
        sesion.registrar(ctx.TurnoAsesor(
            pregunta=pregunta, intencion=resultado["intencion"],
            monto=contexto_pregunta.get("monto"),
            entidades={"destinos": contexto_pregunta.get("destinos"),
                       "persona": contexto_pregunta.get("persona")},
            respuesta=resultado["respuesta"], datos=resultado.get("datos") or {}))
    return resultado


def nueva_sesion(mes: str) -> ctx.SesionAsesor:
    """Crea una sesión conversacional en memoria para un flujo de preguntas."""
    return ctx.SesionAsesor(mes=validar_mes(mes))
