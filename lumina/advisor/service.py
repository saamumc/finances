"""Asesor offline, determinístico y explicable de LÚMINA."""
from __future__ import annotations

import datetime as dt
from math import ceil
from typing import Any

from . import intelligence as iq
from ..core import calculations as calc
from ..core import database as db
from ..core import engine
from ..constants import PRIORIDAD_DISCRECIONAL


def _cop(valor: int) -> str:
    return calc.fmt_cop(max(valor, 0))


def _con_siguiente_paso(mensaje: str, recomendacion: str | None) -> tuple[str, str]:
    """Da a cada lectura una acción concreta, sin alterar los datos analizados."""
    paso = (recomendacion or "Revisen este punto juntos antes de tomar la próxima decisión de gasto.").strip()
    paso = paso.rstrip(". ") + "."
    if "siguiente paso:" in mensaje.lower():
        return mensaje, paso
    return f"{mensaje.rstrip()} Siguiente paso: {paso}", paso


def _rec(tipo: str, prioridad: str, titulo: str, mensaje: str, datos: dict[str, Any], *,
         persona: str = "pareja", impacto: int = 0, confianza: str = "Media",
         tarjeta_id: int | None = None, cta: str | None = None, accion: str | None = None,
         explicacion: str | None = None, recomendacion: str | None = None) -> dict[str, Any]:
    """Contrato ampliado que conserva la clave ``mensaje`` del frontend actual."""
    mensaje, paso = _con_siguiente_paso(mensaje, recomendacion)
    detalle = explicacion or mensaje
    return {
        "tipo": tipo, "prioridad": prioridad, "persona": persona, "titulo": titulo,
        "mensaje": mensaje, "resumen": mensaje, "motivo": detalle,
        "impacto_estimado": impacto, "datos": datos, "confianza": confianza,
        "tarjeta_id": tarjeta_id, "cta": cta, "accion": accion,
        "siguiente_paso": paso,
        "explicacion": {"que_detecte": datos, "por_que_importa": detalle,
                        "que_recomiendo": paso, "impacto": impacto,
                        "confianza": confianza},
    }


def _estado_tarjeta(tarjeta: dict[str, Any]) -> tuple[str, str, str]:
    uso = tarjeta["utilizacion"]
    if uso >= .90:
        return "crítica", "alta", "🔴"
    if uso >= .70:
        return "alta", "alta", "🟠"
    if uso >= .50:
        return "atención", "media", "🟡"
    return "saludable", "positiva", "🟢"


def _faltantes_tarjeta(tarjeta: dict[str, Any]) -> list[str]:
    faltan: list[str] = []
    if not tarjeta.get("fecha_corte"):
        faltan.append("fecha de corte")
    if not tarjeta.get("fecha_pago"):
        faltan.append("fecha límite de pago")
    if not tarjeta.get("interes_mensual"):
        faltan.append("tasa mensual")
    if tarjeta.get("saldo_historico_pendiente"):
        faltan.append("desglose de la deuda histórica")
    return faltan


def _lectura_tarjeta(tarjeta: dict[str, Any], mes: str) -> list[dict[str, Any]]:
    estado, prioridad, icono = _estado_tarjeta(tarjeta)
    actividad = calc.resumen_mensual_tarjeta(tarjeta["id"], mes)
    faltan = _faltantes_tarjeta(tarjeta)
    uso = f"{tarjeta['utilizacion']:.0%}"
    base = {"deuda": tarjeta["saldo_deuda"], "cupo": tarjeta["cupo_total"],
            "disponible": tarjeta["cupo_disponible"], "utilizacion": tarjeta["utilizacion"],
            "pago_minimo": tarjeta["pago_minimo"], "interes_mensual": tarjeta["interes_mensual"],
            "actividad_mes": actividad, "faltantes": faltan}
    resultado: list[dict[str, Any]] = []
    if tarjeta["saldo_deuda"]:
        significado = ("Ya no es una tarjeta cómoda para financiar imprevistos: una utilización alta reduce el margen y puede encarecer decisiones futuras."
                       if estado in ("alta", "crítica") else
                       "La deuda aún tiene margen, pero conviene usar la tarjeta como medio de pago y no como una deuda que se traslada mes a mes.")
        accion = ("Pausen nuevas compras en esta tarjeta y definan un abono adicional antes del próximo corte."
                  if estado in ("alta", "crítica") else
                  "Si el efectivo ya está disponible, programen el pago total antes de la fecha límite para evitar intereses.")
        resultado.append(_rec(
            "tarjeta", prioridad, f"{icono} {tarjeta['nombre']} está {estado}",
            f"{tarjeta['nombre']} utiliza {uso} de su cupo: {_cop(tarjeta['saldo_deuda'])} de {_cop(tarjeta['cupo_total'])}. "
            f"{significado} {accion}", base, impacto=tarjeta["saldo_deuda"],
            confianza="Alta" if not faltan else "Media", tarjeta_id=tarjeta["id"],
            cta="Ver tarjeta", accion="ver_tarjeta",
            explicacion=f"La señal usa únicamente deuda y cupo registrados. {('Falta ' + ', '.join(faltan) + '.') if faltan else 'La tarjeta tiene los datos principales registrados.'}",
            recomendacion=accion))
    neto = actividad["compras"] + actividad["intereses"] + actividad["cargos"] - actividad["pagos"]
    if neto > 0:
        resultado.append(_rec(
            "tendencia", "media" if estado == "saludable" else "alta", f"La deuda de {tarjeta['nombre']} está creciendo este mes",
            f"Durante el período registrado aparecen compras por {_cop(actividad['compras'])}, intereses y cargos por "
            f"{_cop(actividad['intereses'] + actividad['cargos'])} y pagos por {_cop(actividad['pagos'])}. "
            f"El cambio neto es +{_cop(neto)}. Esto se parece más a deuda flotante que a un uso estratégico de liquidez: están pagando, pero la obligación sigue subiendo.",
            {**base, "cambio_neto": neto}, impacto=neto, confianza="Alta", tarjeta_id=tarjeta["id"],
            cta="Registrar pago", accion="registrar_pago",
            recomendacion="Comparar un pago adicional con nuevas compras discrecionales antes del próximo corte."))
    elif actividad["pagos"] and neto < 0:
        resultado.append(_rec(
            "tendencia", "positiva", f"Señal positiva en {tarjeta['nombre']}",
            f"Los pagos registrados ({_cop(actividad['pagos'])}) superaron compras, intereses y cargos "
            f"({_cop(actividad['compras'] + actividad['intereses'] + actividad['cargos'])}). La deuda bajó {_cop(-neto)} en el período. "
            "Es una mejora real de flujo y les devuelve capacidad de decisión.",
            {**base, "reduccion_neta": -neto}, impacto=-neto, confianza="Alta", tarjeta_id=tarjeta["id"],
            cta="Ver historial", accion="ver_tarjeta",
            recomendacion="Mantengan el ritmo y eviten convertir el cupo liberado en nuevas compras antes de cerrar esta deuda"))
    if tarjeta["saldo_historico_pendiente"]:
        resultado.append(_rec(
            "datos", "info", f"Deuda histórica sin desglose en {tarjeta['nombre']}",
            f"Hay {_cop(tarjeta['saldo_historico_pendiente'])} registrados como saldo histórico. El modelo lo atribuye provisionalmente al titular legal para cuadrar el ledger, "
            "pero no afirma que esa persona haya realizado ese consumo. Sin ese desglose no es responsable decidir cómo repartirlo entre ustedes.",
            base, impacto=tarjeta["saldo_historico_pendiente"], confianza="Baja", tarjeta_id=tarjeta["id"],
            cta="Ver tarjeta", accion="ver_tarjeta",
            recomendacion="Revisen el extracto o los comprobantes y registren el origen de esta deuda antes de hacer una liquidación entre ustedes"))
    if tarjeta["pagado_persona1"] or tarjeta["pagado_persona2"]:
        p1, p2 = tarjeta["pagado_persona1"], tarjeta["pagado_persona2"]
        r1, r2 = tarjeta["deuda_persona1"], tarjeta["deuda_persona2"]
        mayor = "Samuel" if p1 - r1 > p2 - r2 else "Sara"
        resultado.append(_rec(
            "pareja", "info", f"Aportes y responsabilidad en {tarjeta['nombre']}",
            f"Samuel ha aportado {_cop(p1)} y conserva {_cop(r1)} de responsabilidad pendiente; "
            f"Sara ha aportado {_cop(p2)} y conserva {_cop(r2)}. {mayor} está aportando una proporción mayor frente a la responsabilidad pendiente registrada. "
            "No es un juicio sobre quién gasta más: muestra quién está poniendo efectivo hoy frente a quién conserva la responsabilidad de consumo.",
            {**base, "aporte_samuel": p1, "aporte_sara": p2, "deuda_samuel": r1, "deuda_sara": r2},
            confianza="Media", tarjeta_id=tarjeta["id"], cta="Revisar distribución", accion="ver_tarjeta",
            recomendacion="Acuerden si ese exceso será una liquidación entre ustedes o si se compensa con un próximo pago"))
    return resultado


def _mes_anterior(mes: str) -> str:
    ano, numero = (int(valor) for valor in mes.split("-"))
    return f"{ano - 1:04d}-12" if numero == 1 else f"{ano:04d}-{numero - 1:02d}"


def _meses_con_actividad() -> list[str]:
    meses = {x["mes"] for x in db.get_ingresos()} | {x["mes"] for x in db.get_gastos()} | {x["mes"] for x in db.get_movimientos_ahorro()} | {x["mes"] for x in db.get_pagos_deuda()}
    return sorted(meses)


def confianza_datos(mes: str) -> dict[str, Any]:
    """Calidad de la evidencia, nunca una afirmación de certeza financiera."""
    meses = _meses_con_actividad()
    ingresos, gastos = db.get_ingresos(mes), db.get_gastos(mes)
    completos = sum(bool(db.get_ingresos(periodo) and db.get_gastos(periodo)) for periodo in meses)
    if completos >= 3 and ingresos and gastos:
        nivel, razon = "Alta", "Hay al menos tres meses con ingresos y gastos registrados."
    elif ingresos and gastos:
        nivel, razon = "Media", "El mes tiene ingresos y gastos, pero el historial completo aún es limitado."
    else:
        nivel, razon = "Baja", "Faltan ingresos o gastos del período; las proyecciones son orientativas."
    return {"nivel": nivel, "meses_con_actividad": len(meses), "meses_completos": completos, "razon": razon,
            "periodo_actual_incompleto": mes == dt.date.today().isoformat()[:7] and dt.date.today().day < 25}


def fondo_emergencia(mes: str) -> dict[str, Any]:
    """Adaptador de nombres para el contrato histórico del asesor.

    El cálculo vive en ``financial_engine.emergency_fund`` para que dashboard,
    asesor y simulaciones partan de una sola regla.
    """
    fondo = engine.emergency_fund(mes)
    return {"encontrado": fondo["found"], "nombre": fondo["name"], "actual": fondo["current"],
            "objetivo": fondo["target"], "meses_cubiertos": fondo["coverage_months"],
            "meses_objetivo": fondo["target_months"], "faltante": fondo["shortfall"],
            "progreso": fondo["progress"], "base_gastos": fondo["monthly_base"], "nota": fondo["assumption"]}


def proyectar_deuda(tarjeta_id: int, pago_adicional: int = 0) -> dict[str, Any]:
    """Amortización mensual explicable. No modifica saldo ni registra un pago."""
    tarjeta = next((t for t in calc.resumen_tarjetas() if t["id"] == tarjeta_id), None)
    if tarjeta is None:
        raise ValueError("Tarjeta no encontrada.")
    if pago_adicional < 0:
        raise ValueError("El pago adicional no puede ser negativo.")
    saldo, tasa = tarjeta["saldo_deuda"], tarjeta["interes_mensual"] / 100
    pago_base = min(max(tarjeta["pago_minimo"], 0), saldo) or 0
    def recorrer(pago: int) -> tuple[int | None, int]:
        pendiente, intereses, meses = saldo, 0, 0
        # 600 meses evita presentar una proyección infinita si el pago ni
        # siquiera cubre los intereses estimados.
        while pendiente > 0 and meses < 600:
            interes = round(pendiente * tasa)
            abono = min(pago, pendiente + interes)
            if abono <= interes and pendiente:
                return None, intereses
            intereses += interes; pendiente = max(pendiente + interes - abono, 0); meses += 1
        return (meses if pendiente == 0 else None), intereses
    meses_actual, interes_actual = recorrer(pago_base)
    pago_mejorado = min(saldo + round(saldo * tasa), pago_base + pago_adicional)
    meses_mejorado, interes_mejorado = recorrer(pago_mejorado)
    return {"tarjeta_id": tarjeta_id, "tarjeta": tarjeta["nombre"], "saldo": saldo, "tasa_mensual": tarjeta["interes_mensual"],
            "pago_minimo": pago_base, "pago_adicional": pago_adicional, "pago_mejorado": pago_mejorado,
            "meses_actual": meses_actual, "meses_mejorado": meses_mejorado,
            "meses_ahorrados": max((meses_actual or 0) - (meses_mejorado or 0), 0) if meses_actual else None,
            "interes_actual_estimado": interes_actual, "interes_mejorado_estimado": interes_mejorado,
            "interes_ahorrado_estimado": max(interes_actual - interes_mejorado, 0),
            "confianza": "Media" if tarjeta["interes_mensual"] and pago_base else "Baja",
            "nota": "Estimación con saldo, tasa mensual y pago mínimo registrados. No incluye nuevas compras, seguros, cuotas bancarias ni cambios de tasa."}


def calendario_financiero(mes: str) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    for tarjeta in calc.resumen_tarjetas():
        if tarjeta["saldo_deuda"] and tarjeta.get("fecha_pago"):
            eventos.append({"fecha": tarjeta["fecha_pago"], "tipo": "Pago de tarjeta", "titulo": tarjeta["nombre"], "monto": min(tarjeta["pago_minimo"], tarjeta["saldo_deuda"]), "prioridad": "importante"})
        if tarjeta.get("fecha_corte"):
            eventos.append({"fecha": tarjeta["fecha_corte"], "tipo": "Corte", "titulo": tarjeta["nombre"], "monto": 0, "prioridad": "atención"})
    for meta in calc.progreso_metas():
        if meta.get("fecha_objetivo") and not meta["cumplida"]:
            eventos.append({"fecha": meta["fecha_objetivo"], "tipo": "Meta", "titulo": meta["nombre"], "monto": meta["faltante"], "prioridad": "importante" if meta["atrasada"] else "atención"})
    return sorted(eventos, key=lambda e: e["fecha"])


def score_salud_financiera(mes: str) -> dict[str, Any]:
    flujo = calc.flujo_caja_mes(mes); tarjetas = calc.resumen_tarjetas(); emergencia = fondo_emergencia(mes)
    ingreso = flujo["ingresos"]; deuda = sum(t["saldo_deuda"] for t in tarjetas); cupo = sum(t["cupo_total"] for t in tarjetas)
    liquidez = max(calc.liquidez_total(mes), 0)
    componentes = {
        "liquidez": min(25, round(25 * min(liquidez / ingreso, 1))) if ingreso else 0,
        "deuda": max(0, 25 - round(25 * (deuda / cupo))) if cupo else 25,
        "flujo": 20 if flujo["ahorro"] > 0 else 8 if flujo["ahorro"] == 0 else 0,
        "emergencia": min(20, round(20 * min(emergencia["meses_cubiertos"] / emergencia["meses_objetivo"], 1))),
        "registro": 10 if confianza_datos(mes)["nivel"] == "Alta" else 6 if ingreso else 2,
    }
    return {"score": sum(componentes.values()), "componentes": componentes,
            "lecturas": {"liquidez": "buena" if componentes["liquidez"] >= 18 else "atención", "deuda": "buena" if componentes["deuda"] >= 18 else "atención", "ahorro": "bueno" if componentes["flujo"] >= 15 else "atención", "fondo_emergencia": "bueno" if componentes["emergencia"] >= 14 else "bajo", "tarjetas": "buena" if componentes["deuda"] >= 18 else "atención"},
            "nota": "El score suma cinco componentes visibles; es una guía explicable, no una calificación crediticia."}


def comparacion_mensual(mes: str) -> dict[str, Any]:
    anterior = _mes_anterior(mes); actual, previo = calc.flujo_caja_mes(mes), calc.flujo_caja_mes(anterior)
    compras = sum(x["valor_original"] for x in db.get_compras_tarjeta() if x.get("mes") == mes)
    compras_previo = sum(x["valor_original"] for x in db.get_compras_tarjeta() if x.get("mes") == anterior)
    return {"mes": mes, "mes_anterior": anterior, "ingresos": actual["ingresos"] - previo["ingresos"], "gastos": actual["salidas"] - previo["salidas"], "ahorro": actual["ahorro"] - previo["ahorro"], "uso_tarjetas": compras - compras_previo,
            "suficiente_historial": bool(db.get_ingresos(anterior) or db.get_gastos(anterior))}


def plan_financiero_mes(mes: str) -> list[dict[str, Any]]:
    estado = engine.financial_state(mes)
    flujo = estado["flow"]; tarjetas = [t for t in estado["debt"]["cards"] if t["saldo_deuda"]]; emergencia = fondo_emergencia(mes)
    minimo = sum(min(t["pago_minimo"], t["saldo_deuda"]) for t in tarjetas)
    disponible = max(flujo["ahorro"], 0); pasos: list[dict[str, Any]] = []
    if minimo: pasos.append({"orden": 1, "accion": "Pagar mínimos", "monto": minimo, "detalle": "Cubre los pagos mínimos registrados antes de acelerar otras metas."})
    reserva = min(max(disponible - minimo, 0), emergencia["faltante"])
    if reserva: pasos.append({"orden": len(pasos)+1, "accion": "Reservar emergencia", "monto": reserva, "detalle": f"Acerca {emergencia['nombre']} a {emergencia['meses_objetivo']} meses de cobertura."})
    restante = max(disponible - minimo - reserva, 0)
    if tarjetas and restante:
        foco = max(tarjetas, key=lambda t: (t["interes_mensual"], t["utilizacion"]))
        abono = min(restante, foco["saldo_deuda"] - min(foco["pago_minimo"], foco["saldo_deuda"]))
        if abono: pasos.append({"orden": len(pasos)+1, "accion": f"Abono adicional a {foco['nombre']}", "monto": abono, "detalle": "Prioriza la tasa mensual y la utilización registradas."}); restante -= abono
    if restante: pasos.append({"orden": len(pasos)+1, "accion": "Gasto disponible", "monto": restante, "detalle": "Solo después de mínimos, reserva y deuda prioritaria."})
    if not pasos: pasos.append({"orden": 1, "accion": "Registrar el flujo", "monto": 0, "detalle": "Faltan datos o no hay excedente registrado para asignar."})
    return pasos


def _priorizar_recomendaciones(recomendaciones: list[dict[str, Any]], limite: int = 5) -> list[dict[str, Any]]:
    """Mantiene pocas señales distintas, ordenadas por riesgo e impacto."""
    orden = {"crítica": 0, "alta": 1, "media": 2, "info": 3, "positiva": 4}
    resultado: list[dict[str, Any]] = []
    vistos: set[tuple[str, int | None]] = set()
    for item in sorted(recomendaciones, key=lambda value: (orden.get(value["prioridad"], 9), -abs(int(value["impacto_estimado"])) )):
        # Una señal de tarjeta puede reunir uso, deuda y acción; no repetimos
        # cuatro avisos que obliguen a tomar exactamente la misma decisión.
        clave = ("tarjeta", item["tarjeta_id"]) if item.get("tarjeta_id") and item["tipo"] in {"diagnostico", "tarjeta", "tendencia"} else (item["tipo"], item.get("tarjeta_id"))
        if clave in vistos:
            continue
        vistos.add(clave); resultado.append(item)
        if len(resultado) >= limite:
            break
    return resultado


def analizar_finanzas(mes: str, estado: dict[str, Any] | None = None) -> dict[str, Any]:
    """Analiza una foto recién reconstruida del estado financiero.

    ``estado`` permite a la capa de servicio compartir una misma foto con la
    vista solicitante, sin guardar resultados entre operaciones. Las llamadas
    directas siguen siendo compatibles y reconstruyen la foto desde SQLite.
    """
    estado = estado or engine.financial_state(mes)
    diagnostico = engine.diagnose(estado)
    auditoria = engine.audit_integrity(mes)
    flujo = estado["flow"]
    liquidez = estado["liquidity"]["available"]
    tarjetas = estado["debt"]["cards"]
    metas = estado["savings"]["goals"]
    cajas = estado["savings"]["funds"]
    # La recomendación de equilibrio entre personas considera el libro
    # completo; la foto mensual queda disponible en ``estado['couple']``.
    balance_pareja = estado["couple_historical"]
    recs: list[dict[str, Any]] = []
    # Adapta el diagnóstico estructurado al contrato ya consumido por la UI.
    etiquetas = {"critical": "alta", "high": "alta", "medium": "media", "low": "info", "positive": "positiva"}
    for hallazgo in diagnostico:
        if hallazgo["code"] in {"card_critical", "card_high", "card_growing", "missing_rate"}:
            continue  # _lectura_tarjeta los explica con mayor contexto humano.
        recs.append(_rec("diagnostico", etiquetas[hallazgo["priority"]], hallazgo["title"],
                         f"{hallazgo['cause']} Riesgo: {hallazgo['risk']}", hallazgo["evidence"],
                         impacto=hallazgo["impact"], confianza=estado["data_quality"]["level"],
                         explicacion=f"Evidencia: {hallazgo['evidence']}. {hallazgo['cause']} Riesgo: {hallazgo['risk']}",
                         recomendacion=hallazgo["action"]))
    for problema in auditoria["issues"]:
        recs.append(_rec("integridad", "alta" if problema["severity"] == "critical" else "media", "Datos que requieren revisión",
                         problema["detail"], problema, confianza="Baja",
                         recomendacion="Corrige o completa estos datos antes de tomar una decisión financiera importante."))
    if not db.get_ingresos(mes):
        recs.append(_rec("datos", "info", "Información aún incompleta",
                         "Todavía no hay ingresos registrados este mes. Se puede ver la deuda y las reservas, pero no sería prudente calcular cuánto pueden destinar a pagos acelerados.",
                          {"ingresos": 0}, confianza="Baja",
                          recomendacion="Registren los ingresos del mes antes de comprometer un pago adicional o una nueva compra"))
    income_history = estado.get("income_history", {})
    missing_income = [history for history in income_history.values() if history["dato_faltante_mes_actual"]]
    if missing_income and any(history["meses_con_datos"] for history in missing_income):
        names = ", ".join(history["persona_nombre"] for history in missing_income)
        references = {history["persona_nombre"]: history["siguiente_ingreso_estimado"] for history in missing_income}
        recs.append(_rec("ingresos_historicos", "info", "Ingreso actual pendiente de confirmar",
                         f"Falta confirmar el ingreso de {names}. Hay una referencia histórica, pero no la trato como dinero disponible ni reemplazo un valor confirmado.",
                         {"personas": [history["persona"] for history in missing_income], "referencias_estimadas": references},
                         confianza=min((history["confianza"] for history in missing_income), key={"Desconocida": 0, "Baja": 1, "Media": 2, "Alta": 3}.get),
                         recomendacion="¿El ingreso de este mes ya está confirmado? Regístrenlo solo cuando conozcan el monto real."))
    for tarjeta in tarjetas:
        recs.extend(_lectura_tarjeta(tarjeta, mes))
    con_deuda = [t for t in tarjetas if t["saldo_deuda"]]
    if len(con_deuda) > 1:
        estrategias = calc.estrategias_tarjetas()
        principal = estrategias["avalancha"][0]
        recs.append(_rec(
            "estrategia", "info", "Entre sus tarjetas, hay una prioridad financiera posible",
            f"Después de cubrir los mínimos, {principal['nombre']} tiene la mayor tasa mensual registrada ({principal['interes_mensual']:.2f}%). "
            "La estrategia avalancha la prioriza para reducir el interés; snowball prioriza el saldo más pequeño para ganar impulso. Elijan una sola regla y sosténganla, en vez de repartir pagos extra sin criterio.",
            {"avalancha": [t["id"] for t in estrategias["avalancha"]], "snowball": [t["id"] for t in estrategias["snowball"]]},
            confianza="Alta" if principal["interes_mensual"] else "Baja", tarjeta_id=principal["id"],
            cta="Comparar tarjetas", accion="ver_tarjetas",
            recomendacion=f"Después de cubrir mínimos, dirijan el próximo abono adicional a {principal['nombre']}"))
    for meta in metas:
        if meta["atrasada"]:
            deficit = int(meta["ahorro_necesario_mensual"] - meta["ahorro_promedio_mensual"])
            recs.append(_rec("meta", "alta" if meta["prioridad"] != PRIORIDAD_DISCRECIONAL else "media",
                             f"{meta['nombre']} necesita atención",
                             f"Faltan {_cop(meta['faltante'])} y el ritmo registrado está por debajo de lo necesario para su fecha objetivo. "
                             "Antes de aumentar el aporte, comparen esa meta con cualquier tarjeta que esté generando intereses altos: proteger el flujo también protege la meta.",
                             {"faltante": meta["faltante"], "deficit": deficit}, impacto=deficit, confianza="Media",
                             recomendacion=f"Separen al menos {_cop(max(deficit, 0))} adicionales al mes para acercar {meta['nombre']} a su fecha objetivo"))
        elif meta["cumplida"]:
            recs.append(_rec("meta", "positiva", f"Meta alcanzada: {meta['nombre']}",
                             "El dinero reservado ya cubre el objetivo definido. Es un buen momento para decidir si se usará como estaba planeado o si se convierte en la siguiente meta.",
                             {"actual": meta["actual"], "objetivo": meta["monto_objetivo"]}, confianza="Alta",
                             recomendacion=f"Definan un destino concreto para {meta['nombre']} antes de redirigir ese ahorro"))
    for caja in cajas:
        if caja["meta"] and caja["saldo"] < caja["meta"] * .25:
            recs.append(_rec("cajita", "media", f"Reserva baja: {caja['nombre']}",
                             f"La cajita tiene {_cop(caja['saldo'])} frente a una referencia de {_cop(caja['meta'])}. "
                             "No conviene retirarlo por impulso: una reserva pequeña puede evitar que un imprevisto termine financiado con tarjeta.",
                             {"saldo": caja["saldo"], "referencia": caja["meta"]}, impacto=caja["meta"] - caja["saldo"],
                             recomendacion=f"Programen un aporte pequeño y recurrente a {caja['nombre']} antes de aumentar gastos discrecionales"))
    gastos_disc = sum(g["valor"] for g in db.get_gastos(mes) if g["prioridad"] == PRIORIDAD_DISCRECIONAL)
    if gastos_disc and flujo["ahorro"] <= 0:
        recs.append(_rec("recorte", "media", "Un lugar opcional para revisar",
                         f"Hay {_cop(gastos_disc)} de gasto discrecional registrado y el flujo mensual no deja ahorro. "
                         "No se trata de eliminar todo disfrute; sí de poner un límite consciente. Como referencia flexible, el 50/30/20 ayuda a separar necesidades, gustos y ahorro o deuda sin convertir el presupuesto en castigo.",
                         {"gasto_discrecional": gastos_disc, "ahorro_flujo": flujo["ahorro"]}, impacto=gastos_disc,
                         recomendacion="Elijan un gasto discrecional para pausar este mes y destinen ese valor al fondo de reserva o a la tarjeta más costosa"))
    recurrentes = engine.detect_recurring_expenses(as_of_month=mes)
    if recurrentes:
        total_recurrente = sum(item["total_mensual_estimado"] for item in recurrentes)
        confianza_recurrente = "Alta" if all(item["confianza"] == "Alta" for item in recurrentes) else "Media"
        recs.append(_rec("recurrentes", "info", "Hay gastos que se repiten cada mes",
                         f"Detecté {len(recurrentes)} patrón(es) recurrente(s) por aproximadamente {_cop(total_recurrente)} al mes. "
                         "No asumo que sean gastos innecesarios: revisen si cada uno sigue siendo útil antes de modificarlo.",
                         {"gastos": recurrentes, "total_mensual_estimado": total_recurrente}, impacto=total_recurrente,
                         confianza=confianza_recurrente,
                         recomendacion="Revisen primero el gasto recurrente más alto y decidan si quieren mantenerlo, renegociarlo o redirigir una parte a deuda o reserva."))
    fatiga = engine.savings_fatigue(mes)
    if fatiga["activa"]:
        destino = f" la cajita {fatiga['cajita_principal']}" if fatiga["cajita_principal"] else " sus aportes de ahorro"
        recs.append(_rec("fatiga_ahorro", "media", "El ahorro está dejando poco margen operativo",
                         f"Durante {len(fatiga['meses'])} meses seguidos destinaron más de {fatiga['umbral']:.0%} del margen operativo registrado al ahorro. "
                         f"Van bien al ahorrar, pero hoy el margen libre es {_cop(fatiga['margen_libre'])}; conviene revisar temporalmente{destino} antes de usar más tarjeta.",
                         fatiga, impacto=max(-fatiga["margen_libre"], 0), confianza="Media",
                         recomendacion="Mantengan los mínimos de deuda y ajusten solo el próximo aporte de ahorro si necesitan recuperar margen."))
    prediccion = engine.forecast_60_days(mes)
    proximo = next((item for item in prediccion["eventos"] if item["fecha"] <= (dt.date.today() + dt.timedelta(days=7)).isoformat()), None)
    if proximo:
        recs.append(_rec("proyeccion", "media" if proximo["monto"] else "info", f"Próximo compromiso estimado: {proximo['titulo']}",
                         f"Para el {proximo['fecha']} aparece {proximo['tipo'].lower()} por {_cop(proximo['monto'])}. "
                         "Es una proyección basada en registros previos; los ingresos no se anticipan por día porque aún no tienen fecha diaria registrada.",
                         proximo, impacto=proximo["monto"], confianza=proximo["confianza"],
                         recomendacion="No comprometan ese monto dos veces y confirmen el ingreso o pago real cuando llegue."))
    saldo_samuel = balance_pareja["balance_neto_persona1"]
    saldo_sara = balance_pareja["balance_neto_persona2"]
    if saldo_samuel > 0 and saldo_sara < 0:
        monto = min(saldo_samuel, -saldo_sara)
        recs.append(_rec("pareja", "media", "Hay un saldo pendiente entre ustedes",
                         f"Samuel está a favor por {_cop(monto)} y Sara tiene ese mismo valor pendiente de equilibrar. Este saldo refleja aportes y responsabilidades registradas; no define quién tiene razón, sino qué conviene conversar para que el dinero no genere fricción.",
                         {"a_favor_samuel": monto, "pendiente_sara": monto}, impacto=monto, confianza="Alta",
                         recomendacion=f"Acuerden si Sara transfiere {_cop(monto)} a Samuel o si lo compensarán con el próximo gasto común"))
    elif saldo_sara > 0 and saldo_samuel < 0:
        monto = min(saldo_sara, -saldo_samuel)
        recs.append(_rec("pareja", "media", "Hay un saldo pendiente entre ustedes",
                         f"Sara está a favor por {_cop(monto)} y Samuel tiene ese mismo valor pendiente de equilibrar. Este saldo refleja aportes y responsabilidades registradas; no define quién tiene razón, sino qué conviene conversar para que el dinero no genere fricción.",
                         {"a_favor_sara": monto, "pendiente_samuel": monto}, impacto=monto, confianza="Alta",
                         recomendacion=f"Acuerden si Samuel transfiere {_cop(monto)} a Sara o si lo compensarán con el próximo gasto común"))
    if not recs:
        recs.append(_rec("estable", "positiva", "Con los datos registrados, todo está estable",
                         "El flujo es positivo y no hay una utilización alta de tarjetas. Están en una posición adecuada para priorizar ahorro, metas y uso disciplinado del crédito.",
                         {"ahorro": flujo["ahorro"]}, confianza="Media",
                         recomendacion="Mantengan el registro al día y aparten primero el ahorro que quieran proteger este mes"))
    foco = max(con_deuda, key=lambda t: (t["utilizacion"], t["interes_mensual"]), default=None)
    resumen = (f"La liquidez registrada este mes es {_cop(liquidez)}. La prioridad es {foco['nombre']}, que utiliza {foco['utilizacion']:.0%} de su cupo; ordenarla primero les devuelve margen de maniobra."
               if foco else f"Este mes entraron {_cop(flujo['ingresos'])}, salieron {_cop(flujo['salidas'])} y quedan {_cop(flujo['ahorro'])} de flujo. No hay deuda vigente de tarjeta registrada.")
    plan = ["Cubrir los mínimos registrados."] if con_deuda else ["Mantener los movimientos al día."]
    if foco and foco["utilizacion"] >= .70:
        plan.append(f"Evitar aumentar {foco['nombre']} antes de revisar un pago adicional.")
    if not db.get_ingresos(mes):
        plan.append("Registrar ingresos para que la capacidad de pago tenga mejor confianza.")
    if foco and flujo["ahorro"] > 0:
        abono = min(int(flujo["ahorro"]), foco["saldo_deuda"])
        plan.append(f"Consideren destinar {_cop(abono)} este mes a {foco['nombre']} sin comprometer sus reservas.")
    confianza = confianza_datos(mes)
    emergencia = fondo_emergencia(mes)
    comparacion = comparacion_mensual(mes)
    if comparacion["suficiente_historial"]:
        if comparacion["gastos"] > 0:
            recs.append(_rec("comparacion", "media", "El gasto aumentó frente al mes anterior",
                             f"Las salidas de caja aumentaron {_cop(comparacion['gastos'])} frente a {comparacion['mes_anterior']}.",
                             comparacion, confianza=confianza["nivel"], recomendacion="Revisen las categorías que subieron antes de ampliar el gasto discrecional."))
        if comparacion["ahorro"] > 0:
            recs.append(_rec("comparacion", "positiva", "Mejoró la capacidad de ahorro",
                             f"El flujo disponible mejoró {_cop(comparacion['ahorro'])} frente al mes anterior.",
                             comparacion, confianza=confianza["nivel"], recomendacion="Decidan de forma explícita si este margen irá a reserva, deuda o una meta."))
    if emergencia["encontrado"] and emergencia["faltante"]:
        recs.append(_rec("emergencia", "media", f"{emergencia['nombre']} aún no cubre la referencia",
                         f"Tiene {_cop(emergencia['actual'])}, equivalente a {emergencia['meses_cubiertos']:.1f} meses de gastos obligatorios registrados; faltan {_cop(emergencia['faltante'])} para la referencia.",
                         emergencia, impacto=emergencia["faltante"], confianza=confianza["nivel"],
                         recomendacion="Protejan un aporte recurrente antes de depender de la tarjeta para imprevistos."))
    for persona, etiqueta in (("persona1", "Samuel"), ("persona2", "Sara")):
        personal = calc.dashboard_personal(persona, mes)
        if personal["deuda"] and any(t["utilizacion"] >= .70 for t in personal["tarjetas"]):
            recs.append(_rec("personal", "media", f"{etiqueta} tiene una tarjeta con utilización alta",
                             f"Su deuda atribuida registrada es {_cop(personal['deuda'])}. Esta señal separa responsabilidad de quién hizo el pago.",
                             {"persona": persona, "deuda": personal["deuda"]}, persona=persona, confianza=confianza["nivel"],
                             recomendacion="Revisa tu tablero personal y prioriza el mínimo de esa tarjeta."))
    alertas = []
    for evento in calendario_financiero(mes):
        if evento["tipo"] == "Pago de tarjeta":
            alertas.append({"prioridad": "importante", "titulo": f"Próximo pago: {evento['titulo']}", "detalle": f"Mínimo registrado: {_cop(evento['monto'])} para {evento['fecha']}."})
    for tarjeta in tarjetas:
        if tarjeta["utilizacion"] >= .9:
            alertas.append({"prioridad": "crítico", "titulo": f"Utilización crítica: {tarjeta['nombre']}", "detalle": f"Usa {tarjeta['utilizacion']:.0%} del cupo."})
    recs = _priorizar_recomendaciones(recs)
    presupuesto_deuda = max(int(flujo["ahorro"]), 0)
    proyeccion_deuda = engine.unified_debt_payoff(presupuesto_deuda, "avalancha", start_month=mes)
    prioridad_principal = diagnostico[0] if diagnostico else None
    return {"mes": mes, "resumen": resumen, "recomendaciones": recs, "flujo": flujo, "liquidez": liquidez,
            "metas": metas, "salud_tarjetas": [{**t, "estado_salud": _estado_tarjeta(t)[0]} for t in tarjetas],
            "plan_accion": plan, "plan_financiero": plan_financiero_mes(mes), "fondo_emergencia": emergencia,
            "score_salud": score_salud_financiera(mes), "confianza": confianza, "comparacion_mensual": comparacion,
            "calendario": calendario_financiero(mes), "alertas": alertas, "estado_financiero": estado,
            "diagnostico": diagnostico, "auditoria": auditoria, "prioridad_principal": prioridad_principal,
            "recomendaciones_principales": recs[:3], "gastos_recurrentes": recurrentes,
            "ingresos_historicos": income_history,
            "prediccion_60_dias": prediccion, "fatiga_ahorro": fatiga, "proyeccion_deuda_unificada": proyeccion_deuda}


def simular(mes: str, ajuste_discrecional: int = 0, aporte_meta: int = 0) -> dict[str, Any]:
    """Escenario puro: no escribe en SQLite."""
    base = analizar_finanzas(mes)
    capacidad = max(base["flujo"]["ahorro"] + ajuste_discrecional - aporte_meta, 0)
    return {"modifica_base": False, "capacidad_ahorro": capacidad, "cambio_flujo": ajuste_discrecional - aporte_meta,
            "mensaje": f"La simulación dejaría aproximadamente {_cop(capacidad)} disponibles para reservas después de los ajustes. "
                       "Siguiente paso: comparen este margen con el pago adicional o la meta que quieren priorizar antes de registrarlo."}


def simular_escenario(mes: str, tipo: str, monto: int, *, tarjeta_id: int | None = None) -> dict[str, Any]:
    """Escenarios puros para el simulador ampliado; jamás escribe en SQLite."""
    if monto <= 0:
        raise ValueError("El monto debe ser mayor que cero.")
    estado = engine.financial_state(mes)
    antes_liquidez = estado["liquidity"]["available"]
    antes_deuda = estado["debt"]["total"]
    antes_ahorro = estado["savings"]["total"]
    if tipo == "compra_tarjeta":
        compra = evaluar_gasto(mes, monto, metodo="tarjeta", tarjeta_id=tarjeta_id)
        return {**compra, "modifica_base": False, "escenario": tipo, "liquidez_antes": antes_liquidez,
                "liquidez_despues": antes_liquidez, "deuda_antes": antes_deuda, "deuda_despues": antes_deuda + monto,
                "ahorro_antes": antes_ahorro, "ahorro_despues": antes_ahorro, "metas_afectadas": [],
                "diagnostico": "La compra usa cupo hoy, pero crea una obligación futura por el mismo monto.",
                "recomendacion": compra["metodo_recomendado"]}
    flujo = calc.flujo_caja_mes(mes); ahorro = flujo["ahorro"]
    efectos = {"gasto": (-monto, 0, 0), "ahorro_adicional": (-monto, 0, monto), "pago_tarjeta": (-monto, -monto, 0),
               "ingreso_extra": (monto, 0, 0), "reduccion_gastos": (monto, 0, 0), "aumento_gastos": (-monto, 0, 0),
               "perdida_ingresos": (-monto, 0, 0), "retiro_cajita": (monto, 0, -monto), "aporte_meta": (-monto, 0, monto),
               "nueva_deuda": (monto, monto, 0)}
    if tipo not in efectos:
        raise ValueError("Tipo de escenario no válido.")
    liquidez, deuda, ahorro_mov = efectos[tipo]
    liquidez_despues, deuda_despues, ahorro_despues = antes_liquidez + liquidez, antes_deuda + deuda, antes_ahorro + ahorro_mov
    deficit = max(-(ahorro + liquidez), 0)
    diagnostico = "El escenario conserva el margen registrado." if not deficit else "El escenario dejaría el flujo estimado en déficit."
    recomendacion = "Puedes registrarlo si el efecto representa una decisión real y mantienes los pagos próximos cubiertos." if not deficit else "Reduce el monto, compénsalo con un recorte o espera antes de registrarlo."
    return {"modifica_base": False, "escenario": tipo, "monto": monto, "impacto_liquidez": liquidez,
            "impacto_deuda": deuda, "impacto_ahorro": ahorro_mov, "flujo_estimado": ahorro + liquidez,
            "margen_seguridad_afectado": deficit, "liquidez_antes": antes_liquidez, "liquidez_despues": liquidez_despues,
            "deuda_antes": antes_deuda, "deuda_despues": deuda_despues, "ahorro_antes": antes_ahorro,
            "ahorro_despues": ahorro_despues, "metas_afectadas": [meta["nombre"] for meta in estado["savings"]["goals"]] if tipo == "aporte_meta" else [],
            "diagnostico": diagnostico, "recomendacion": recomendacion,
            "mensaje": "Escenario calculado con movimientos existentes; no se registró ningún cambio."}


def presupuesto_seguro(mes: str, margen_porcentaje: int = 10) -> dict[str, Any]:
    if not isinstance(margen_porcentaje, int) or not 0 <= margen_porcentaje <= 50:
        raise ValueError("El margen de seguridad debe estar entre 0% y 50%.")
    estado = engine.financial_state(mes)
    flujo = estado["flow"]
    patrimonio = estado["wealth"]
    tarjetas = estado["debt"]["cards"]
    metas = estado["savings"]["goals"]
    pagos_minimos = sum(min(t["pago_minimo"], t["saldo_deuda"]) for t in tarjetas)
    metas_prioritarias = sum(int(m["ahorro_necesario_mensual"] or 0) for m in metas if m["prioridad"] != PRIORIDAD_DISCRECIONAL and not m["cumplida"])
    margen = round(flujo["ingresos"] * margen_porcentaje / 100)
    base = patrimonio["disponible_gastos_recurrentes"]
    seguro = max(base - pagos_minimos - metas_prioritarias - margen, 0)
    return {"mes": mes, "liquidez_registrada": base, "pagos_minimos_tarjetas": pagos_minimos,
            "metas_prioritarias": metas_prioritarias, "margen_seguridad": margen, "margen_porcentaje": margen_porcentaje,
            "puede_gastar_hasta": seguro, "confianza": "Media" if flujo["ingresos"] else "Baja",
            "nota": "Solo considera ingresos, gastos, reservas, tarjetas y metas registrados. "
                    "Siguiente paso: no comprometan este margen hasta confirmar que los pagos mínimos y las fechas próximas están cubiertos."}


def evaluar_gasto(mes: str, monto: int, *, metodo: str = "debito", tarjeta_id: int | None = None,
                  margen_porcentaje: int = 10) -> dict[str, Any]:
    """Simulación pura que diferencia liquidez real de nueva deuda de tarjeta."""
    if not isinstance(monto, int) or monto <= 0:
        raise ValueError("El monto de la salida debe ser mayor que cero.")
    presupuesto = presupuesto_seguro(mes, margen_porcentaje)
    advertencias: list[str] = []
    if metodo == "tarjeta":
        tarjeta = next((t for t in calc.resumen_tarjetas() if t["id"] == tarjeta_id), None)
        if tarjeta is None:
            raise ValueError("Selecciona una tarjeta válida para simular este método.")
        if monto > tarjeta["cupo_disponible"]:
            return {**presupuesto, "estado": "no", "titulo": "No con esta tarjeta", "monto": monto,
                    "despues": presupuesto["puede_gastar_hasta"], "tarjeta": tarjeta, "mensaje": "El monto supera el cupo disponible de la tarjeta seleccionada. "
                    "Siguiente paso: elijan otra fuente de pago o pospongan la compra hasta liberar cupo.",
                    "advertencias": ["Cupo insuficiente"], "metodo_recomendado": "No usar esta tarjeta", "impacto_liquidez_hoy": 0, "obligacion_futura": monto}
        uso_nuevo = (tarjeta["saldo_deuda"] + monto) / tarjeta["cupo_total"] if tarjeta["cupo_total"] else 1
        if uso_nuevo >= .70:
            advertencias.append(f"La utilización subiría a {uso_nuevo:.0%}.")
        if monto > presupuesto["puede_gastar_hasta"]:
            advertencias.append("No hay liquidez registrada suficiente para cubrir esta obligación futura con el margen actual.")
        estado = "cuidado" if advertencias else "si"
        return {**presupuesto, "estado": estado, "titulo": "Sí, pero con cuidado" if estado == "cuidado" else "Cabe en el cupo registrado",
                "monto": monto, "despues": presupuesto["puede_gastar_hasta"], "deficit": max(monto - presupuesto["puede_gastar_hasta"], 0),
                "metodo_recomendado": "Tarjeta, solo si planean cubrir la obligación futura.", "tarjeta": tarjeta,
                "utilizacion_despues": uso_nuevo, "cupo_disponible_despues": tarjeta["cupo_disponible"] - monto,
                "impacto_liquidez_hoy": 0, "obligacion_futura": monto,
                "mensaje": f"Esta compra no reduce su liquidez hoy, pero aumentaría la deuda de {tarjeta['nombre']} de {_cop(tarjeta['saldo_deuda'])} a {_cop(tarjeta['saldo_deuda'] + monto)}. "
                "Siguiente paso: úsala solo si pueden separar ese mismo monto para pagarla antes de generar intereses.",
                "advertencias": advertencias}
    if metodo not in ("efectivo", "debito"):
        raise ValueError("Método de pago no válido.")
    limite = presupuesto["puede_gastar_hasta"]
    despues = limite - monto
    estado = "si" if monto <= limite else "no"
    return {**presupuesto, "estado": estado, "titulo": "Sí, pueden hacerlo" if estado == "si" else "Mejor no por ahora",
            "monto": monto, "despues": max(despues, 0), "deficit": max(-despues, 0),
            "metodo_recomendado": "Débito o efectivo: no aumenta la deuda bancaria.", "tarjeta": None,
            "impacto_liquidez_hoy": monto, "obligacion_futura": 0,
            "mensaje": "Después de esta salida siguen cubiertos los compromisos y el margen de seguridad registrados. Siguiente paso: registren la compra y mantengan intacto el margen restante." if estado == "si" else "La salida pondría en riesgo el margen reservado para compromisos ya registrados. Siguiente paso: esperen, reduzcan el monto o identifiquen qué gasto pueden reemplazar sin afectar sus compromisos.",
             "advertencias": advertencias}


def responder_compra_inteligente(monto: int, concepto: str = "compra", urgencia: str = "normal", *,
                                 mes: str | None = None, persona_pregunta: str | None = None,
                                 responsabilidad: str | None = None, usar_tarjeta: int | None = None) -> dict[str, Any]:
    """Traduce una simulación de compra a una respuesta corta y trazable."""
    if urgencia not in {"ahora", "normal", "puede_esperar"}:
        raise ValueError("La urgencia debe ser ahora, normal o puede_esperar.")
    period = mes or dt.date.today().strftime("%Y-%m")
    scenario = engine.simular_compra(monto, period, concepto, quien_paga=persona_pregunta,
                                     responsabilidad=responsabilidad, usar_tarjeta=usar_tarjeta)
    alternative = next((item for item in scenario["alternativas"] if item["tipo"] == "esperar_ingreso"), None)
    if urgencia != "ahora" and alternative and not scenario["posible"]:
        quick = "🟠 Yo esperaría"
        explanation = f"Hoy no conviene: {alternative['opcion'].lower()}. {alternative['razon']}"
    elif urgencia == "puede_esperar" and alternative and scenario["recomendacion"] != "🟢 Puedes hacerlo":
        quick = "🟠 Yo esperaría"
        explanation = f"Es viable, pero esperar mejora el margen. {alternative['razon']}"
    else:
        quick = scenario["recomendacion"]
        after = scenario["estado_despues"]
        explanation = f"Después de {concepto}, la caja libre sería {_cop(after['caja_disponible'])} y el flujo del mes {_cop(after['ahorro_mensual'])}."
    warnings = scenario["consecuencias"][:2]
    change = "La recomendación mejoraría si aumentas el margen con un ingreso confirmado o el recorte calculado en alternativas."
    if scenario["impacto_tarjeta"]:
        card = scenario["impacto_tarjeta"]
        change = f"La recomendación mejoraría si eliges otra fuente de pago o bajas el uso de {card['tarjeta_nombre']} antes de comprar."
    return {"respuesta_rapida": quick, "explicacion_breve": explanation, "advertencias": warnings,
            "cambio_recomendacion": change, "siguiente_paso": "Confirma el método de pago y la responsabilidad antes de registrar una compra real.",
            "scenario_completo": scenario}


def simular_priorizacion(mes: str, presupuesto: int, diferir: list[int],
                          priorizar: list[int] | None = None) -> dict[str, Any]:
    """Presenta en lenguaje corto una priorización manual, sin registrar pagos."""
    escenario = engine.simular_priorizacion_tarjetas(mes, presupuesto, diferir, priorizar)
    diferidas = escenario["tarjetas_diferidas"]
    pagadas = escenario["tarjetas_pagadas"]
    nombres_diferidos = ", ".join(tarjeta["tarjeta_nombre"] for tarjeta in diferidas) or "ninguna tarjeta"
    pagos_extra = [tarjeta for tarjeta in pagadas if tarjeta["pago_extra_asignado"]]
    if not escenario["posible"]:
        respuesta = (f"No alcanza para cubrir los mínimos de las tarjetas no diferidas: faltan "
                     f"{_cop(escenario['deficit_minimos'])}. No supongo cómo cada banco trataría un pago parcial.")
        confianza = "Media"
    else:
        destino = ", ".join(tarjeta["tarjeta_nombre"] for tarjeta in pagos_extra)
        respuesta = (f"Se pueden cubrir los mínimos de las tarjetas no diferidas y diferir {nombres_diferidos}. "
                     f"El excedente se concentra en {destino or 'las tarjetas priorizadas no tienen saldo pendiente'}.")
        confianza = "Media" if diferidas else "Alta"
    explicacion = (f"Presupuesto usado: {_cop(escenario['presupuesto_usado'])}; sobrante: "
                   f"{_cop(escenario['presupuesto_sobrante'])}. "
                   f"Interés estimado de las tarjetas diferidas: "
                   f"{_cop(escenario['interes_total_generado_tarjetas_diferidas'])}.")
    return {"respuesta_rapida": "🟠 Requiere revisión con el banco" if diferidas else "🟢 Plan de pagos simulado",
            "explicacion_breve": explicacion, "respuesta": respuesta, "confianza": confianza,
            "advertencias": escenario["consecuencias"],
            "siguiente_paso": "Confirmen las condiciones de mora y pago mínimo con cada banco antes de ejecutar un pago real.",
            "datos": escenario, "scenario_completo": escenario}


def _responder_pregunta_basica(mes: str, pregunta: str, *, monto: int | None = None) -> dict[str, Any]:
    """Respuestas deterministas heredadas; se conservan como red de seguridad."""
    texto = " ".join((pregunta or "").casefold().split())
    if not texto:
        raise ValueError("Escribe una pregunta para el asesor.")
    intencion_diferir_tarjetas = "tarjeta" in texto and any(expresion in texto for expresion in (
        "diferir", "aplazar", "posponer", "no pagar",
    ))
    if intencion_diferir_tarjetas:
        return {"respuesta": "Puedo simularlo. Dime qué tarjeta(s) quieres diferir este mes y cuál(es) priorizar con el presupuesto disponible (usa el nombre o ID de cada tarjeta).",
                "confianza": "Media", "datos": {"requiere": ["diferir", "priorizar", "presupuesto_disponible"]}}
    analisis = analizar_finanzas(mes)
    balance = analisis["estado_financiero"]["couple_historical"]
    if "cuánto le debo a sara" in texto or "cuanto le debo a sara" in texto:
        monto_deuda = max(int(balance["balance_neto_persona2"]), 0)
        respuesta = f"Le debes {_cop(monto_deuda)} a Sara según los aportes y responsabilidades activos." if monto_deuda else "Con los registros activos no aparece un saldo pendiente de Samuel hacia Sara."
        return {"respuesta": respuesta, "confianza": "Alta", "datos": {"monto": monto_deuda, "persona": "Sara"}}
    if "cuánto me debe samuel" in texto or "cuanto me debe samuel" in texto:
        monto_deuda = max(int(balance["balance_neto_persona2"]), 0)
        respuesta = f"Samuel te debe {_cop(monto_deuda)} según los aportes y responsabilidades activos." if monto_deuda else "Con los registros activos no aparece un saldo pendiente de Samuel hacia Sara."
        return {"respuesta": respuesta, "confianza": "Alta", "datos": {"monto": monto_deuda, "persona": "Samuel"}}
    if "cuánto puedo ahorrar" in texto or "cuanto puedo ahorrar" in texto:
        capacidad = max(int(analisis["flujo"]["ahorro"]), 0)
        return {"respuesta": f"Con los movimientos de este mes, pueden destinar hasta {_cop(capacidad)} sin contar dinero que aún no esté registrado.",
                "confianza": analisis["confianza"]["nivel"], "datos": {"capacidad": capacidad}}
    if "ahorrando demasiado" in texto:
        fatiga = analisis["fatiga_ahorro"]
        respuesta = "Sí, el ritmo de ahorro está dejando poco margen operativo." if fatiga["activa"] else "No veo una señal de ahorro excesivo con los datos registrados."
        return {"respuesta": respuesta, "confianza": "Media", "datos": fatiga}
    if "qué pagar primero" in texto or "que pagar primero" in texto:
        cards = [card for card in calc.estrategias_tarjetas()["avalancha"] if card["saldo_deuda"]]
        if not cards: return {"respuesta": "No hay tarjetas activas con deuda registrada para priorizar.", "confianza": "Alta", "datos": {}}
        card = cards[0]
        return {"respuesta": f"Después de los mínimos, prioricen {card['nombre']}: tiene la mayor tasa mensual registrada ({card['interes_mensual']:.2f}%).",
                "confianza": "Alta" if card["interes_mensual"] else "Baja", "datos": {"tarjeta_id": card["id"]}}
    if "cuándo salimos de las tarjetas" in texto or "cuando salimos de las tarjetas" in texto:
        projection = analisis["proyeccion_deuda_unificada"]
        if projection["viable"]:
            return {"respuesta": f"Con {_cop(projection['monthly_budget'])} al mes y estrategia avalancha, la proyección termina cerca de {projection['debt_free_date']}. No es una fecha garantizada.",
                    "confianza": projection["confidence"], "datos": projection}
        return {"respuesta": "No puedo estimar una fecha responsable: el excedente mensual registrado no alcanza los mínimos de tarjeta.", "confianza": "Baja", "datos": projection}
    if "podemos comprar" in texto or "puedo comprar" in texto:
        if monto is None:
            return {"respuesta": "Dime el monto y el método de pago para evaluarlo sin registrar nada.", "confianza": "Media", "datos": {}}
        decision = evaluar_gasto(mes, monto)
        return {"respuesta": decision["mensaje"], "confianza": decision["confianza"], "datos": decision}
    return {"respuesta": "Puedo responder sobre compras, ahorro, deudas entre ustedes, tarjetas y prioridad de pagos. Formula una de esas preguntas con los datos disponibles.",
            "confianza": "Baja", "datos": {}}


# ---------------------------------------------------------------------------
# Capa inteligente del asesor
# ---------------------------------------------------------------------------
# Las funciones siguientes exponen ``advisor_intelligence`` con el contrato que
# consume la capa de servicio. Nada aquí escribe en SQLite.

def perfil_financiero(mes: str) -> dict[str, Any]:
    """Foto interpretada del mes: ingresos, gastos, tarjetas, deuda, ahorro, pareja y flujo."""
    return iq.perfil_financiero(mes)


def estado_financiero(mes: str) -> dict[str, Any]:
    """Lectura general del momento financiero con hallazgos y dimensiones de salud."""
    return iq.estado_actual(mes)


def hallazgos(mes: str) -> list[dict[str, Any]]:
    """Hallazgos priorizados con estructura qué / por qué / acción / impacto."""
    return iq.generar_hallazgos(mes)


def dimensiones_salud(mes: str) -> dict[str, Any]:
    """Siete dimensiones explicables; nunca un puntaje único que oculte el detalle."""
    return iq.dimensiones_salud(mes)


def anomalias(mes: str) -> list[dict[str, Any]]:
    """Posibles anomalías del mes, siempre presentadas como señal y no como error."""
    return iq.detectar_anomalias(mes)


def detectar_gastos_fijos(mes: str | None = None, *, incluir_registrados: bool = False) -> dict[str, Any]:
    """Candidatos a gasto fijo detectados en el historial. No registra nada."""
    return iq.detectar_gastos_fijos(mes, incluir_registrados=incluir_registrados)


def podria_ser_gasto_fijo(descripcion: str, *, monto: int | None = None, mes: str | None = None,
                          categoria: str | None = None) -> dict[str, Any]:
    """«¿Esto podría ser un gasto fijo?» con respuesta SÍ / NO / PROBABLE y evidencia."""
    return iq.analizar_posible_gasto_fijo(descripcion, monto=monto, categoria=categoria, mes=mes)


def capacidad_discrecional(mes: str, margen_porcentaje: int | None = None) -> dict[str, Any]:
    """Cuánto pueden gastar hoy sin tocar compromisos ya registrados."""
    return iq.capacidad_discrecional(mes, margen_porcentaje=margen_porcentaje)


def evaluar_asequibilidad(mes: str, monto: int | None = None, *, concepto: str = "esta salida",
                          margen_porcentaje: int | None = None) -> dict[str, Any]:
    """Veredicto de asequibilidad: sí / al límite / no, con qué tendría que cambiar."""
    return iq.evaluar_asequibilidad(mes, monto, concepto=concepto, margen_porcentaje=margen_porcentaje)


def opciones_de_pago(mes: str, monto: int, *, concepto: str = "esta compra") -> dict[str, Any]:
    """Compara efectivo/débito, cada tarjeta viable y esperar, con sus consecuencias."""
    return iq.opciones_de_pago(mes, monto, concepto=concepto)


def simular_escenario_detallado(mes: str, tipo: str, monto: int, *, tarjeta_id: int | None = None) -> dict[str, Any]:
    """Escenario con antes/después en liquidez, deuda, ahorro, utilización y flujo."""
    return iq.simular_escenario_detallado(mes, tipo, monto, tarjeta_id=tarjeta_id)


def comparar_meses(mes: str, referencia: str | None = None) -> dict[str, Any]:
    """Comparación mes contra mes con detalle por categoría."""
    return iq.comparar_meses(mes, referencia)


def tendencias(mes: str, meses: int = 6) -> dict[str, Any]:
    """Series recientes para distinguir tendencia de mes puntual."""
    return iq.tendencias(mes, meses)


def analisis_pareja(mes: str) -> dict[str, Any]:
    """Aportes, consumo, desbalance y saldo entre Samuel y Sara."""
    return iq.analisis_pareja(mes)


def comparar_destinos(mes: str, destinos: list[dict[str, Any]] | None = None, *,
                      meses_para_viajar: int | None = None) -> dict[str, Any]:
    """Marco de comparación de viajes con datos entregados; no inventa precios."""
    return iq.comparar_destinos(mes, destinos or [], meses_para_viajar=meses_para_viajar)


def plantilla_viaje() -> dict[str, Any]:
    """Qué datos se necesitan para evaluar o comparar un viaje."""
    return iq.plantilla_viaje()


def informe_integridad(mes: str | None = None) -> dict[str, Any]:
    """Estado de integridad del libro, incluido el descuadre histórico si existe."""
    return iq.informe_integridad(mes)


def explicar_finanzas_simple(mes: str) -> dict[str, Any]:
    """Explicación en lenguaje llano del mes, sin tecnicismos."""
    return iq.explicar_finanzas_simple(mes)


def resumen_ejecutivo(mes: str) -> str:
    """Párrafo corto con lo que de verdad importa este mes."""
    return iq.resumen_ejecutivo(mes)


def clasificar_pregunta(pregunta: str) -> dict[str, Any]:
    """Intención detectada y entidades extraídas de una pregunta en lenguaje natural."""
    return iq.clasificar_intencion(pregunta)


def responder_pregunta(mes: str, pregunta: str, *, monto: int | None = None) -> dict[str, Any]:
    """Asesor conversacional: clasifica la intención y responde con datos reales.

    Si el motor no reconoce la intención, cae a las respuestas deterministas
    heredadas antes de declarar que no entendió.
    """
    resultado = iq.responder(mes, pregunta, monto=monto)
    if resultado["intencion"] == "unknown":
        heredado = _responder_pregunta_basica(mes, pregunta, monto=monto)
        if heredado["confianza"] != "Baja":
            return {**heredado, "intencion": "unknown_legacy", "preguntas_seguimiento": []}
    return resultado


# ---------------------------------------------------------------------------
# Capa inteligente ampliada
# ---------------------------------------------------------------------------

def contexto(mes: str, *, refrescar: bool = False) -> Any:
    """Contexto financiero canónico del mes (secciones perezosas y cacheadas)."""
    return iq.contexto_de(mes, refrescar=refrescar)


def invalidar_contexto(mes: str | None = None) -> None:
    """Descarta la foto cacheada tras registrar movimientos reales."""
    iq.invalidar_contexto(mes)


def plan_de_accion(mes: str) -> dict[str, Any]:
    """«¿Qué deberíamos hacer?» priorizado por horizonte temporal."""
    return iq.plan_de_accion(mes)


def riesgo_financiero(mes: str) -> dict[str, Any]:
    """Modelo de riesgo multifactor con las dimensiones separadas."""
    return iq.riesgo_financiero(mes)


def oportunidades(mes: str) -> list[dict[str, Any]]:
    """Oportunidades detectadas a partir de los datos registrados."""
    return iq.oportunidades(mes)


def hallazgos_agrupados(mes: str) -> list[dict[str, Any]]:
    """Hallazgos con los que obligan a la misma decisión reunidos en bloques."""
    return iq.contexto_de(mes).seccion("grupos")


def cotejar_gastos_fijos(mes: str | None = None) -> dict[str, Any]:
    """Compara los gastos fijos registrados contra los cobros reales."""
    return iq.cotejar_gastos_fijos(mes)


def inteligencia_tarjetas(mes: str) -> dict[str, Any]:
    """Señales de comportamiento por tarjeta (uso, crecimiento, mínimos, datos)."""
    return iq.inteligencia_tarjetas(mes)


def comparar_tarjetas(mes: str) -> dict[str, Any]:
    """Comparación entre tarjetas para decidir cuál usar y cuál atacar."""
    return iq.comparar_tarjetas(mes)


def inteligencia_ahorro(mes: str) -> dict[str, Any]:
    """Estado de cajitas y metas con sus señales (atrasos, conflictos, fatiga)."""
    return iq.inteligencia_ahorro(mes)


def explicacion_estructurada(mes: str) -> dict[str, Any]:
    """Narrativa financiera completa en nueve bloques."""
    return iq.explicacion_estructurada(mes)


def nueva_sesion(mes: str) -> Any:
    """Sesión conversacional en memoria para resolver preguntas de seguimiento."""
    return iq.nueva_sesion(mes)


def conversar(mes: str, pregunta: str, *, monto: int | None = None, sesion: Any = None) -> dict[str, Any]:
    """Responde manteniendo el hilo de la conversación cuando se pasa ``sesion``."""
    return iq.responder(mes, pregunta, monto=monto, sesion=sesion)
