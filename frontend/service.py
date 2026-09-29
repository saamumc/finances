"""Capa de servicio entre app.py (Tkinter) y los módulos de dominio.

app.py solo conoce esta interfaz: recibe strings crudos de los widgets,
aquí se normalizan (montos, personas, responsabilidad, método de pago) y se
delega en database.py / calculations.py / financial_engine.py /
financial_advisor.py, que son la única fuente de verdad financiera.
Este archivo NO implementa reglas financieras nuevas; solo traduce.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import calculations as calc
import database as db
import financial_advisor as advisor
import financial_engine as engine
from constants import PERSONA1, PERSONA2

NOMBRES = {PERSONA1: "Samuel", PERSONA2: "Sara"}
_PERSONA_POR_NOMBRE = {"Samuel": PERSONA1, "Sara": PERSONA2, PERSONA1: PERSONA1, PERSONA2: PERSONA2}
_METODO_POR_ETIQUETA = {"Efectivo": "efectivo", "Débito": "debito", "Tarjeta": "tarjeta",
                        "efectivo": "efectivo", "debito": "debito", "tarjeta": "tarjeta"}
_PRIORIDAD_POR_ETIQUETA = {"Obligatorio": "obligatorio", "Discrecional": "discrecional",
                          "obligatorio": "obligatorio", "discrecional": "discrecional"}
_FRECUENCIAS = {"Mensual": "mensual", "Bimestral": "bimestral", "Trimestral": "trimestral",
               "Semestral": "semestral", "Anual": "anual"}

PREFS_PATH = db.DB_PATH.parent / "preferencias.json"


def mes_actual() -> str:
    return dt.date.today().strftime("%Y-%m")


def dinero(valor: Any) -> str:
    """Formatea un entero COP; tolera negativos (a diferencia de calc.fmt_cop)."""
    try:
        monto = int(valor)
    except (TypeError, ValueError):
        monto = 0
    signo = "-" if monto < 0 else ""
    return signo + f"${abs(monto):,}".replace(",", ".")


def parsear_dinero(valor: Any) -> int:
    """Convierte texto de un campo COP (con $, puntos, comas) a entero de pesos."""
    if valor is None:
        return 0
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(round(valor))
    texto = str(valor).strip()
    if not texto:
        return 0
    negativo = texto.startswith("-")
    digitos = "".join(ch for ch in texto if ch.isdigit())
    if not digitos:
        return 0
    numero = int(digitos)
    return -numero if negativo else numero


def cargar_preferencias() -> dict[str, Any]:
    try:
        return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def guardar_preferencias(pref: dict[str, Any]) -> None:
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(pref, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # Las preferencias son cosméticas; nunca deben romper la app.


def _persona(valor: str) -> str:
    resultado = _PERSONA_POR_NOMBRE.get(str(valor).strip())
    if resultado is None:
        raise ValueError(f"Persona no reconocida: {valor!r}")
    return resultado


def _responsabilidad(valor: str) -> str:
    texto = str(valor).strip()
    if texto == "Samuel":
        return PERSONA1
    if texto == "Sara":
        return PERSONA2
    # "Compartido", "Personalizado" y "Porcentaje personalizado" ya se
    # tradujeron a montos p1/p2 concretos en la UI: el modelo de datos solo
    # distingue individual vs. compartido.
    return "compartido"


def _metodo(valor: str) -> str:
    return _METODO_POR_ETIQUETA.get(str(valor).strip(), "efectivo")


def _prioridad(valor: str) -> str:
    return _PRIORIDAD_POR_ETIQUETA.get(str(valor).strip(), "obligatorio")


def _id_opcional(valor: Any) -> int | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto.lower() == "none":
        return None
    try:
        return int(texto)
    except ValueError:
        return None


class FinanceService:
    """Fachada única que usa app.py; nunca ejecuta SQL directamente."""

    def __init__(self) -> None:
        # Nada más en la app llama a init_db(): sin esto, una base existente
        # se queda congelada en su SCHEMA_VERSION anterior y las tablas o
        # columnas nuevas (como gastos_fijos) nunca se crean.
        db.init_db()

    # ------------------------------------------------------------------
    # Panorama general
    # ------------------------------------------------------------------
    def dashboard(self, mes: str) -> dict[str, Any]:
        ahorros = calc.resumen_ahorros()
        return {
            "flujo": calc.flujo_caja_mes(mes),
            "liquidez": calc.liquidez_por_persona(mes),
            "patrimonio": calc.patrimonio_liquido(mes),
            "cajitas": {"total": ahorros["total"], "fondos": ahorros["fondos"]},
            "tarjetas": calc.resumen_tarjetas(),
        }

    def resumen_tarjetas(self, mes: str) -> dict[str, Any]:
        tarjetas = calc.resumen_tarjetas()
        deuda_total = sum(t["saldo_deuda"] for t in tarjetas)
        cupo_total = sum(t["cupo_total"] for t in tarjetas)
        return {
            "tarjetas": tarjetas, "deuda_total": deuda_total, "cupo_total": cupo_total,
            "cupo_disponible": sum(t["cupo_disponible"] for t in tarjetas),
            "utilizacion_global": (deuda_total / cupo_total) if cupo_total else 0.0,
            "pagos_realizados": sum(t["total_pagado"] for t in tarjetas),
            "intereses_registrados": sum(t["intereses_registrados"] for t in tarjetas),
            "deuda_persona1": sum(t["deuda_persona1"] for t in tarjetas),
            "deuda_persona2": sum(t["deuda_persona2"] for t in tarjetas),
            "pago_minimo_total": sum(min(t["pago_minimo"], t["saldo_deuda"]) for t in tarjetas),
        }

    def presupuesto_seguro(self, mes: str, margen_porcentaje: int = 10) -> dict[str, Any]:
        return advisor.presupuesto_seguro(mes, margen_porcentaje)

    def asesor(self, mes: str) -> dict[str, Any]:
        return advisor.analizar_finanzas(mes)

    def tarjetas(self) -> list[dict[str, Any]]:
        return calc.resumen_tarjetas()

    def evaluar_gasto(self, mes: str, monto: Any, metodo: str, tarjeta_id: Any, margen_porcentaje: int = 10) -> dict[str, Any]:
        return advisor.evaluar_gasto(mes, parsear_dinero(monto), metodo=_metodo(metodo),
                                     tarjeta_id=_id_opcional(tarjeta_id), margen_porcentaje=int(margen_porcentaje))

    # ------------------------------------------------------------------
    # Movimientos: ingresos y gastos
    # ------------------------------------------------------------------
    def crear_ingreso(self, mes: str, pagador: str, nombre: str, monto: Any) -> int:
        return db.registrar_ingreso(mes, _persona(pagador), nombre, parsear_dinero(monto))

    def crear_gasto(self, datos: dict[str, Any]) -> int:
        tarjeta_id = _id_opcional(datos.get("tarjeta_id"))
        cuotas = int(parsear_dinero(datos.get("cuotas") or 1)) or 1
        return db.registrar_gasto(
            datos["mes"], datos["nombre"], datos["categoria"], parsear_dinero(datos["valor"]),
            datos.get("fecha") or None, _metodo(datos["metodo"]), _persona(datos["pagador"]),
            _responsabilidad(datos["responsabilidad"]), parsear_dinero(datos["monto_p1"]),
            parsear_dinero(datos["monto_p2"]), tarjeta_id=tarjeta_id, cuotas=cuotas,
            prioridad=_prioridad(datos.get("prioridad", "Obligatorio")),
        )

    def editar_gasto(self, gasto_id: int, datos: dict[str, Any]) -> None:
        db.actualizar_gasto(
            gasto_id, datos["mes"], datos["nombre"], datos["categoria"], parsear_dinero(datos["valor"]),
            datos.get("fecha") or None, _persona(datos["pagador"]), _responsabilidad(datos["responsabilidad"]),
            parsear_dinero(datos["monto_p1"]), parsear_dinero(datos["monto_p2"]),
            prioridad=_prioridad(datos.get("prioridad", "Obligatorio")),
        )

    def gasto(self, gasto_id: int) -> dict[str, Any]:
        return db.get_gasto(gasto_id)

    def movimientos(self, mes: str, incluir_reversados: bool = False) -> list[dict[str, Any]]:
        filas: list[dict[str, Any]] = []
        for i in db.get_ingresos(mes, incluir_reversados=incluir_reversados):
            filas.append({"id": i["id"], "tipo": "ingreso", "descripcion": i["concepto"],
                         "persona": NOMBRES[i["persona"]], "fecha": mes, "monto": i["valor"], "estado": i["estado"]})
        etiqueta_responsabilidad = {PERSONA1: NOMBRES[PERSONA1], PERSONA2: NOMBRES[PERSONA2],
                                    "compartido": "Compartido"}
        etiqueta_metodo = {"efectivo": "Efectivo", "debito": "Débito", "tarjeta": "Tarjeta"}
        nombres_tarjeta = {t["id"]: t["nombre"] for t in db.get_tarjetas()}
        for g in db.get_gastos(mes, incluir_reversados=incluir_reversados):
            # La UI necesita distinguir quién pagó de quién asume el gasto: son
            # dos dimensiones distintas y no se deducen la una de la otra.
            filas.append({"id": g["id"], "tipo": "gasto", "descripcion": g["nombre"],
                         "persona": NOMBRES[g["pagador"]], "fecha": g.get("fecha") or mes,
                         "monto": g["valor"], "estado": g["estado"], "tarjeta_id": g.get("tarjeta_id"),
                         "categoria": g["categoria"],
                         "metodo": etiqueta_metodo.get(g["metodo_pago"], g["metodo_pago"]),
                         "responsabilidad": etiqueta_responsabilidad.get(g["responsabilidad"], g["responsabilidad"]),
                         "monto_p1": g["monto_p1"], "monto_p2": g["monto_p2"],
                         "prioridad": g["prioridad"],
                         "tarjeta": nombres_tarjeta.get(g.get("tarjeta_id"))})
        for p in db.get_pagos_deuda(mes, incluir_reversados=incluir_reversados):
            filas.append({"id": p["id"], "tipo": "pago", "descripcion": p.get("concepto") or "Pago de tarjeta",
                         "persona": NOMBRES[p["pagador"]], "fecha": p["fecha"], "monto": p["monto"],
                         "estado": p["estado"], "tarjeta_id": p["tarjeta_id"],
                         "tarjeta": nombres_tarjeta.get(p["tarjeta_id"]),
                         "responsabilidad": f"Aportes: {NOMBRES[PERSONA1]} {p['monto_aportado_p1']:,} / "
                                            f"{NOMBRES[PERSONA2]} {p['monto_aportado_p2']:,}".replace(",", "."),
                         "categoria": "Pago de tarjeta", "metodo": "Débito"})
        for c in db.get_compras_tarjeta(incluir_reversadas=incluir_reversados):
            if c.get("mes") != mes or c.get("tipo", "COMPRA") == "COMPRA":
                continue
            persona = PERSONA1 if c["monto_p1"] >= c["monto_p2"] else PERSONA2
            filas.append({"id": c["id"], "tipo": "interes" if c["tipo"] == "INTERES" else "cargo",
                         "descripcion": c["descripcion"], "persona": NOMBRES[persona], "fecha": c.get("fecha") or mes,
                         "monto": c["valor_original"], "estado": c["estado"], "tarjeta_id": c["tarjeta_id"]})
        for l in db.get_liquidaciones(mes, incluir_reversados=incluir_reversados):
            filas.append({"id": l["id"], "tipo": "liquidacion", "descripcion": l.get("concepto") or "Liquidación",
                         "persona": NOMBRES[l["deudor"]], "fecha": l["fecha"], "monto": l["monto"], "estado": l["estado"]})
        for a in db.get_movimientos_ahorro(mes=mes, incluir_reversados=incluir_reversados):
            aportante = a.get("aportante") or PERSONA1
            filas.append({"id": a["id"], "tipo": "ahorro", "descripcion": a["concepto"],
                         "persona": NOMBRES.get(aportante, NOMBRES[PERSONA1]), "fecha": a["fecha"],
                         "monto": a["monto"], "estado": a["estado"]})
        filas.sort(key=lambda fila: (fila["fecha"] or "", fila["id"]), reverse=True)
        return filas

    def proponer_anulacion(self, dominio: str, movimiento_id: int) -> dict[str, Any]:
        return db.proponer_anular_movimiento(dominio, movimiento_id)

    def revisar_anulacion_tarjeta(self, tarjeta_id: int, movimiento_id: int, tipo: str) -> dict[str, Any]:
        return db.revisar_transaccion(tarjeta_id, movimiento_id, tipo)

    def reversar(self, dominio: str, movimiento_id: int, motivo: str) -> None:
        funciones = {
            "ingreso": db.reversar_ingreso, "gasto": db.reversar_gasto,
            "pago": db.reversar_pago_tarjeta, "liquidacion": db.reversar_liquidacion,
            "ahorro": db.reversar_movimiento_ahorro, "movimiento_tarjeta": db.reversar_movimiento_tarjeta,
        }
        if dominio not in funciones:
            raise ValueError(f"No se puede anular un movimiento de tipo {dominio!r}.")
        funciones[dominio](movimiento_id, motivo)

    # ------------------------------------------------------------------
    # Tarjetas
    # ------------------------------------------------------------------
    def crear_tarjeta(self, nombre: str, propietario: str, cupo: Any, minimo: Any, interes: Any,
                      historico: Any, fecha_historico: str | None, *, banco: str | None = None,
                      tipo: str | None = None, ultimos_4: str | None = None, fecha_corte: str | None = None,
                      fecha_pago: str | None = None, notas: str | None = None) -> int:
        return db.registrar_tarjeta(
            nombre, _persona(propietario), parsear_dinero(cupo), pago_minimo=parsear_dinero(minimo),
            interes_mensual=float(str(interes).replace(",", ".") or 0), saldo_inicial_historico=parsear_dinero(historico),
            fecha_saldo_inicial=fecha_historico or None, banco=banco or None, tipo=tipo or None,
            ultimos_4=ultimos_4 or None, fecha_corte=fecha_corte or None, fecha_pago=fecha_pago or None,
            notas=notas or None,
        )

    def editar_tarjeta(self, tarjeta_id: int, datos: dict[str, Any]) -> None:
        db.actualizar_tarjeta(
            tarjeta_id, nombre=datos["nombre"], propietario=_persona(datos["propietario"]),
            cupo_total=parsear_dinero(datos["cupo"]), pago_minimo=parsear_dinero(datos["minimo"]),
            interes_mensual=float(str(datos["interes"]).replace(",", ".") or 0),
            activa=(str(datos["activa"]).strip() == "Activa"), banco=datos.get("banco") or None,
            tipo=datos.get("tipo") or None, ultimos_4=datos.get("ultimos_4") or None,
            fecha_corte=datos.get("fecha_corte") or None, fecha_pago=datos.get("fecha_pago") or None,
            notas=datos.get("notas") or None,
        )

    def tarjeta_detalle(self, tarjeta_id: int) -> dict[str, Any]:
        tarjeta = next((t for t in calc.resumen_tarjetas() if t["id"] == tarjeta_id), None)
        if tarjeta is None:
            raise ValueError("Tarjeta no encontrada.")
        mensual = calc.resumen_mensual_tarjeta(tarjeta_id, mes_actual())
        historial = calc.historial_tarjeta(tarjeta_id)
        return {"tarjeta": tarjeta, "mensual": mensual, "historial": historial}

    def simular_pago_tarjeta(self, tarjeta_id: int, extra: Any) -> dict[str, Any]:
        """Simulación pura de un pago adicional; no toca SQLite."""
        tarjeta = next((t for t in calc.resumen_tarjetas() if t["id"] == int(tarjeta_id)), None)
        if tarjeta is None:
            raise ValueError("Tarjeta no encontrada.")
        extra_valor = parsear_dinero(extra)
        if extra_valor < 0:
            raise ValueError("El pago adicional no puede ser negativo.")
        deuda_actual, cupo = tarjeta["saldo_deuda"], tarjeta["cupo_total"]
        deuda_despues = max(deuda_actual - extra_valor, 0)
        cupo_liberado = min(extra_valor, deuda_actual)
        nota = "Simulación pura: no registra el pago ni modifica la tarjeta."
        if extra_valor > deuda_actual:
            nota += f" El excedente de {dinero(extra_valor - deuda_actual)} no tendría deuda a la cual aplicarse."
        return {
            "deuda_actual": deuda_actual, "deuda_despues": deuda_despues,
            "utilizacion_actual": (deuda_actual / cupo) if cupo else 0.0,
            "utilizacion_despues": (deuda_despues / cupo) if cupo else 0.0,
            "cupo_liberado": cupo_liberado, "nota": nota,
        }

    def crear_movimiento_tarjeta(self, datos: dict[str, Any]) -> int:
        return db.registrar_movimiento_tarjeta(
            datos["mes"], datos["fecha"], int(datos["tarjeta_id"]), datos["tipo"], parsear_dinero(datos["monto"]),
            datos["descripcion"], _responsabilidad(datos["responsabilidad"]), parsear_dinero(datos["monto_p1"]),
            parsear_dinero(datos["monto_p2"]), periodo=datos.get("periodo") or None,
        )

    def crear_pago_tarjeta(self, datos: dict[str, Any]) -> int:
        return db.registrar_pago_tarjeta(
            datos["mes"], datos["fecha"], int(datos["tarjeta_id"]), parsear_dinero(datos["monto"]),
            _persona(datos["pagador"]), parsear_dinero(datos["aporte_p1"]), parsear_dinero(datos["aporte_p2"]),
            concepto=datos.get("concepto") or None,
        )

    def ajustar_saldo_tarjeta(self, tarjeta_id: int, nuevo_saldo: Any, motivo: str, fecha: str, persona: str) -> int:
        return db.ajustar_saldo_tarjeta(int(tarjeta_id), parsear_dinero(nuevo_saldo), motivo, fecha, _persona(persona))

    # ------------------------------------------------------------------
    # Vista personal
    # ------------------------------------------------------------------
    def dashboard_personal(self, persona: str, mes: str) -> dict[str, Any]:
        return calc.dashboard_personal(persona, mes)

    def reconciliacion_personal(self, persona: str, mes: str) -> dict[str, Any]:
        return calc.reconciliar_saldo_personal(persona, mes)

    # ------------------------------------------------------------------
    # Cajitas / metas
    # ------------------------------------------------------------------
    def crear_cajita(self, datos: dict[str, Any]) -> int:
        titular = datos.get("titular") or PERSONA1
        propietario = titular if titular in (PERSONA1, PERSONA2) else PERSONA1
        return db.crear_ahorro(datos["nombre"], propietario, datos.get("descripcion") or None,
                               parsear_dinero(datos.get("meta", 0)), titular=titular,
                               icono=datos.get("icono") or "💰")

    def movimiento_ahorro(self, datos: dict[str, Any]) -> int:
        tipo = "DEPOSITO" if str(datos["tipo"]).strip().lower().startswith("dep") else "RETIRO"
        return db.registrar_movimiento_ahorro(
            datos["mes"], datos["fecha"], int(datos["ahorro_id"]), tipo, parsear_dinero(datos["monto"]),
            datos.get("concepto") or "Movimiento de cajita", aportante=_persona(datos.get("aportante", PERSONA1)),
        )

    # ------------------------------------------------------------------
    # Asesor / simulador
    # ------------------------------------------------------------------
    def responder_compra_inteligente(self, mes: str, monto: Any, concepto: str, *, urgencia: str,
                                     pagador: str, responsabilidad: str, tarjeta_id: Any = None) -> dict[str, Any]:
        return advisor.responder_compra_inteligente(
            parsear_dinero(monto), concepto, urgencia, mes=mes, persona_pregunta=_persona(pagador),
            responsabilidad=_responsabilidad(responsabilidad), usar_tarjeta=_id_opcional(tarjeta_id),
        )

    def simular_escenario(self, mes: str, tipo: str, monto: Any, tarjeta_id: Any = None) -> dict[str, Any]:
        return advisor.simular_escenario(mes, tipo, parsear_dinero(monto), tarjeta_id=_id_opcional(tarjeta_id))

    def proyeccion_deuda(self, tarjeta_id: int, extra: Any) -> dict[str, Any]:
        return advisor.proyectar_deuda(int(tarjeta_id), parsear_dinero(extra))

    def preguntar_asesor(self, mes: str, pregunta: str, monto: Any = None) -> dict[str, Any]:
        monto_val = parsear_dinero(monto) if monto not in (None, "") else None
        return advisor.responder_pregunta(mes, pregunta, monto=monto_val)

    # ------------------------------------------------------------------
    # Asesor inteligente (análisis, detección y simulación explicable)
    # ------------------------------------------------------------------
    def perfil_financiero(self, mes: str) -> dict[str, Any]:
        return advisor.perfil_financiero(mes)

    def estado_financiero(self, mes: str) -> dict[str, Any]:
        return advisor.estado_financiero(mes)

    def hallazgos_asesor(self, mes: str) -> list[dict[str, Any]]:
        return advisor.hallazgos(mes)

    def dimensiones_salud(self, mes: str) -> dict[str, Any]:
        return advisor.dimensiones_salud(mes)

    def anomalias(self, mes: str) -> list[dict[str, Any]]:
        return advisor.anomalias(mes)

    def resumen_ejecutivo(self, mes: str) -> str:
        return advisor.resumen_ejecutivo(mes)

    def explicar_finanzas(self, mes: str) -> dict[str, Any]:
        return advisor.explicar_finanzas_simple(mes)

    def detectar_gastos_fijos(self, mes: str | None = None, incluir_registrados: bool = False) -> dict[str, Any]:
        return advisor.detectar_gastos_fijos(mes, incluir_registrados=bool(incluir_registrados))

    def podria_ser_gasto_fijo(self, descripcion: str, monto: Any = None, mes: str | None = None) -> dict[str, Any]:
        monto_val = parsear_dinero(monto) if monto not in (None, "") else None
        return advisor.podria_ser_gasto_fijo(descripcion, monto=monto_val, mes=mes)

    def capacidad_discrecional(self, mes: str, margen_porcentaje: Any = None) -> dict[str, Any]:
        margen = int(margen_porcentaje) if margen_porcentaje not in (None, "") else None
        return advisor.capacidad_discrecional(mes, margen_porcentaje=margen)

    def evaluar_asequibilidad(self, mes: str, monto: Any = None, concepto: str = "esta salida") -> dict[str, Any]:
        monto_val = parsear_dinero(monto) if monto not in (None, "") else None
        return advisor.evaluar_asequibilidad(mes, monto_val, concepto=concepto)

    def opciones_de_pago(self, mes: str, monto: Any, concepto: str = "esta compra") -> dict[str, Any]:
        return advisor.opciones_de_pago(mes, parsear_dinero(monto), concepto=concepto)

    def escenario_detallado(self, mes: str, tipo: str, monto: Any, tarjeta_id: Any = None) -> dict[str, Any]:
        return advisor.simular_escenario_detallado(mes, tipo, parsear_dinero(monto),
                                                   tarjeta_id=_id_opcional(tarjeta_id))

    def comparar_meses(self, mes: str, referencia: str | None = None) -> dict[str, Any]:
        return advisor.comparar_meses(mes, referencia)

    def tendencias(self, mes: str, meses: int = 6) -> dict[str, Any]:
        return advisor.tendencias(mes, int(meses))

    def analisis_pareja(self, mes: str) -> dict[str, Any]:
        return advisor.analisis_pareja(mes)

    def comparar_destinos(self, mes: str, destinos: list[dict[str, Any]] | None = None,
                          meses_para_viajar: Any = None) -> dict[str, Any]:
        plazo = int(meses_para_viajar) if meses_para_viajar not in (None, "") else None
        return advisor.comparar_destinos(mes, destinos or [], meses_para_viajar=plazo)

    def plantilla_viaje(self) -> dict[str, Any]:
        return advisor.plantilla_viaje()

    def informe_integridad(self, mes: str | None = None) -> dict[str, Any]:
        return advisor.informe_integridad(mes)

    def plan_de_accion(self, mes: str) -> dict[str, Any]:
        return advisor.plan_de_accion(mes)

    def riesgo_financiero(self, mes: str) -> dict[str, Any]:
        return advisor.riesgo_financiero(mes)

    def oportunidades(self, mes: str) -> list[dict[str, Any]]:
        return advisor.oportunidades(mes)

    def hallazgos_agrupados(self, mes: str) -> list[dict[str, Any]]:
        return advisor.hallazgos_agrupados(mes)

    def cotejar_gastos_fijos(self, mes: str | None = None) -> dict[str, Any]:
        return advisor.cotejar_gastos_fijos(mes)

    def inteligencia_tarjetas(self, mes: str) -> dict[str, Any]:
        return advisor.inteligencia_tarjetas(mes)

    def comparar_tarjetas(self, mes: str) -> dict[str, Any]:
        return advisor.comparar_tarjetas(mes)

    def inteligencia_ahorro(self, mes: str) -> dict[str, Any]:
        return advisor.inteligencia_ahorro(mes)

    def explicacion_estructurada(self, mes: str) -> dict[str, Any]:
        return advisor.explicacion_estructurada(mes)

    def nueva_sesion_asesor(self, mes: str) -> Any:
        """Crea una sesión conversacional; la UI la conserva mientras dure el diálogo."""
        return advisor.nueva_sesion(mes)

    def conversar(self, mes: str, pregunta: str, monto: Any = None, sesion: Any = None) -> dict[str, Any]:
        monto_val = parsear_dinero(monto) if monto not in (None, "") else None
        return advisor.conversar(mes, pregunta, monto=monto_val, sesion=sesion)

    def refrescar_asesor(self, mes: str | None = None) -> None:
        """Invalida la foto cacheada del asesor tras registrar movimientos."""
        advisor.invalidar_contexto(mes)

    # ------------------------------------------------------------------
    # Gastos fijos
    # ------------------------------------------------------------------
    def gastos_fijos(self, mes: str | None = None) -> dict[str, Any]:
        return calc.resumen_gastos_fijos(mes)

    def crear_gasto_fijo(self, datos: dict[str, Any]) -> int:
        tarjeta_id = _id_opcional(datos.get("tarjeta_id"))
        return db.registrar_gasto_fijo(
            datos["nombre"], datos["categoria"], parsear_dinero(datos["valor"]),
            _FRECUENCIAS.get(datos.get("frecuencia", "Mensual"), "mensual"), _persona(datos["propietario"]),
            _responsabilidad(datos["responsabilidad"]), parsear_dinero(datos["monto_p1"]),
            parsear_dinero(datos["monto_p2"]), dia_pago=_id_opcional(datos.get("dia_pago")),
            metodo_pago=_metodo(datos.get("metodo_pago", "Débito")), tarjeta_id=tarjeta_id,
            notas=datos.get("notas") or None, activo=str(datos.get("activo", "Activo")).strip() != "Inactivo",
        )

    def editar_gasto_fijo(self, gasto_fijo_id: int, datos: dict[str, Any]) -> None:
        tarjeta_id = _id_opcional(datos.get("tarjeta_id"))
        db.actualizar_gasto_fijo(
            gasto_fijo_id, nombre=datos["nombre"], categoria=datos["categoria"],
            valor=parsear_dinero(datos["valor"]), frecuencia=_FRECUENCIAS.get(datos.get("frecuencia", "Mensual"), "mensual"),
            propietario=_persona(datos["propietario"]), responsabilidad=_responsabilidad(datos["responsabilidad"]),
            monto_p1=parsear_dinero(datos["monto_p1"]), monto_p2=parsear_dinero(datos["monto_p2"]),
            dia_pago=_id_opcional(datos.get("dia_pago")), metodo_pago=_metodo(datos.get("metodo_pago", "Débito")),
            tarjeta_id=tarjeta_id, activo=str(datos.get("activo", "Activo")).strip() != "Inactivo",
            notas=datos.get("notas") or None,
        )

    def eliminar_gasto_fijo(self, gasto_fijo_id: int) -> None:
        db.eliminar_gasto_fijo(gasto_fijo_id)

    def gasto_fijo(self, gasto_fijo_id: int) -> dict[str, Any]:
        return db.get_gasto_fijo(gasto_fijo_id)

    # ------------------------------------------------------------------
    # Terceros
    # ------------------------------------------------------------------
    def terceros(self) -> dict[str, Any]:
        resumen = calc.resumen_deudas_terceros()
        return {"por_cobrar": resumen["por_cobrar"], "por_pagar": resumen["por_pagar"],
               "registrados": db.get_terceros(), "detalle": resumen["detalle"]}

    def crear_tercero(self, nombre: str, tipo: str, contacto: str) -> int:
        return db.crear_tercero(nombre, tipo, contacto)

    def crear_prestamo_tercero(self, datos: dict[str, Any]) -> int:
        return db.registrar_prestamo_tercero(datos["mes"], datos["fecha"], datos["tercero"], datos["tipo"],
                                             parsear_dinero(datos["monto"]), datos["propietario"], datos["concepto"])

    def crear_abono_tercero(self, datos: dict[str, Any]) -> int:
        return db.registrar_abono_tercero(datos["mes"], datos["fecha"], int(datos["prestamo_id"]),
                                          parsear_dinero(datos["monto"]))

    # ------------------------------------------------------------------
    # Balance de pareja
    # ------------------------------------------------------------------
    def estado_actual(self, mes: str) -> dict[str, Any]:
        return {"liquidity": {"by_person": calc.liquidez_por_persona(mes)}}

    def explicacion(self, persona: str) -> dict[str, Any]:
        return calc.explicar_balance(persona)

    def crear_liquidacion(self, datos: dict[str, Any]) -> int:
        return db.registrar_liquidacion(datos["mes"], datos["fecha"], datos["deudor"], datos["acreedor"],
                                        parsear_dinero(datos["monto"]), concepto=datos.get("concepto"))

    # ------------------------------------------------------------------
    # Diagnóstico e Integridad de BD
    # ------------------------------------------------------------------
    def diagnostico_completo(self, mes: str | None = None) -> dict[str, Any]:
        """Retorna diagnóstico de la salud financiera y la BD."""
        if mes is None:
            mes = mes_actual()
        
        try:
            bd_check = db.verificar_bd_integridad()
            
            return {
                "mes": mes,
                "bd": {
                    "integridad": bd_check.get("integridad"),
                    "schema": bd_check.get("schema_version"),
                    "ok": bd_check.get("ok"),
                },
                "tarjetas_activas": len(self.tarjetas()),
                "anomalias": len(self.anomalias(mes)),
                "hallazgos": len(self.hallazgos_asesor(mes)),
                "timestamp": dt.datetime.now().isoformat(),
            }
        except Exception as e:
            return {
                "error": str(e),
                "timestamp": dt.datetime.now().isoformat(),
            }

    def refrescar_y_verificar(self) -> dict[str, Any]:
        """Recalcula el asesor y verifica la integridad."""
        mes = mes_actual()
        try:
            self.refrescar_asesor(mes)
            diag = self.diagnostico_completo(mes)
            return {"ok": True, "diagnostico": diag}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _invalidar_cache_tras_escribir(cls: type) -> type:
    """Envuelve los métodos de escritura para refrescar la foto del asesor.

    El asesor cachea su contexto durante unos segundos para no recorrer el
    historial en cada pregunta. Si la interfaz registra un movimiento en ese
    lapso, la foto quedaría vieja: aquí se invalida automáticamente, sin que
    app.py tenga que acordarse de hacerlo.
    """
    prefijos = ("crear_", "editar_", "eliminar_", "reversar_", "ajustar_", "guardar_", "registrar_")
    for nombre in dir(cls):
        if not nombre.startswith(prefijos):
            continue
        original = getattr(cls, nombre)
        if not callable(original) or getattr(original, "_invalida_cache", False):
            continue

        def envolver(metodo):
            def envuelto(self, *args, **kwargs):
                resultado = metodo(self, *args, **kwargs)
                advisor.invalidar_contexto()
                return resultado
            envuelto.__name__ = metodo.__name__
            envuelto.__doc__ = metodo.__doc__
            envuelto._invalida_cache = True
            return envuelto

        setattr(cls, nombre, envolver(original))
    return cls


FinanceService = _invalidar_cache_tras_escribir(FinanceService)