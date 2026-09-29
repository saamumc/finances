"""Motor de diagnóstico de LÚMINA: hallazgos, riesgo, oportunidades y plan.

Recibe secciones ya calculadas del contexto financiero (no consulta la base) y
produce estructuras que la interfaz puede pintar tal cual: cada hallazgo trae
identificador, severidad razonada, evidencia, impacto, acción y confianza.

Tres decisiones de diseño que vale la pena conocer antes de tocar este archivo:

* La severidad nunca se asigna a dedo: sale de ``_severidad_por_reglas`` y
  siempre viaja con ``razon_severidad``.
* Los hallazgos que obligan a la misma decisión se agrupan (presión de tarjeta,
  por ejemplo) en vez de mostrarse como cinco avisos distintos.
* El orden final combina severidad, impacto, urgencia, confianza,
  reversibilidad y control del usuario. Una suscripción de $20.000 no puede
  aparecer sobre una deuda de $1.500.000.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Sequence

# Severidades ordenadas de menor a mayor.
INFO = "info"
BAJA = "baja"
MEDIA = "media"
ALTA = "alta"
CRITICA = "critica"
POSITIVA = "positiva"

ORDEN_SEVERIDAD: dict[str, int] = {CRITICA: 0, ALTA: 1, MEDIA: 2, BAJA: 3, INFO: 4, POSITIVA: 5}
PESO_SEVERIDAD: dict[str, float] = {CRITICA: 1.0, ALTA: 0.78, MEDIA: 0.5, BAJA: 0.28, INFO: 0.12, POSITIVA: 0.05}

# Compatibilidad con la UI actual, que sigue leyendo la clave ``prioridad``.
_ETIQUETA_UI: dict[str, str] = {CRITICA: "critica", ALTA: "alta", MEDIA: "media",
                                BAJA: "baja", INFO: "info", POSITIVA: "positiva"}

CONFIANZA_TEXTO: dict[str, float] = {"Alta": 0.9, "Media": 0.6, "Baja": 0.3}


def fmt_cop(valor: Any) -> str:
    """Formatea pesos colombianos tolerando negativos y valores no enteros."""
    try:
        monto = int(round(float(valor)))
    except (TypeError, ValueError):
        return "$0"
    signo = "-" if monto < 0 else ""
    return f"{signo}${abs(monto):,}".replace(",", ".")


def fmt_pct(valor: Any, decimales: int = 0) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "0%"
    return f"{numero * 100:.{decimales}f}%"


def fmt_meses(cantidad: Any) -> str:
    try:
        numero = int(cantidad)
    except (TypeError, ValueError):
        return "un plazo no calculable"
    return "1 mes" if numero == 1 else f"{numero} meses"


@dataclass
class Hallazgo:
    """Un diagnóstico explicable y accionable.

    ``que`` describe el hecho, ``por_que`` la causa observada en los datos y
    ``accion`` lo único que el usuario tiene que decidir. ``evidencia`` guarda
    las cifras crudas para que la interfaz pueda mostrar el rastro completo.
    """

    id: str
    categoria: str
    severidad: str
    titulo: str
    que: str
    por_que: str
    accion: str
    impacto: str
    evidencia: dict[str, Any] = field(default_factory=dict)
    confianza: str = "Media"
    razon_severidad: str = ""
    entidades_relacionadas: list[dict[str, Any]] = field(default_factory=list)
    montos_relacionados: dict[str, int] = field(default_factory=dict)
    monto_involucrado: int = 0
    urgencia: float = 0.5          # 0 = puede esperar, 1 = decide este mes
    reversibilidad: float = 0.5    # 1 = fácil de deshacer
    control_usuario: float = 0.7   # cuánto depende de una decisión propia
    grupo: str | None = None
    prioridad_calculada: float = 0.0

    def a_dict(self) -> dict[str, Any]:
        datos = asdict(self)
        # La interfaz existente lee ``prioridad``; se conserva el nombre.
        datos["prioridad"] = _ETIQUETA_UI.get(self.severidad, self.severidad)
        return datos


def _severidad_por_reglas(*, impacto_relativo: float, es_integridad: bool = False,
                          compromete_obligaciones: bool = False, tendencia_negativa: bool = False,
                          solo_confirmacion: bool = False) -> tuple[str, str]:
    """Deriva severidad de condiciones observables y devuelve su justificación.

    ``impacto_relativo`` es el peso del hallazgo frente al ingreso mensual.
    """
    if es_integridad:
        return CRITICA, ("Afecta la reconstrucción histórica del libro: cualquier liquidación entre ustedes "
                         "partiría de una base que no cuadra.")
    if compromete_obligaciones:
        return CRITICA, "Compromete obligaciones ya adquiridas (mínimos, gastos fijos o cobertura básica)."
    if solo_confirmacion:
        return BAJA, "Solo requiere que confirmen un dato; no cambia decisiones de este mes por sí solo."
    if impacto_relativo >= 0.30:
        return ALTA, f"El monto involucrado equivale a {fmt_pct(impacto_relativo)} del ingreso mensual registrado."
    if impacto_relativo >= 0.10 or tendencia_negativa:
        return MEDIA, ("Impacto intermedio frente al ingreso"
                       + (" y con una tendencia que empeora si no se corrige." if tendencia_negativa else "."))
    if impacto_relativo > 0:
        return BAJA, "Impacto bajo frente al ingreso mensual registrado."
    return INFO, "Es información de contexto, no un problema por sí mismo."


def _impacto_relativo(monto: int, ingreso: int) -> float:
    if not ingreso:
        return 0.0
    return abs(monto) / ingreso


def _escala_monto(monto: int) -> float:
    """Escala logarítmica: distingue $20.000 de $1.500.000 sin que un solo
    hallazgo enorme aplaste a todos los demás."""
    if monto <= 0:
        return 0.0
    return min(math.log10(monto) / 7.0, 1.0)


# ---------------------------------------------------------------------------
# Modelo de riesgo multifactor
# ---------------------------------------------------------------------------

def _factor(nombre: str, valor: float | None, peso: float, lectura: str,
            evidencia: dict[str, Any]) -> dict[str, Any]:
    return {"factor": nombre, "valor": None if valor is None else round(valor, 3),
            "peso": peso, "lectura": lectura, "evidencia": evidencia}


def modelo_riesgo(perfil: dict[str, Any], tendencias: dict[str, Any] | None = None) -> dict[str, Any]:
    """Riesgo financiero como conjunto de factores, nunca como un solo número.

    Cada factor se normaliza a 0-1 (0 = sin presión, 1 = presión máxima) y se
    reporta por separado. El índice combinado existe solo para ordenar
    hallazgos internamente; la lectura que se le muestra al usuario es la lista
    de factores, no el índice.
    """
    ingresos = perfil["ingresos"]["total"]
    gastos = perfil["gastos"]
    tarjetas = perfil["tarjetas"]
    deuda = perfil["deuda"]
    ahorro = perfil["ahorro"]
    flujo = perfil["flujo"]

    factores: list[dict[str, Any]] = []

    carga_deuda = min((deuda["carga_sobre_ingreso"] or 0.0) / 0.60, 1.0)
    factores.append(_factor("carga_de_deuda", carga_deuda, 0.20,
                            f"La deuda total equivale a {fmt_pct(deuda['carga_sobre_ingreso'] or 0)} del ingreso.",
                            {"deuda_total": deuda["total"], "ingreso": ingresos}))

    utilizacion = min((tarjetas["utilizacion_global"] or 0.0) / 0.90, 1.0)
    factores.append(_factor("utilizacion_tarjetas", utilizacion, 0.18,
                            f"Utilización global de {fmt_pct(tarjetas['utilizacion_global'] or 0)}.",
                            {"deuda": tarjetas["deuda_total"], "cupo": tarjetas["cupo_total"]}))

    minimos = min((deuda["minimos_sobre_ingreso"] or 0.0) / 0.20, 1.0)
    factores.append(_factor("pagos_minimos", minimos, 0.12,
                            f"Los mínimos de tarjeta son {fmt_cop(tarjetas['pago_minimo_total'])} al mes.",
                            {"minimos": tarjetas["pago_minimo_total"], "ingreso": ingresos}))

    carga_fija = gastos["carga_fija"]
    valor_fija = min((carga_fija or 0.0) / 0.60, 1.0) if carga_fija is not None else None
    factores.append(_factor("carga_gastos_fijos", valor_fija, 0.15,
                            (f"Los gastos fijos son {fmt_pct(carga_fija)} del ingreso."
                             if carga_fija is not None else "No hay ingreso registrado para medir la carga fija."),
                            {"fijos": gastos["gastos_fijos_registrados"]["total_mensual_equivalente"]}))

    obligatorio = max(gastos["obligatorios"], 1)
    cobertura = flujo["disponible"] / obligatorio
    colchon = min(max(1.0 - cobertura / 3.0, 0.0), 1.0)
    factores.append(_factor("colchon_liquidez", colchon, 0.15,
                            f"La caja disponible cubre {cobertura:.1f} meses de gasto obligatorio.",
                            {"disponible": flujo["disponible"], "obligatorio": gastos["obligatorios"]}))

    emergencia = ahorro["emergencia"]
    reserva = min(max(1.0 - (emergencia["progress"] or 0.0), 0.0), 1.0)
    factores.append(_factor("reserva_emergencia", reserva, 0.10,
                            f"El fondo de emergencia va en {fmt_pct(emergencia['progress'] or 0)} de su referencia.",
                            {"actual": emergencia["current"], "objetivo": emergencia["target"]}))

    variacion_ingreso = perfil["ingresos"]["variacion_vs_anterior"]
    if variacion_ingreso is None:
        ingreso_factor = None
        lectura_ingreso = "No hay mes anterior registrado para comparar el ingreso."
    else:
        ingreso_factor = min(max(-variacion_ingreso, 0.0) / 0.30, 1.0)
        lectura_ingreso = f"El ingreso varió {fmt_pct(variacion_ingreso)} frente al mes anterior."
    factores.append(_factor("estabilidad_ingreso", ingreso_factor, 0.10, lectura_ingreso,
                            {"actual": perfil["ingresos"]["total"],
                             "anterior": perfil["ingresos"]["total_mes_anterior"]}))

    if tendencias and tendencias.get("gastos", {}).get("direccion") == "subiendo":
        tendencia_factor, lectura_tendencia = 0.7, "El gasto viene subiendo tres meses seguidos."
    elif tendencias and tendencias.get("gastos", {}).get("direccion") == "bajando":
        tendencia_factor, lectura_tendencia = 0.1, "El gasto viene bajando."
    else:
        tendencia_factor, lectura_tendencia = 0.3, "El gasto no muestra una tendencia sostenida."
    factores.append(_factor("tendencia_gasto", tendencia_factor, 0.10, lectura_tendencia,
                            {"serie": (tendencias or {}).get("gastos", {}).get("serie", [])}))

    considerados = [factor for factor in factores if factor["valor"] is not None]
    peso_total = sum(factor["peso"] for factor in considerados) or 1.0
    indice = sum(factor["valor"] * factor["peso"] for factor in considerados) / peso_total
    if indice >= 0.66:
        nivel = "alto"
    elif indice >= 0.38:
        nivel = "moderado"
    else:
        nivel = "contenido"
    dominantes = sorted(considerados, key=lambda factor: -(factor["valor"] * factor["peso"]))[:3]
    return {
        "nivel": nivel, "indice": round(indice, 3), "factores": factores,
        "factores_dominantes": [factor["factor"] for factor in dominantes],
        "sin_datos": [factor["factor"] for factor in factores if factor["valor"] is None],
        "lectura": (f"Riesgo {nivel}. Lo que más pesa hoy: "
                    + ", ".join(factor["lectura"] for factor in dominantes)),
        "nota": "El índice existe para ordenar hallazgos; la lectura útil son los factores por separado.",
    }


# ---------------------------------------------------------------------------
# Oportunidades
# ---------------------------------------------------------------------------

def _oportunidad(identificador: str, titulo: str, detalle: str, accion: str,
                 beneficio: str, evidencia: dict[str, Any], monto: int = 0,
                 confianza: str = "Media") -> dict[str, Any]:
    return {"id": identificador, "titulo": titulo, "detalle": detalle, "accion": accion,
            "beneficio": beneficio, "evidencia": evidencia, "monto_estimado": monto,
            "confianza": confianza, "tipo": "oportunidad"}


def detectar_oportunidades(perfil: dict[str, Any], *, detectados: dict[str, Any],
                           cotejo: dict[str, Any], tendencias: dict[str, Any],
                           comparacion: dict[str, Any]) -> list[dict[str, Any]]:
    """Oportunidades derivadas de datos, nunca de suposiciones optimistas."""
    oportunidades: list[dict[str, Any]] = []
    flujo, tarjetas = perfil["flujo"], perfil["tarjetas"]
    ahorro, gastos = perfil["ahorro"], perfil["gastos"]
    excedente = int(flujo["ahorro"])

    if excedente > 0 and ahorro["aporte_neto_mes"] < excedente:
        sin_usar = excedente - max(ahorro["aporte_neto_mes"], 0)
        oportunidades.append(_oportunidad(
            "capacidad_ahorro_sin_usar", "Hay capacidad de ahorro sin asignar",
            f"El mes deja {fmt_cop(excedente)} y solo {fmt_cop(max(ahorro['aporte_neto_mes'], 0))} "
            "se movieron a cajitas.",
            "Definan a dónde va ese excedente antes de que se diluya en gasto no planeado.",
            f"Asignar {fmt_cop(sin_usar)} cambia el punto de partida del próximo mes.",
            {"excedente": excedente, "aportado": ahorro["aporte_neto_mes"]},
            monto=sin_usar, confianza="Alta"))

    con_deuda = [tarjeta for tarjeta in tarjetas["tarjetas"] if tarjeta["saldo_deuda"]]
    if con_deuda and excedente > 0:
        cara = max(con_deuda, key=lambda item: item["interes_mensual"])
        if cara["interes_mensual"]:
            abono = min(excedente, max(cara["saldo_deuda"] - cara["pago_minimo_efectivo"], 0))
            if abono:
                oportunidades.append(_oportunidad(
                    "abono_extra_tarjeta", f"Se puede acelerar el pago de {cara['nombre']}",
                    f"Tiene la tasa más alta registrada ({cara['interes_mensual']:.2f}% mensual) sobre "
                    f"{fmt_cop(cara['saldo_deuda'])}.",
                    f"Abonar {fmt_cop(abono)} por encima del mínimo este mes.",
                    f"Evita cerca de {fmt_cop(round(abono * cara['interes_mensual'] / 100))} de interés mensual.",
                    {"tarjeta_id": cara["id"], "saldo": cara["saldo_deuda"], "tasa": cara["interes_mensual"]},
                    monto=abono, confianza="Alta"))

    for meta in ahorro["metas"]:
        if meta.get("cumplida") and meta.get("ahorro_promedio_mensual"):
            oportunidades.append(_oportunidad(
                "meta_cumplida_liberada", f"La meta «{meta['nombre']}» ya está cumplida",
                f"Se venían aportando cerca de {fmt_cop(meta['ahorro_promedio_mensual'])} al mes.",
                "Redirijan ese aporte a otra meta o a la tarjeta más cara antes de que se reabsorba en gasto.",
                f"Libera {fmt_cop(meta['ahorro_promedio_mensual'])} mensuales ya habituados.",
                {"meta": meta["nombre"], "aporte": round(meta["ahorro_promedio_mensual"])},
                monto=round(meta["ahorro_promedio_mensual"]), confianza="Media"))

    for candidato in detectados.get("candidatos", [])[:5]:
        if candidato["clase"] == "suscripcion":
            oportunidades.append(_oportunidad(
                "suscripcion_revisable", f"Revisar la suscripción «{candidato['nombre']}»",
                f"Se cobra {fmt_cop(candidato['monto_promedio'])} cada mes desde hace "
                f"{candidato['ocurrencias']} meses.",
                "Confirmen si siguen usándola; si no, cancelarla libera el monto completo todos los meses.",
                f"Hasta {fmt_cop(candidato['monto_promedio'] * 12)} al año si ya no se usa.",
                {"nombre": candidato["nombre"], "monto": candidato["monto_promedio"]},
                monto=candidato["monto_promedio"], confianza=candidato["confianza"]))

    for item in comparacion.get("principales_bajadas", [])[:2]:
        oportunidades.append(_oportunidad(
            "categoria_a_la_baja", f"{item['categoria']} bajó este mes",
            f"Gastaron {fmt_cop(abs(item['diferencia']))} menos que en {comparacion['mes_comparado']}.",
            "Si el recorte fue deliberado, fíjenlo como presupuesto en vez de dejarlo al azar.",
            f"Sostenerlo valdría {fmt_cop(abs(item['diferencia']) * 12)} al año.",
            {"categoria": item["categoria"], "diferencia": item["diferencia"]},
            monto=abs(item["diferencia"]), confianza="Media"))

    exceso_registrado = [item for item in cotejo.get("monto_desactualizado", [])
                         if item["diferencia"] < 0]
    for item in exceso_registrado[:2]:
        oportunidades.append(_oportunidad(
            "gasto_fijo_sobrestimado", f"«{item['nombre']}» está presupuestado por encima de lo real",
            f"Registrado en {fmt_cop(item['valor_registrado'])} pero el promedio real es "
            f"{fmt_cop(item['promedio_real'])}.",
            "Ajusten el valor del gasto fijo para que el margen disponible deje de estar subestimado.",
            f"Libera {fmt_cop(abs(item['diferencia']))} de margen mensual que ya tenían.",
            item, monto=abs(item["diferencia"]), confianza="Alta"))

    if gastos["discrecionales"] and tendencias.get("gastos", {}).get("direccion") == "bajando":
        oportunidades.append(_oportunidad(
            "gasto_en_descenso", "El gasto total viene bajando",
            "La serie de los últimos meses muestra un descenso sostenido.",
            "Aprovechen el momento para subir el aporte fijo a la reserva antes de que el gasto vuelva a subir.",
            "Convierte un recorte temporal en ahorro permanente.",
            {"serie": tendencias["gastos"].get("serie", [])}, confianza="Media"))

    return sorted(oportunidades, key=lambda item: -item["monto_estimado"])


# ---------------------------------------------------------------------------
# Agrupación, priorización y plan
# ---------------------------------------------------------------------------

GRUPOS: dict[str, tuple[str, str]] = {
    "presion_tarjeta": ("La presión de las tarjetas está aumentando",
                        "Varias señales apuntan a la misma decisión: cuánto abonar y qué dejar de cargar."),
    "gastos_fijos": ("Los compromisos fijos necesitan una revisión",
                     "Lo registrado y lo que de verdad se está cobrando no coinciden del todo."),
    "integridad": ("Hay datos que no cuadran",
                   "Antes de liquidar entre ustedes conviene resolver esto."),
    "ahorro": ("La reserva y las metas compiten por el mismo flujo",
               "Las señales de ahorro apuntan a una sola decisión de prioridades."),
}

_CATEGORIA_A_GRUPO: dict[str, str] = {
    "tarjeta": "presion_tarjeta",
    "gastos_fijos": "gastos_fijos",
    "integridad": "integridad",
    "ahorro": "ahorro",
    "metas": "ahorro",
}


def calcular_prioridad(hallazgo: Hallazgo, ingreso_mensual: int) -> float:
    """Combina severidad, impacto, urgencia, confianza, reversibilidad y control.

    La reversibilidad resta: algo fácil de deshacer puede esperar. El control
    suma: no tiene sentido poner arriba algo sobre lo que no se puede actuar.
    """
    severidad = PESO_SEVERIDAD.get(hallazgo.severidad, 0.3)
    impacto = max(_escala_monto(hallazgo.monto_involucrado),
                  _impacto_relativo(hallazgo.monto_involucrado, ingreso_mensual))
    confianza = CONFIANZA_TEXTO.get(hallazgo.confianza, 0.6)
    puntaje = (severidad * 0.40
               + min(impacto, 1.0) * 0.25
               + hallazgo.urgencia * 0.15
               + confianza * 0.10
               + (1.0 - hallazgo.reversibilidad) * 0.05
               + hallazgo.control_usuario * 0.05)
    return round(puntaje, 4)


def priorizar(hallazgos: Sequence[Hallazgo], ingreso_mensual: int) -> list[Hallazgo]:
    for hallazgo in hallazgos:
        hallazgo.prioridad_calculada = calcular_prioridad(hallazgo, ingreso_mensual)
    return sorted(hallazgos, key=lambda item: (ORDEN_SEVERIDAD.get(item.severidad, 9),
                                               -item.prioridad_calculada))


def agrupar(hallazgos: Sequence[Hallazgo]) -> list[dict[str, Any]]:
    """Reúne hallazgos que obligan a la misma decisión en un solo bloque."""
    por_grupo: dict[str, list[Hallazgo]] = {}
    sueltos: list[Hallazgo] = []
    for hallazgo in hallazgos:
        clave = hallazgo.grupo or _CATEGORIA_A_GRUPO.get(hallazgo.categoria)
        if clave and clave in GRUPOS:
            por_grupo.setdefault(clave, []).append(hallazgo)
        else:
            sueltos.append(hallazgo)
    bloques: list[dict[str, Any]] = []
    for clave, items in por_grupo.items():
        if len(items) == 1:
            sueltos.append(items[0])
            continue
        titulo, resumen = GRUPOS[clave]
        severidad = min(items, key=lambda item: ORDEN_SEVERIDAD.get(item.severidad, 9)).severidad
        bloques.append({
            "tipo": "grupo", "id": clave, "titulo": titulo, "resumen": resumen,
            "severidad": severidad, "prioridad": _ETIQUETA_UI.get(severidad, severidad),
            "monto_involucrado": sum(item.monto_involucrado for item in items),
            "hallazgos": [item.a_dict() for item in items],
            "acciones": [item.accion for item in items],
        })
    for hallazgo in sueltos:
        bloques.append({"tipo": "hallazgo", **hallazgo.a_dict()})
    return sorted(bloques, key=lambda item: (ORDEN_SEVERIDAD.get(item.get("severidad", MEDIA), 9),
                                             -int(item.get("monto_involucrado", 0) or 0)))


def plan_de_accion(hallazgos: Sequence[Hallazgo], oportunidades: Sequence[dict[str, Any]],
                   perfil: dict[str, Any]) -> dict[str, Any]:
    """Responde «¿qué deberíamos hacer?» ordenando por horizonte temporal."""
    inmediato: list[dict[str, Any]] = []
    este_mes: list[dict[str, Any]] = []
    tres_meses: list[dict[str, Any]] = []
    opcional: list[dict[str, Any]] = []

    def paso(origen: Any, accion: str, por_que: str, requiere: str, impacto: str,
             monto: int = 0) -> dict[str, Any]:
        return {"accion": accion, "por_que": por_que, "requiere": requiere,
                "impacto_esperado": impacto, "monto": monto,
                "origen": getattr(origen, "id", None) or (origen.get("id") if isinstance(origen, dict) else None)}

    for hallazgo in hallazgos:
        registro = paso(hallazgo, hallazgo.accion, hallazgo.que, _requisito(hallazgo),
                        hallazgo.impacto, hallazgo.monto_involucrado)
        if hallazgo.severidad == CRITICA:
            inmediato.append(registro)
        elif hallazgo.severidad == ALTA:
            este_mes.append(registro)
        elif hallazgo.severidad == MEDIA:
            tres_meses.append(registro)
        elif hallazgo.severidad not in (POSITIVA,):
            opcional.append(registro)

    for oportunidad in oportunidades[:4]:
        registro = paso(oportunidad, oportunidad["accion"], oportunidad["detalle"],
                        "Una decisión de ustedes; no requiere dinero nuevo.",
                        oportunidad["beneficio"], oportunidad["monto_estimado"])
        (este_mes if oportunidad["monto_estimado"] >= max(perfil["ingresos"]["total"] * 0.05, 1)
         else opcional).append(registro)

    flujo = int(perfil["flujo"]["ahorro"])
    if not inmediato and not este_mes and flujo > 0:
        este_mes.append({"accion": "Asignar el excedente del mes antes de que se diluya",
                         "por_que": f"El mes deja {fmt_cop(flujo)} sin destino declarado.",
                         "requiere": "Decidir entre reserva, deuda o meta.",
                         "impacto_esperado": "Convierte un sobrante en una decisión.",
                         "monto": flujo, "origen": "excedente_libre"})

    return {
        "inmediato": inmediato[:4], "este_mes": este_mes[:5],
        "proximos_tres_meses": tres_meses[:5], "optimizacion_opcional": opcional[:5],
        "resumen": _resumen_plan(inmediato, este_mes, tres_meses),
        "nota": "El orden respeta severidad e impacto; ninguna acción se ejecuta sola: ustedes la confirman.",
    }


def _requisito(hallazgo: Hallazgo) -> str:
    if hallazgo.categoria in ("datos", "integridad"):
        return "Revisar registros; no requiere dinero."
    if hallazgo.monto_involucrado:
        return f"Disponer de aproximadamente {fmt_cop(hallazgo.monto_involucrado)} o ajustar otro compromiso."
    return "Una decisión de ustedes."


def _resumen_plan(inmediato: Sequence[Any], este_mes: Sequence[Any],
                  tres_meses: Sequence[Any]) -> str:
    if inmediato:
        return (f"Hay {len(inmediato)} cosa(s) que atendería ya, antes de cualquier gasto nuevo. "
                f"Después vienen {len(este_mes)} decisiones de este mes.")
    if este_mes:
        return f"No hay urgencias, pero sí {len(este_mes)} decisiones que vale la pena cerrar este mes."
    if tres_meses:
        return "Nada urgente. Lo que queda son ajustes para los próximos meses."
    return "No encuentro acciones pendientes con los datos registrados."


def resumen_severidades(hallazgos: Iterable[Hallazgo]) -> dict[str, int]:
    conteo: dict[str, int] = {}
    for hallazgo in hallazgos:
        conteo[hallazgo.severidad] = conteo.get(hallazgo.severidad, 0) + 1
    return conteo
