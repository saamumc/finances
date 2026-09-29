"""Adaptador de lectura/escritura entre la interfaz y el dominio financiero."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import calculations as calc
import database as db
import financial_advisor
from constants import (
    METODO_DEBITO, METODO_EFECTIVO, METODO_TARJETA,
    PERSONA1, PERSONA2, PRIORIDAD_DISCRECIONAL, PRIORIDAD_OBLIGATORIO,
    RESP_COMPARTIDO, RESP_P1, RESP_P2, to_pesos,
)

NOMBRES = {PERSONA1: "Samuel", PERSONA2: "Sara"}
# Estas tablas son el único puente entre textos de interfaz y el dominio.
# SQLite solo recibe códigos estables, nunca etiquetas de presentación.
METODOS_UI = {"Efectivo": METODO_EFECTIVO, "Débito": METODO_DEBITO,
              "Débito / transferencia": METODO_DEBITO, "Tarjeta": METODO_TARJETA,
              METODO_EFECTIVO: METODO_EFECTIVO, METODO_DEBITO: METODO_DEBITO, METODO_TARJETA: METODO_TARJETA}
RESPONSABILIDADES_UI = {"Samuel": RESP_P1, "Samuel 100%": RESP_P1, "Yo 100%": RESP_P1,
                        "Sara": RESP_P2, "Sara 100%": RESP_P2, "Compartido": RESP_COMPARTIDO,
                        "Personalizado": RESP_COMPARTIDO, RESP_P1: RESP_P1, RESP_P2: RESP_P2,
                        RESP_COMPARTIDO: RESP_COMPARTIDO}
PRIORIDADES_UI = {"Obligatorio": PRIORIDAD_OBLIGATORIO, "Discrecional": PRIORIDAD_DISCRECIONAL}
PREFERENCES_PATH = Path(__file__).resolve().parent.parent / "ui_preferences.json"


def hoy() -> str:
    return dt.date.today().isoformat()


def mes_actual() -> str:
    return hoy()[:7]


def nombre(persona: str) -> str:
    return NOMBRES.get(persona, persona)


def persona_dominio(valor: str) -> str:
    aliases = {"Samuel": PERSONA1, "Yo": PERSONA1, PERSONA1: PERSONA1,
               "Sara": PERSONA2, PERSONA2: PERSONA2}
    try:
        return aliases[valor]
    except KeyError:
        raise ValueError("Selecciona Samuel o Sara.") from None


def metodo_dominio(valor: str) -> str:
    try:
        return METODOS_UI[valor]
    except KeyError:
        raise ValueError("Selecciona un método de pago válido.") from None


def responsabilidad_dominio(valor: str) -> str:
    try:
        return RESPONSABILIDADES_UI[valor]
    except KeyError:
        raise ValueError("Selecciona una responsabilidad válida.") from None


def dinero(valor: object) -> str:
    monto = to_pesos(valor)
    signo = "-" if monto < 0 else ""
    return f"{signo}${abs(monto):,}".replace(",", ".")


def parsear_dinero(texto: str) -> int:
    if not isinstance(texto, str):
        raise ValueError("El monto debe ser texto.")
    limpio = texto.strip().replace("$", "").replace(" ", "")
    # COP se registra en pesos enteros: se permiten separadores de miles,
    # pero se rechazan formatos ambiguos como 1.234,56.
    if not limpio or not limpio.replace(".", "").replace(",", "").lstrip("-").isdigit():
        raise ValueError("Ingresa un monto entero en pesos, por ejemplo 125000.")
    if "." in limpio and "," in limpio:
        raise ValueError("El monto no puede incluir decimales; usa pesos enteros.")
    limpio = limpio.replace(".", "").replace(",", "")
    return to_pesos(limpio)


def cargar_preferencias() -> dict[str, str]:
    try:
        return json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tema": "dark"}


def guardar_preferencias(preferencias: dict[str, str]) -> None:
    temporal = PREFERENCES_PATH.with_suffix(".tmp")
    temporal.write_text(json.dumps(preferencias, indent=2), encoding="utf-8")
    os.replace(temporal, PREFERENCES_PATH)


class FinanceService:
    """Facade fina: prepara datos de UI sin duplicar cálculos del dominio."""

    def __init__(self) -> None:
        db.init_db()

    def dashboard(self, mes: str) -> dict[str, Any]:
        flujo = calc.flujo_caja_mes(mes)
        liquidez = calc.liquidez_por_persona(mes)
        tarjetas = calc.resumen_tarjetas()
        balance = calc.balance_historico_pareja(mes)
        terceros = calc.resumen_deudas_terceros()
        patrimonio = calc.patrimonio_liquido()
        total_directo = sum(d["gastos_directos"] for d in liquidez.values())
        total_pagos = sum(d["pagos_tarjeta_aportados"] for d in liquidez.values())
        alertas: list[tuple[str, str]] = []
        for tarjeta in tarjetas:
            if tarjeta["utilizacion"] >= 0.70:
                alertas.append(("warning", f"{tarjeta['nombre']} usa {tarjeta['utilizacion']:.0%} de su cupo."))
        for tarjeta in db.get_tarjetas():
            if tarjeta["saldo_historico_pendiente"]:
                alertas.append(("info", f"{tarjeta['nombre']} conserva deuda histórica pendiente."))
        for tarjeta in tarjetas:
            if tarjeta["saldo_deuda"] and tarjeta["interes_estimado"]:
                alertas.append(("warning", f"{tarjeta['nombre']} podría generar {dinero(tarjeta['interes_estimado'])} de interés este mes."))
        for presupuesto in calc.resumen_presupuestos(mes)["presupuestos"]:
            if presupuesto["gastado"] > presupuesto["monto"]:
                alertas.append(("warning", f"{presupuesto['categoria']} excede su presupuesto por {dinero(presupuesto['gastado'] - presupuesto['monto'])}."))
        if not alertas:
            alertas.append(("success", "No hay alertas financieras activas."))
        return {"flujo": flujo, "liquidez": liquidez, "tarjetas": tarjetas, "balance": balance,
                "terceros": terceros, "patrimonio": patrimonio, "metas": calc.progreso_metas(), "cajitas": calc.resumen_ahorros(),
                "gastos_directos": total_directo, "pagos_tarjeta": total_pagos, "alertas": alertas}

    def ingresos(self, mes: str | None = None, reversados: bool = False) -> list[dict]:
        return db.get_ingresos(mes, reversados)

    def gastos(self, mes: str | None = None, reversados: bool = False) -> list[dict]:
        return db.get_gastos(mes, reversados)

    def tarjetas(self) -> list[dict]:
        return [{**tarjeta, **calc.resumen_tarjeta(tarjeta)} for tarjeta in db.get_tarjetas()]

    def resumen_tarjetas(self, mes: str) -> dict[str, Any]:
        tarjetas = self.tarjetas()
        total_cupo = sum(t["cupo_total"] for t in tarjetas)
        total_deuda = sum(t["saldo_deuda"] for t in tarjetas)
        pagos = sum(t["total_pagado"] for t in tarjetas)
        intereses = sum(t["intereses_registrados"] for t in tarjetas)
        mensual = {"compras": 0, "pagos": 0, "intereses": 0, "cargos": 0, "variacion": 0}
        for tarjeta in tarjetas:
            actividad = calc.resumen_mensual_tarjeta(tarjeta["id"], mes)
            for clave in mensual:
                mensual[clave] += int(actividad[clave])
        return {"tarjetas": tarjetas, "deuda_total": total_deuda, "cupo_total": total_cupo,
                "cupo_disponible": total_cupo - total_deuda,
                "utilizacion_global": total_deuda / total_cupo if total_cupo else 0.0,
                "pagos_realizados": pagos, "intereses_registrados": intereses,
                "deuda_persona1": sum(t["deuda_persona1"] for t in tarjetas),
                "deuda_persona2": sum(t["deuda_persona2"] for t in tarjetas),
                "pago_minimo_total": sum(min(t["pago_minimo"], t["saldo_deuda"]) for t in tarjetas),
                "mensual": mensual, "estrategias": calc.estrategias_tarjetas()}

    def tarjeta_detalle(self, tarjeta_id: int) -> dict[str, Any]:
        tarjeta = next((t for t in db.get_tarjetas() if t["id"] == tarjeta_id), None)
        if tarjeta is None:
            raise ValueError("Tarjeta no encontrada.")
        compras = db.get_compras_tarjeta(tarjeta_id)
        conocida = sum(c["valor_pendiente"] for c in compras)
        pagos = [p for p in db.get_pagos_deuda(incluir_reversados=True) if p["tarjeta_id"] == tarjeta_id]
        for pago in pagos:
            pago["asignaciones"] = db.get_asignaciones_pago(pago["id"])
            pago["asignaciones_ajustes"] = db.get_asignaciones_pago_ajustes(pago["id"])
        return {"tarjeta": {**tarjeta, **calc.resumen_tarjeta(tarjeta)}, "compras": compras,
                "deuda_compras": conocida, "deuda_historica": tarjeta["saldo_historico_pendiente"],
                "pagos": pagos, "historial": calc.historial_tarjeta(tarjeta_id),
                "mensual": calc.resumen_mensual_tarjeta(tarjeta_id, mes_actual()),
                "tendencia": calc.tendencia_tarjeta(tarjeta_id),
                "responsabilidad": calc.deuda_pendiente_por_responsabilidad(tarjeta_id),
                "ajustes": db.get_ajustes_tarjeta(tarjeta_id, incluir_reversados=True)}

    def crear_ingreso(self, mes: str, persona: str, concepto: str, valor: str) -> int:
        return db.registrar_ingreso(mes, persona, concepto, parsear_dinero(valor), str(uuid.uuid4()))

    def crear_tarjeta(self, nombre_tarjeta: str, propietario: str, cupo: str, minimo: str,
                       interes: str, historico: str, fecha: str | None, **extra: str) -> int:
        return db.registrar_tarjeta(nombre_tarjeta, persona_dominio(propietario), parsear_dinero(cupo),
                                    pago_minimo=parsear_dinero(minimo or "0"),
                                    interes_mensual=self._parsear_porcentaje(interes),
                                    saldo_inicial_historico=parsear_dinero(historico or "0"),
                                    fecha_saldo_inicial=fecha or None, banco=extra.get("banco") or None,
                                    tipo=extra.get("tipo") or None, ultimos_4=extra.get("ultimos_4") or None,
                                    fecha_corte=extra.get("fecha_corte") or None, fecha_pago=extra.get("fecha_pago") or None,
                                    notas=extra.get("notas") or None, color=extra.get("color") or None)

    def editar_tarjeta(self, tarjeta_id: int, datos: dict[str, str]) -> None:
        db.actualizar_tarjeta(
            tarjeta_id, nombre=datos["nombre"], propietario=persona_dominio(datos["propietario"]),
            cupo_total=parsear_dinero(datos["cupo"]), pago_minimo=parsear_dinero(datos.get("minimo", "0") or "0"),
            interes_mensual=self._parsear_porcentaje(datos.get("interes", "0")),
            activa=datos.get("activa", "Activa") in ("Activa", "1", "true", "True"),
            banco=datos.get("banco") or None, tipo=datos.get("tipo") or None, ultimos_4=datos.get("ultimos_4") or None,
            fecha_corte=datos.get("fecha_corte") or None, fecha_pago=datos.get("fecha_pago") or None,
            notas=datos.get("notas") or None, color=datos.get("color") or None,
        )

    def ajustar_saldo_tarjeta(self, tarjeta_id: int, nuevo_saldo: str, motivo: str, fecha: str, realizado_por: str) -> int:
        return db.ajustar_saldo_tarjeta(tarjeta_id, parsear_dinero(nuevo_saldo), motivo, fecha,
                                        persona_dominio(realizado_por), str(uuid.uuid4()))

    def crear_gasto(self, datos: dict[str, str]) -> int:
        metodo = metodo_dominio(datos["metodo"])
        responsabilidad = responsabilidad_dominio(datos["responsabilidad"])
        tarjeta_id = int(datos["tarjeta_id"]) if metodo == METODO_TARJETA else None
        return db.registrar_gasto(
            datos["mes"], datos["nombre"], datos["categoria"], parsear_dinero(datos["valor"]), datos["fecha"],
            metodo, persona_dominio(datos["pagador"]), responsabilidad, parsear_dinero(datos["monto_p1"]),
            parsear_dinero(datos["monto_p2"]), tarjeta_id, int(datos["cuotas"]),
            PRIORIDADES_UI[datos["prioridad"]], str(uuid.uuid4()),
        )

    def crear_pago_tarjeta(self, datos: dict[str, str]) -> int:
        return db.registrar_pago_tarjeta(datos["mes"], datos["fecha"], int(datos["tarjeta_id"]),
                                          parsear_dinero(datos["monto"]), persona_dominio(datos["pagador"]),
                                          parsear_dinero(datos["aporte_p1"]), parsear_dinero(datos["aporte_p2"]),
                                          str(uuid.uuid4()), concepto=datos.get("concepto") or None)

    def crear_movimiento_tarjeta(self, datos: dict[str, str]) -> int:
        tipo = datos["tipo"].upper()
        return db.registrar_movimiento_tarjeta(
            datos["mes"], datos["fecha"], int(datos["tarjeta_id"]), tipo, parsear_dinero(datos["monto"]),
            datos["descripcion"], responsabilidad_dominio(datos["responsabilidad"]),
            parsear_dinero(datos["monto_p1"]), parsear_dinero(datos["monto_p2"]), str(uuid.uuid4()),
            periodo=datos.get("periodo") or None,
        )

    def crear_liquidacion(self, datos: dict[str, str]) -> int:
        return db.registrar_liquidacion(datos["mes"], datos["fecha"], datos["deudor"], datos["acreedor"],
                                        parsear_dinero(datos["monto"]), datos["concepto"], str(uuid.uuid4()))

    def reversar(self, tipo: str, movimiento_id: int, motivo: str) -> None:
        acciones = {"ingreso": db.reversar_ingreso, "gasto": db.reversar_gasto,
                    "pago": db.reversar_pago_tarjeta, "liquidacion": db.reversar_liquidacion,
                    "ahorro": db.reversar_movimiento_ahorro, "movimiento_tarjeta": db.reversar_movimiento_tarjeta}
        acciones[tipo](movimiento_id, motivo)

    def movimientos(self, mes: str | None = None, incluir_reversados: bool = False) -> list[dict]:
        filas: list[dict] = []
        for ingreso in db.get_ingresos(mes, incluir_reversados):
            filas.append({"tipo": "ingreso", "id": ingreso["id"], "fecha": ingreso["mes"], "descripcion": ingreso["concepto"], "persona": nombre(ingreso["persona"]), "monto": ingreso["valor"], "estado": ingreso["estado"]})
        for gasto in db.get_gastos(mes, incluir_reversados):
            filas.append({"tipo": "gasto", "id": gasto["id"], "fecha": gasto["fecha"] or gasto["mes"], "descripcion": gasto["nombre"], "persona": nombre(gasto["pagador"]), "monto": gasto["valor"], "estado": gasto["estado"]})
        for pago in db.get_pagos_deuda(mes, incluir_reversados):
            tarjeta = next((t for t in db.get_tarjetas() if t["id"] == pago["tarjeta_id"]), None)
            filas.append({"tipo": "pago", "id": pago["id"], "fecha": pago["fecha"], "descripcion": f"Pago · {tarjeta['nombre'] if tarjeta else 'Tarjeta eliminada'}", "persona": nombre(pago["pagador"]), "monto": pago["monto"], "estado": pago["estado"]})
        for movimiento in db.get_compras_tarjeta(incluir_reversadas=incluir_reversados):
            if movimiento.get("tipo", "COMPRA") in ("INTERES", "CARGO"):
                filas.append({"tipo": movimiento["tipo"].lower(), "id": movimiento["id"], "fecha": movimiento["fecha"] or movimiento["mes"],
                              "descripcion": f"{movimiento['tipo'].title()} · {movimiento['descripcion']}", "persona": "Tarjeta",
                              "monto": movimiento["valor_original"], "estado": movimiento["estado"]})
        for liq in db.get_liquidaciones(mes, incluir_reversados):
            filas.append({"tipo": "liquidacion", "id": liq["id"], "fecha": liq["fecha"], "descripcion": f"{nombre(liq['deudor'])} → {nombre(liq['acreedor'])}", "persona": "Pareja", "monto": liq["monto"], "estado": liq["estado"]})
        fondos = {f["id"]: f for f in db.get_ahorros(solo_activos=False)}
        for movimiento in db.get_movimientos_ahorro(mes=mes, incluir_reversados=incluir_reversados):
            fondo = fondos.get(movimiento["ahorro_id"], {})
            accion = "Entrada" if movimiento["tipo"] == "DEPOSITO" else "Salida"
            filas.append({"tipo": "ahorro", "id": movimiento["id"], "fecha": movimiento["fecha"],
                          "descripcion": f"{accion} · {fondo.get('nombre', 'Cajita eliminada')} · {movimiento['concepto']}",
                          "persona": nombre(fondo.get("propietario", PERSONA1)), "monto": movimiento["monto"],
                          "estado": movimiento["estado"]})
        return sorted(filas, key=lambda f: (f["fecha"], f["id"]), reverse=True)

    def auditoria(self) -> list[dict]:
        return db.get_movimientos_log(limit=300)

    def explicacion(self, persona: str, mes: str | None = None) -> dict:
        return calc.explicar_balance(persona, mes)

    def ahorros(self) -> dict[str, Any]:
        return calc.resumen_ahorros()

    @staticmethod
    def _parsear_porcentaje(texto: str) -> float:
        try:
            valor = Decimal((texto or "0").strip().replace(",", "."))
        except (InvalidOperation, ValueError):
            raise ValueError("El interés mensual debe ser un porcentaje válido.") from None
        if not valor.is_finite():
            raise ValueError("El interés mensual debe ser finito.")
        return float(valor)

    def crear_ahorro(self, nombre_ahorro: str, propietario: str, descripcion: str, meta: str = "0") -> int:
        return db.crear_ahorro(nombre_ahorro, propietario, descripcion, parsear_dinero(meta or "0"))

    def crear_cajita(self, datos: dict[str, str]) -> int:
        titular = datos.get("titular", PERSONA1)
        propietario = PERSONA1 if titular == "compartido" else titular
        return db.crear_ahorro(datos["nombre"], propietario, datos.get("descripcion", ""), parsear_dinero(datos.get("meta", "0") or "0"),
                                titular=titular, tipo=datos.get("tipo", "Ahorro"), prioridad=datos.get("prioridad", PRIORIDAD_OBLIGATORIO),
                                icono=datos.get("icono", "💰"), color=datos.get("color", "#5B8DEF"), fecha_objetivo=datos.get("fecha_objetivo") or None)

    def movimiento_ahorro(self, datos: dict[str, str]) -> int:
        tipo = "DEPOSITO" if datos["tipo"] == "Depositar" else "RETIRO"
        return db.registrar_movimiento_ahorro(datos["mes"], datos["fecha"], int(datos["ahorro_id"]), tipo,
                                              parsear_dinero(datos["monto"]), datos["concepto"], str(uuid.uuid4()),
                                              aportante=datos.get("aportante", PERSONA1), gasto_id=int(datos["gasto_id"]) if datos.get("gasto_id") else None,
                                              motivo=datos.get("motivo"))

    def crear_meta(self, datos: dict[str, str]) -> int:
        return db.crear_meta(datos["nombre"], parsear_dinero(datos["monto_objetivo"]), descripcion=datos.get("descripcion"),
                             fecha_objetivo=datos.get("fecha_objetivo") or None, prioridad=datos.get("prioridad", PRIORIDAD_OBLIGATORIO),
                             ahorro_id=int(datos["ahorro_id"]) if datos.get("ahorro_id") else None, icono=datos.get("icono", "🎯"))

    def metas(self) -> list[dict]:
        return calc.progreso_metas()

    def asesor(self, mes: str) -> dict[str, Any]:
        return financial_advisor.analizar_finanzas(mes)

    def simulacion(self, mes: str, ajuste_discrecional: str = "0", aporte_meta: str = "0") -> dict[str, Any]:
        return financial_advisor.simular(mes, parsear_dinero(ajuste_discrecional or "0"), parsear_dinero(aporte_meta or "0"))

    def simular_pago_tarjeta(self, tarjeta_id: int, monto: str) -> dict[str, Any]:
        tarjeta = next((t for t in self.tarjetas() if t["id"] == tarjeta_id), None)
        if tarjeta is None:
            raise ValueError("Tarjeta no encontrada.")
        pago = parsear_dinero(monto)
        if pago <= 0 or pago > tarjeta["saldo_deuda"]:
            raise ValueError("El pago debe ser mayor que cero y no superar la deuda actual.")
        deuda_despues = tarjeta["saldo_deuda"] - pago
        uso_despues = deuda_despues / tarjeta["cupo_total"] if tarjeta["cupo_total"] else 0.0
        interes_despues = int((Decimal(deuda_despues) * Decimal(str(tarjeta["interes_mensual"])) / Decimal("100")).quantize(Decimal("1")))
        return {"modifica_base": False, "pago": pago, "deuda_actual": tarjeta["saldo_deuda"],
                "deuda_despues": deuda_despues, "utilizacion_actual": tarjeta["utilizacion"],
                "utilizacion_despues": uso_despues, "cupo_liberado": pago,
                "interes_mensual_estimado_antes": tarjeta["interes_estimado"],
                "interes_mensual_estimado_despues": interes_despues,
                "confianza": "Media" if tarjeta["interes_mensual"] else "Baja",
                "nota": "La comparación usa la tasa mensual registrada; no reemplaza el cálculo real del banco." if tarjeta["interes_mensual"] else "No puedo estimar intereses con precisión porque no hay una tasa mensual registrada."}

    def presupuesto_seguro(self, mes: str, margen_porcentaje: int = 10) -> dict[str, Any]:
        return financial_advisor.presupuesto_seguro(mes, margen_porcentaje)

    def evaluar_gasto(self, mes: str, monto: str, metodo: str, tarjeta_id: str | None = None,
                       margen_porcentaje: int = 10) -> dict[str, Any]:
        return financial_advisor.evaluar_gasto(
            mes, parsear_dinero(monto), metodo=metodo,
            tarjeta_id=int(tarjeta_id) if tarjeta_id else None, margen_porcentaje=margen_porcentaje,
        )

    def movimientos_ahorro(self, ahorro_id: int | None = None, mes: str | None = None,
                            reversados: bool = False) -> list[dict]:
        return db.get_movimientos_ahorro(ahorro_id, mes, reversados)

    def reversar_ahorro(self, movimiento_id: int, motivo: str) -> None:
        db.reversar_movimiento_ahorro(movimiento_id, motivo)

    def terceros(self) -> dict[str, Any]:
        return calc.resumen_deudas_terceros()

    def crear_prestamo_tercero(self, datos: dict[str, str]) -> int:
        return db.registrar_prestamo_tercero(datos["mes"], datos["fecha"], datos["tercero"], datos["tipo"],
                                             parsear_dinero(datos["monto"]), datos["propietario"], datos["concepto"],
                                             str(uuid.uuid4()))

    def crear_abono_tercero(self, datos: dict[str, str]) -> int:
        return db.registrar_abono_tercero(datos["mes"], datos["fecha"], int(datos["prestamo_id"]),
                                          parsear_dinero(datos["monto"]), str(uuid.uuid4()))

    def abonos_tercero(self, prestamo_id: int, reversados: bool = False) -> list[dict]:
        return db.get_abonos_tercero(prestamo_id, reversados)

    def categorias(self) -> list[dict]:
        return db.get_categorias()

    def crear_categoria(self, nombre_categoria: str) -> int:
        return db.crear_categoria(nombre_categoria)

    def presupuestos(self, mes: str) -> dict[str, Any]:
        return calc.resumen_presupuestos(mes)

    def guardar_presupuesto(self, mes: str, categoria_id: str, monto: str) -> int:
        return db.guardar_presupuesto(mes, int(categoria_id), parsear_dinero(monto))

    def crear_respaldo(self) -> Path | None:
        return db.backup_db()
