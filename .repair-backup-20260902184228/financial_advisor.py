"""Asesor offline, determinístico y explicable de LÚMINA."""
from __future__ import annotations

from typing import Any

import calculations as calc
import database as db
from constants import PRIORIDAD_DISCRECIONAL


def _cop(valor: int) -> str:
    return calc.fmt_cop(max(valor, 0))


def _rec(tipo: str, prioridad: str, titulo: str, mensaje: str, datos: dict[str, Any], *,
         persona: str = "pareja", impacto: int = 0, confianza: str = "Media",
         tarjeta_id: int | None = None, cta: str | None = None, accion: str | None = None,
         explicacion: str | None = None, recomendacion: str | None = None) -> dict[str, Any]:
    """Contrato ampliado que conserva la clave ``mensaje`` del frontend actual."""
    detalle = explicacion or mensaje
    return {
        "tipo": tipo, "prioridad": prioridad, "persona": persona, "titulo": titulo,
        "mensaje": mensaje, "resumen": mensaje, "motivo": detalle,
        "impacto_estimado": impacto, "datos": datos, "confianza": confianza,
        "tarjeta_id": tarjeta_id, "cta": cta, "accion": accion,
        "explicacion": {"que_detecte": datos, "por_que_importa": detalle,
                        "que_recomiendo": recomendacion or mensaje, "impacto": impacto,
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
        significado = ("No significa necesariamente que exista un problema inmediato, pero reduce el margen para gastos imprevistos."
                       if estado in ("alta", "crítica") else
                       "La utilización todavía deja margen, aunque conviene vigilar que los pagos sigan cubriendo la nueva actividad.")
        accion = ("Evitar aumentar esta tarjeta antes de revisar un pago adicional." if estado in ("alta", "crítica")
                  else "Mantener registrados compras y pagos para conservar esta lectura actualizada.")
        resultado.append(_rec(
            "tarjeta", prioridad, f"{icono} {tarjeta['nombre']} está {estado}",
            f"Mi lectura es que esta tarjeta usa {uso} de su cupo: {_cop(tarjeta['saldo_deuda'])} de {_cop(tarjeta['cupo_total'])}. "
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
            f"El cambio neto es +{_cop(neto)}. Si este patrón continúa, la deuda seguirá creciendo aunque estén pagando.",
            {**base, "cambio_neto": neto}, impacto=neto, confianza="Alta", tarjeta_id=tarjeta["id"],
            cta="Registrar pago", accion="registrar_pago",
            recomendacion="Comparar un pago adicional con nuevas compras discrecionales antes del próximo corte."))
    elif actividad["pagos"] and neto < 0:
        resultado.append(_rec(
            "tendencia", "positiva", f"Señal positiva en {tarjeta['nombre']}",
            f"Los pagos registrados ({_cop(actividad['pagos'])}) superaron compras, intereses y cargos "
            f"({_cop(actividad['compras'] + actividad['intereses'] + actividad['cargos'])}). La deuda bajó {_cop(-neto)} en el período. "
            "Si mantienen este ritmo, van en la dirección correcta.",
            {**base, "reduccion_neta": -neto}, impacto=-neto, confianza="Alta", tarjeta_id=tarjeta["id"],
            cta="Ver historial", accion="ver_tarjeta"))
    if tarjeta["saldo_historico_pendiente"]:
        resultado.append(_rec(
            "datos", "info", f"Deuda histórica sin desglose en {tarjeta['nombre']}",
            f"Hay {_cop(tarjeta['saldo_historico_pendiente'])} registrados como saldo histórico. El modelo lo atribuye provisionalmente al titular legal para cuadrar el ledger, "
            "pero no afirma que esa persona haya realizado ese consumo. No puedo determinar el origen de esta parte de la deuda sin más detalle.",
            base, impacto=tarjeta["saldo_historico_pendiente"], confianza="Baja", tarjeta_id=tarjeta["id"],
            cta="Ver tarjeta", accion="ver_tarjeta"))
    if tarjeta["pagado_persona1"] or tarjeta["pagado_persona2"]:
        p1, p2 = tarjeta["pagado_persona1"], tarjeta["pagado_persona2"]
        r1, r2 = tarjeta["deuda_persona1"], tarjeta["deuda_persona2"]
        mayor = "Samuel" if p1 - r1 > p2 - r2 else "Sara"
        resultado.append(_rec(
            "pareja", "info", f"Aportes y responsabilidad en {tarjeta['nombre']}",
            f"Samuel ha aportado {_cop(p1)} y conserva {_cop(r1)} de responsabilidad pendiente; "
            f"Sara ha aportado {_cop(p2)} y conserva {_cop(r2)}. {mayor} está aportando una proporción mayor frente a la responsabilidad pendiente registrada. "
            "No significa que alguien esté haciendo algo mal: solo hace visible cómo se está distribuyendo el dinero.",
            {**base, "aporte_samuel": p1, "aporte_sara": p2, "deuda_samuel": r1, "deuda_sara": r2},
            confianza="Media", tarjeta_id=tarjeta["id"], cta="Revisar distribución", accion="ver_tarjeta"))
    return resultado


def analizar_finanzas(mes: str) -> dict[str, Any]:
    flujo = calc.flujo_caja_mes(mes)
    liquidez = calc.liquidez_total(mes)
    tarjetas = calc.resumen_tarjetas()
    metas = calc.progreso_metas()
    cajas = calc.resumen_ahorros()["fondos"]
    recs: list[dict[str, Any]] = []
    if not db.get_ingresos(mes):
        recs.append(_rec("datos", "info", "Información aún incompleta",
                         "No hay ingresos registrados este mes. Puedo leer deuda, pagos y reservas, pero no estimar con confianza cuánto flujo tienen disponible para acelerar una obligación.",
                         {"ingresos": 0}, confianza="Baja"))
    for tarjeta in tarjetas:
        recs.extend(_lectura_tarjeta(tarjeta, mes))
    con_deuda = [t for t in tarjetas if t["saldo_deuda"]]
    if len(con_deuda) > 1:
        estrategias = calc.estrategias_tarjetas()
        principal = estrategias["avalancha"][0]
        recs.append(_rec(
            "estrategia", "info", "Entre sus tarjetas, hay una prioridad financiera posible",
            f"Después de cubrir los mínimos, {principal['nombre']} tiene la mayor tasa mensual registrada ({principal['interes_mensual']:.2f}%). "
            "Una estrategia de avalancha la priorizaría para reducir el interés potencial; una estrategia snowball priorizaría el saldo más pequeño. Es una comparación, no una orden automática.",
            {"avalancha": [t["id"] for t in estrategias["avalancha"]], "snowball": [t["id"] for t in estrategias["snowball"]]},
            confianza="Alta" if principal["interes_mensual"] else "Baja", tarjeta_id=principal["id"],
            cta="Comparar tarjetas", accion="ver_tarjetas"))
    for meta in metas:
        if meta["atrasada"]:
            deficit = int(meta["ahorro_necesario_mensual"] - meta["ahorro_promedio_mensual"])
            recs.append(_rec("meta", "alta" if meta["prioridad"] != PRIORIDAD_DISCRECIONAL else "media",
                             f"{meta['nombre']} necesita atención",
                             f"Faltan {_cop(meta['faltante'])} y el ritmo registrado está por debajo de lo necesario para su fecha objetivo. "
                             "Antes de decidir, compararía ese aporte con cualquier tarjeta que tenga interés alto.",
                             {"faltante": meta["faltante"], "deficit": deficit}, impacto=deficit, confianza="Media"))
        elif meta["cumplida"]:
            recs.append(_rec("meta", "positiva", f"Meta alcanzada: {meta['nombre']}",
                             "Hay una señal positiva: el dinero reservado ya cubre el objetivo definido.",
                             {"actual": meta["actual"], "objetivo": meta["monto_objetivo"]}, confianza="Alta"))
    for caja in cajas:
        if caja["meta"] and caja["saldo"] < caja["meta"] * .25:
            recs.append(_rec("cajita", "media", f"Reserva baja: {caja['nombre']}",
                             f"La cajita tiene {_cop(caja['saldo'])} frente a una referencia de {_cop(caja['meta'])}. "
                             "No retiraría dinero automáticamente; es una señal para comparar reservas y obligaciones antes de moverlo.",
                             {"saldo": caja["saldo"], "referencia": caja["meta"]}, impacto=caja["meta"] - caja["saldo"]))
    gastos_disc = sum(g["valor"] for g in db.get_gastos(mes) if g["prioridad"] == PRIORIDAD_DISCRECIONAL)
    if gastos_disc and flujo["ahorro"] <= 0:
        recs.append(_rec("recorte", "media", "Un lugar opcional para revisar",
                         f"Hay {_cop(gastos_disc)} de gasto discrecional registrado y el flujo mensual no deja ahorro. "
                         "No es una instrucción de recorte; si quieren acelerar una tarjeta, este es uno de los escenarios que podrían comparar.",
                         {"gasto_discrecional": gastos_disc, "ahorro_flujo": flujo["ahorro"]}, impacto=gastos_disc))
    if not recs:
        recs.append(_rec("estable", "positiva", "Con los datos registrados, todo está estable",
                         "El flujo es positivo, no hay tarjetas sobre la señal interna de utilización alta y no veo una alerta fuerte. "
                         "Sigan registrando movimientos para conservar esta lectura.", {"ahorro": flujo["ahorro"]}, confianza="Media"))
    foco = max(con_deuda, key=lambda t: (t["utilizacion"], t["interes_mensual"]), default=None)
    resumen = (f"Mi lectura de este mes: la liquidez registrada es {_cop(liquidez)} y la principal atención está en {foco['nombre']}, que usa {foco['utilizacion']:.0%} de su cupo."
               if foco else f"Mi lectura de este mes: tienen ingresos por {_cop(flujo['ingresos'])}, salidas por {_cop(flujo['salidas'])} y un flujo de {_cop(flujo['ahorro'])}. No hay deuda de tarjeta vigente registrada.")
    plan = ["Cubrir los mínimos registrados."] if con_deuda else ["Mantener los movimientos al día."]
    if foco and foco["utilizacion"] >= .70:
        plan.append(f"Evitar aumentar {foco['nombre']} antes de revisar un pago adicional.")
    if not db.get_ingresos(mes):
        plan.append("Registrar ingresos para que la capacidad de pago tenga mejor confianza.")
    return {"mes": mes, "resumen": resumen, "recomendaciones": recs, "flujo": flujo, "liquidez": liquidez,
            "metas": metas, "salud_tarjetas": [{**t, "estado_salud": _estado_tarjeta(t)[0]} for t in tarjetas],
            "plan_accion": plan}


def simular(mes: str, ajuste_discrecional: int = 0, aporte_meta: int = 0) -> dict[str, Any]:
    """Escenario puro: no escribe en SQLite."""
    base = analizar_finanzas(mes)
    capacidad = max(base["flujo"]["ahorro"] + ajuste_discrecional - aporte_meta, 0)
    return {"modifica_base": False, "capacidad_ahorro": capacidad, "cambio_flujo": ajuste_discrecional - aporte_meta,
            "mensaje": f"La simulación dejaría aproximadamente {_cop(capacidad)} disponibles para reservas después de los ajustes."}


def presupuesto_seguro(mes: str, margen_porcentaje: int = 10) -> dict[str, Any]:
    if not isinstance(margen_porcentaje, int) or not 0 <= margen_porcentaje <= 50:
        raise ValueError("El margen de seguridad debe estar entre 0% y 50%.")
    flujo = calc.flujo_caja_mes(mes)
    patrimonio = calc.patrimonio_liquido()
    tarjetas = calc.resumen_tarjetas()
    metas = calc.progreso_metas()
    pagos_minimos = sum(min(t["pago_minimo"], t["saldo_deuda"]) for t in tarjetas)
    metas_prioritarias = sum(int(m["ahorro_necesario_mensual"] or 0) for m in metas if m["prioridad"] != PRIORIDAD_DISCRECIONAL and not m["cumplida"])
    margen = round(flujo["ingresos"] * margen_porcentaje / 100)
    base = patrimonio["disponible_gastos_recurrentes"]
    seguro = max(base - pagos_minimos - metas_prioritarias - margen, 0)
    return {"mes": mes, "liquidez_registrada": base, "pagos_minimos_tarjetas": pagos_minimos,
            "metas_prioritarias": metas_prioritarias, "margen_seguridad": margen, "margen_porcentaje": margen_porcentaje,
            "puede_gastar_hasta": seguro, "confianza": "Media" if flujo["ingresos"] else "Baja",
            "nota": "Solo considera ingresos, gastos, reservas, tarjetas y metas registrados."}


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
                    "despues": presupuesto["puede_gastar_hasta"], "tarjeta": tarjeta, "mensaje": "El monto supera el cupo disponible de la tarjeta seleccionada.",
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
                "mensaje": f"Esta compra no reduce su liquidez hoy, pero aumentaría la deuda de {tarjeta['nombre']} de {_cop(tarjeta['saldo_deuda'])} a {_cop(tarjeta['saldo_deuda'] + monto)}.",
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
            "mensaje": "Después de esta salida siguen cubiertos los compromisos y el margen de seguridad registrados." if estado == "si" else "La salida pondría en riesgo el margen reservado para compromisos ya registrados.",
            "advertencias": advertencias}
